"""Artifact delete / archive / share against the real Postgres (skipped if down)."""

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from odn.config import settings
from odn.db import tenant_transaction
from odn.platform.sharing import service as sharing
from odn.platform.workspace import manage
from odn.platform.workspace import saved_queries as sq
from odn.platform.workspace.service import ChartResult, ChartSpec, save_chart
from odn.platform.workspace.store import page_store
from odn.tenancy.context import RequestContext
from tests.db.test_rls import APP_URL, OWNER_URL, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")


@pytest.fixture
def users(monkeypatch: pytest.MonkeyPatch):
    from odn.db import pool
    from odn.db.migrate import migrate

    migrate(OWNER_URL)
    settings.database_url = APP_URL
    pool.cache_clear()
    monkeypatch.setattr(manage, "_delete_object", lambda tenant, key: None)  # no object storage in tests
    t, other_t = uuid.uuid4(), uuid.uuid4()
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(OWNER_URL) as conn:
        for tid in (t, other_t):
            conn.execute("INSERT INTO tenants (id, slug, name) VALUES (%s, %s, 'x')", (tid, f"t-{tid.hex[:12]}"))
        for u, tid in ((a, t), (b, t), (c, other_t)):
            conn.execute("INSERT INTO users (id, tenant_id, subject, email) VALUES (%s, %s, %s, %s)",
                         (u, tid, u.hex, f"{u.hex[:8]}@example.com"))
    yield {n: RequestContext(tenant_id=tid, user_id=u, subject=n, role="member")
           for n, tid, u in (("alice", t, a), ("bob", t, b), ("carol", other_t, c))}
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = ANY(%s)", ([t, other_t],))


def _chart(title: str) -> ChartResult:
    return ChartResult(
        spec=ChartSpec(source="q1", type="bar", x="state", y="n", title=title), sql="SELECT 1",
        points=[{"x": "CA", "y": 3}], truncated=False, source_tables=["p.d.t"], source_truncated=False,
    )


def _chart_id(ctx: RequestContext, name: str) -> uuid.UUID:
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        return conn.execute("SELECT id FROM workspace_charts WHERE name = %s AND user_id = %s",
                            (name, ctx.user_id)).fetchone()["id"]


def test_archive_hides_restores_and_only_the_owner_can_act(users) -> None:
    alice, bob = users["alice"], users["bob"]
    q = sq.create_query(alice, name="revenue", sql="SELECT 1", dialect="bigquery", connection_id=None)
    save_chart(alice, "s1", _chart("By state"), None)
    chart = _chart_id(alice, "c1")

    assert manage.archive(alice, "sql", q.id, "forever") is None
    purge_at = manage.archive(alice, "chart", chart, "30d")
    assert purge_at is not None and purge_at > datetime.now(UTC) + timedelta(days=29)
    assert page_store(alice).items == [] and page_store(alice, q="revenue").items == []  # hidden, not searchable
    assert sq.list_queries(alice) == []  # nor in the agent's / Explorer's saved SQL
    archived = page_store(alice, archived=True)
    assert {(i.kind, i.purge_at is None) for i in archived.items} == {("sql", True), ("chart", False)}

    for action in (lambda: manage.restore(bob, "sql", q.id), lambda: manage.delete(bob, "chart", chart),
                   lambda: manage.archive(users["carol"], "sql", q.id, "30d")):
        with pytest.raises(KeyError):
            action()
    manage.restore(alice, "sql", q.id)
    assert [i.kind for i in page_store(alice).items] == ["sql"]


def test_deleted_names_are_not_reused_in_the_conversation(users) -> None:
    alice = users["alice"]
    save_chart(alice, "s1", _chart("one"), None)
    assert save_chart(alice, "s1", _chart("two"), None) == "c2"
    manage.delete(alice, "chart", _chart_id(alice, "c2"))
    assert save_chart(alice, "s1", _chart("three"), None) == "c3"  # not c2 again
    assert save_chart(alice, "s2", _chart("other chat"), None) == "c1"


def test_purge_deletes_only_archived_items_whose_time_is_up(users) -> None:
    alice = users["alice"]
    due = sq.create_query(alice, name="due", sql="SELECT 1", dialect="bigquery", connection_id=None)
    kept = sq.create_query(alice, name="kept", sql="SELECT 1", dialect="bigquery", connection_id=None)
    later = sq.create_query(alice, name="later", sql="SELECT 1", dialect="bigquery", connection_id=None)
    manage.archive(alice, "sql", due.id, "30d")
    manage.archive(alice, "sql", kept.id, "forever")
    manage.archive(alice, "sql", later.id, "30d")
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE saved_queries SET purge_at = now() - interval '1 minute' WHERE id = %s", (due.id,))
    assert manage.purge_due() >= 1
    assert {i.name for i in page_store(alice, archived=True).items} == {"kept", "later"}


def test_sharing_one_artifact(users) -> None:
    alice, bob, carol = users["alice"], users["bob"], users["carol"]
    q = sq.create_query(alice, name="ca_revenue", sql="SELECT state, sum(x) FROM `p.d.t` GROUP BY 1",
                        dialect="bigquery", connection_id=None, description="Revenue per state")
    with pytest.raises(sharing.ShareError, match="sign in"):
        sharing.share_artifact(alice, kind="sql", item_id=q.id, title="", audience="public", recipient_ids=[],
                               expires_days=30)
    share = sharing.share_artifact(alice, kind="sql", item_id=q.id, title="", audience="organization",
                                   recipient_ids=[], expires_days=30)
    assert share["title"] == "ca_revenue.sql"
    seen = sharing.get(bob, share["id"])
    assert seen["kind"] == "artifact" and seen["artifacts"][0]["sql"].startswith("SELECT state")
    with pytest.raises(sharing.ShareError):
        sharing.get(carol, share["id"])  # another organization
    with pytest.raises(sharing.ShareError):
        sharing.continue_share(bob, "token", share["id"])
    copy = sharing.save_shared_sql(bob, share["id"])
    assert copy["name"] == "ca_revenue" and [x.name for x in sq.list_queries(bob)] == ["ca_revenue"]

    save_chart(alice, "s1", _chart("Revenue by state"), None)
    chart_share = sharing.share_artifact(alice, kind="chart", item_id=_chart_id(alice, "c1"), title="",
                                         audience="public", recipient_ids=[], expires_days=7)
    public = sharing.public_get(chart_share["id"])
    assert public["title"] == "Revenue by state" and public["artifacts"][0]["kind"] == "chart"
    manage.archive(alice, "sql", q.id, "30d")
    with pytest.raises(sharing.ShareError, match="not found"):  # archived items can't be shared
        sharing.share_artifact(alice, kind="sql", item_id=q.id, title="", audience="organization",
                               recipient_ids=[], expires_days=30)


def test_result_tables_are_readable_in_the_tenant_but_only_the_owner_can_change_them(users) -> None:
    alice, bob = users["alice"], users["bob"]
    with psycopg.connect(OWNER_URL) as conn:
        table_id = conn.execute(
            "INSERT INTO workspace_artifacts (tenant_id, user_id, session_id, name, kind, sql_text, object_key, "
            "row_count, arrow_bytes, truncated, columns) VALUES (%s, %s, 's1', 'q1', 'warehouse', 'SELECT 1', 'k', "
            "1, 1, false, '[]') RETURNING id", (alice.tenant_id, alice.user_id),
        ).fetchone()[0]
    with tenant_transaction(bob.tenant_id, bob.user_id) as conn:
        assert conn.execute("SELECT count(*) AS n FROM workspace_artifacts WHERE id = %s", (table_id,)).fetchone()["n"] == 1
        assert conn.execute("DELETE FROM workspace_artifacts WHERE id = %s", (table_id,)).rowcount == 0
        assert conn.execute("UPDATE workspace_artifacts SET archived_at = now() WHERE id = %s", (table_id,)).rowcount == 0
    manage.delete(alice, "table", table_id)
    assert page_store(alice).items == []
