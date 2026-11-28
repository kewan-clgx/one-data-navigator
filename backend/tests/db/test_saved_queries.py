"""Saved SQL against the real Postgres (skipped if down): owner-only, names, versions, search."""

import uuid

import psycopg
import pytest

from odn.config import settings
from odn.platform.workspace import saved_queries as sq
from odn.tenancy.context import RequestContext
from tests.db.test_rls import APP_URL, OWNER_URL, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")

SQL = "SELECT naics, COUNT(*) FROM `p.firmo.businesses` WHERE state = 'CA' GROUP BY 1"


@pytest.fixture
def users():
    from odn.db import pool
    from odn.db.migrate import migrate

    migrate(OWNER_URL)
    settings.database_url = APP_URL
    pool.cache_clear()
    ta, tb = uuid.uuid4(), uuid.uuid4()
    u1, u2, ub = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(OWNER_URL) as conn:
        for t in (ta, tb):
            conn.execute("INSERT INTO tenants (id, slug, name) VALUES (%s, %s, 'x')", (t, f"t-{t.hex[:12]}"))
        for u, t in ((u1, ta), (u2, ta), (ub, tb)):
            conn.execute("INSERT INTO users (id, tenant_id, subject, role) VALUES (%s, %s, %s, 'member')", (u, t, u.hex))
    yield {n: RequestContext(tenant_id=t, user_id=u, subject=u.hex, role="member")
           for n, t, u in (("u1", ta, u1), ("u2", ta, u2), ("other", tb, ub))}
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = ANY(%s)", ([ta, tb],))


def test_saved_sql_is_private_to_its_owner(users) -> None:
    saved = sq.create_query(users["u1"], name="CA businesses by NAICS.sql", sql=SQL, dialect="bigquery",
                            connection_id=None)
    assert saved.name == "ca_businesses_by_naics" and saved.tables == ["p.firmo.businesses"]
    assert [q.name for q in sq.list_queries(users["u1"])] == ["ca_businesses_by_naics"]
    for other in ("u2", "other"):
        assert sq.list_queries(users[other]) == []
        with pytest.raises(KeyError):
            sq.get_query(users[other], saved.id)
        with pytest.raises(KeyError):
            sq.delete_query(users[other], saved.id)


def test_name_clash_suggests_a_free_name(users) -> None:
    sq.create_query(users["u1"], name="revenue", sql=SQL, dialect="bigquery", connection_id=None)
    with pytest.raises(sq.NameTaken) as taken:
        sq.create_query(users["u1"], name="Revenue", sql=SQL, dialect="bigquery", connection_id=None)
    assert taken.value.suggestion == "revenue_2"
    sq.create_query(users["u2"], name="revenue", sql=SQL, dialect="bigquery", connection_id=None)  # per user
    other = sq.create_query(users["u1"], name="costs", sql=SQL, dialect="bigquery", connection_id=None)
    with pytest.raises(sq.NameTaken):
        sq.update_query(users["u1"], other.id, version=1, name="revenue")


def test_saves_bump_the_version_keep_history_and_detect_conflicts(users) -> None:
    ctx = users["u1"]
    saved = sq.create_query(ctx, name="q", sql=SQL, dialect="bigquery", connection_id=None)
    renamed = sq.update_query(ctx, saved.id, version=1, name="renamed")
    assert renamed.version == 1  # renaming isn't a new version of the SQL
    v2 = sq.update_query(ctx, saved.id, version=1, sql=SQL.replace("'CA'", "'TX'"))
    assert v2.version == 2
    with pytest.raises(sq.VersionConflict) as conflict:  # an editor still on version 1
        sq.update_query(ctx, saved.id, version=1, sql="SELECT 1")
    assert conflict.value.current == 2
    assert [v.version for v in sq.list_versions(ctx, saved.id)] == [2, 1]
    for i in range(25):
        sq.update_query(ctx, saved.id, version=2 + i, sql=f"SELECT {i}")
    assert len(sq.list_versions(ctx, saved.id)) == sq.KEEP_VERSIONS


def test_search_by_name_text_and_table(users) -> None:
    ctx = users["u1"]
    sq.create_query(ctx, name="ca_industries", sql=SQL, dialect="bigquery", connection_id=None,
                    description="Top industries in California")
    sq.create_query(ctx, name="workspace_followup", sql="WITH x AS (SELECT * FROM q1) SELECT * FROM x",
                    dialect="duckdb", connection_id=None)
    names = lambda **kw: [q.name for q in sq.list_queries(ctx, **kw)]
    assert names(q="naics") == ["ca_industries"]  # SQL text
    assert names(q="california") == ["ca_industries"]  # description
    assert names(q="firmo") == ["ca_industries"]  # table
    assert names(table="businesses") == ["ca_industries"] and names(table="firmo.businesses") == ["ca_industries"]
    assert names(table="q1") == ["workspace_followup"]  # the CTE x isn't a table


def test_unknown_or_foreign_connection_is_rejected(users) -> None:
    with pytest.raises(sq.SavedQueryError):
        sq.create_query(users["u1"], name="x", sql=SQL, dialect="bigquery", connection_id=uuid.uuid4())
    with pytest.raises(sq.SavedQueryError):
        sq.normalize_name("***")
