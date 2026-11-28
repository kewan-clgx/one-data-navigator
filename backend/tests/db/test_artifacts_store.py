"""Artifacts store: saved charts and uploaded files are visible only to their owner (needs Postgres)."""

import uuid

import psycopg
import pytest

from odn.config import settings
from odn.db import tenant_transaction
from odn.platform.workspace import saved_queries as sq
from odn.platform.workspace.service import ChartResult, ChartSpec, InvalidSession, save_chart
from odn.platform.workspace.store import get_chart, list_store, page_store
from odn.tenancy.context import RequestContext
from tests.db.test_rls import APP_URL, OWNER_URL, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")


@pytest.fixture(scope="module")
def users():
    from odn.db import pool
    from odn.db.migrate import migrate

    migrate(OWNER_URL)
    settings.database_url = APP_URL
    pool.cache_clear()
    t, u1, u2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("INSERT INTO tenants (id, slug, name) VALUES (%s, %s, 'x')",
                     (t, f"t-{t.hex[:12]}"))
        for u in (u1, u2):
            conn.execute("INSERT INTO users (id, tenant_id, subject) VALUES (%s, %s, %s)", (u, t, u.hex))
    yield (
        RequestContext(tenant_id=t, user_id=u1, subject="a", role="member"),
        RequestContext(tenant_id=t, user_id=u2, subject="b", role="member"),
    )
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = %s", (t,))


def _chart(title: str) -> ChartResult:
    return ChartResult(
        spec=ChartSpec(source="q1", type="bar", x="state", y="n", title=title), sql="SELECT 1",
        points=[{"x": "CA", "y": 3}, {"x": "TX", "y": 5}], truncated=False, source_tables=["p.d.t"],
        source_truncated=False,
    )


def test_charts_are_numbered_per_conversation_and_private(users) -> None:
    alice, bob = users
    assert save_chart(alice, "s1", _chart("By state"), None) == "c1"
    assert save_chart(alice, "s1", _chart("Again"), None) == "c2"
    assert save_chart(alice, "s2", _chart("Other chat"), None) == "c1"

    mine = list_store(alice, kind="chart", session_id="s1")
    assert [(i.name, i.title, i.point_count) for i in mine] == [("c2", "Again", 2), ("c1", "By state", 2)]
    assert get_chart(alice, mine[0].id).name == "c2"

    assert list_store(bob, kind="chart") == []
    with pytest.raises(KeyError):
        get_chart(bob, mine[0].id)


def test_files_are_private_even_within_the_tenant(users) -> None:
    alice, bob = users
    with tenant_transaction(alice.tenant_id, alice.user_id) as conn:
        conn.execute(
            "INSERT INTO workspace_files (tenant_id, user_id, session_id, filename, mime_type, size_bytes, "
            "object_key) VALUES (%s, %s, 's1', 'map.png', 'image/png', 10, 'k')",
            (alice.tenant_id, alice.user_id),
        )
    files = list_store(alice, kind="file")
    assert [(f.name, f.inline) for f in files] == [("map.png", True)]
    assert list_store(bob, kind="file") == []
    # Writing a row for another user is refused by the owner-only policy.
    with pytest.raises(psycopg.errors.InsufficientPrivilege), tenant_transaction(bob.tenant_id, bob.user_id) as conn:
        conn.execute(
            "INSERT INTO workspace_files (tenant_id, user_id, session_id, filename, mime_type, size_bytes, "
            "object_key) VALUES (%s, %s, 's1', 'x.png', 'image/png', 1, 'k')",
            (alice.tenant_id, alice.user_id),
        )


def test_panel_pages_every_kind_with_search_counts_and_sorting(users) -> None:
    alice, bob = users
    carol = RequestContext(tenant_id=alice.tenant_id, user_id=uuid.uuid4(), subject="c", role="member")
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("INSERT INTO users (id, tenant_id, subject) VALUES (%s, %s, %s)",
                     (carol.user_id, carol.tenant_id, carol.user_id.hex))
    save_chart(carol, "s1", _chart("Revenue by state"), None)
    for i in range(23):
        sq.create_query(carol, name=f"query_{i:02d}", sql="\n".join(f"-- line {n}" for n in range(14)) + "\nSELECT 1",
                        dialect="bigquery", connection_id=None)
    with tenant_transaction(carol.tenant_id, carol.user_id) as conn:
        conn.execute(
            "INSERT INTO workspace_files (tenant_id, user_id, session_id, filename, mime_type, size_bytes, "
            "object_key) VALUES (%s, %s, 's1', 'zips.csv', 'text/csv', 10, 'k')", (carol.tenant_id, carol.user_id),
        )

    first = page_store(carol)
    assert len(first.items) == 20 and first.counts == {"chart": 1, "sql": 23, "file": 1}
    sql_item = next(i for i in first.items if i.kind == "sql")
    assert sql_item.sql_lines == 15 and sql_item.sql_head.count("\n") == 9  # 10-line preview
    rest = page_store(carol, cursor=first.next_cursor)
    assert rest.next_cursor is None and rest.counts is None
    ids = [i.id for i in first.items + rest.items]
    assert len(ids) == len(set(ids)) == 25  # every item exactly once

    by_name = page_store(carol, sort="name", limit=3)
    assert [i.name for i in by_name.items] == ["query_00", "query_01", "query_02"]
    assert [i.name for i in page_store(carol, sort="name", limit=3, cursor=by_name.next_cursor).items] == [
        "query_03", "query_04", "query_05"]
    assert [i.title for i in page_store(carol, q="revenue").items] == ["Revenue by state"]
    assert [i.kind for i in page_store(carol, kind="file").items] == ["file"]
    assert {i.kind for i in page_store(carol, session_id="s1").items} == {"chart", "file"}  # no saved SQL
    assert page_store(bob, q="query").items == []
    with pytest.raises(InvalidSession):
        page_store(carol, cursor="not-a-cursor")
