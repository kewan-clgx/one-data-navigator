"""Artifacts store: saved charts and uploaded files are visible only to their owner (needs Postgres)."""

import uuid

import psycopg
import pytest

from odn.config import settings
from odn.db import tenant_transaction
from odn.platform.workspace.service import ChartResult, ChartSpec, save_chart
from odn.platform.workspace.store import get_chart, list_store
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
        conn.execute("INSERT INTO tenants (id, slug, name, oidc_issuer) VALUES (%s, %s, 'x', 'iss')",
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
