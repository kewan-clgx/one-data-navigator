"""PostgresConnector runs user queries read-only, against the real Postgres (docker compose stack).

The AST policy cannot see side effects inside functions (nextval, user functions), so the database itself
must refuse writes. Skipped if the local Postgres is not running.
"""

import os
import uuid

import psycopg
import pytest

from odn.platform.connectors.base import QueryLimits
from odn.platform.connectors.postgres import PostgresConnector

OWNER_URL = os.environ.get("ODN_TEST_OWNER_URL", "postgresql://odn:odn_local_dev@localhost:5532/odn")


def _reachable() -> bool:
    try:
        psycopg.connect(OWNER_URL, connect_timeout=2).close()
    except psycopg.OperationalError:
        return False
    return True


pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")


@pytest.fixture
def sequence() -> str:
    name = f"odn_ro_probe_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(OWNER_URL, autocommit=True) as conn:
        conn.execute(f"CREATE SEQUENCE public.{name}")
    yield name
    with psycopg.connect(OWNER_URL, autocommit=True) as conn:
        conn.execute(f"DROP SEQUENCE IF EXISTS public.{name}")


def _connector(schema: str | None = None, password: str | None = None) -> PostgresConnector:
    info = psycopg.conninfo.conninfo_to_dict(OWNER_URL)
    return PostgresConnector(
        host=info["host"],
        port=int(info["port"]),
        database=info["dbname"],
        user=info["user"],
        password=password or info["password"],
        schema=schema,
        ssl_mode="disable",
    )


def _run(connector: PostgresConnector, sql_text: str) -> list:
    """Same call sequence as QueryService: estimate, execute, stream batches."""
    connector.estimate_cost(sql_text)
    return list(connector.execute_paged(sql_text, QueryLimits(timeout_s=30)).batches())


def test_side_effecting_select_is_rejected(sequence: str) -> None:
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        _run(_connector(), f"SELECT nextval('public.{sequence}')")


def test_session_default_cannot_reenable_writes(sequence: str) -> None:
    # A query that flips the session default must not make later queries on the pooled connection writable.
    connector = _connector()
    _run(connector, "SELECT set_config('default_transaction_read_only', 'off', false)")
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        _run(connector, f"SELECT nextval('public.{sequence}')")


def test_plain_select_still_works() -> None:
    batches = _run(_connector(), "SELECT 1 AS one")
    assert batches[0].to_pylist() == [{"one": 1}]


@pytest.fixture
def sample_schema() -> str:
    schema = f"odn_meta_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(OWNER_URL, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"CREATE TABLE {schema}.businesses (id int PRIMARY KEY, city text, status text)")
        conn.execute(f"COMMENT ON TABLE {schema}.businesses IS 'Business listings'")
        conn.execute(f"COMMENT ON COLUMN {schema}.businesses.city IS 'City name'")
        conn.execute(f"ALTER TABLE {schema}.businesses DROP COLUMN status")
        conn.execute(f"INSERT INTO {schema}.businesses SELECT g, 'x' FROM generate_series(1, 50) g")
        conn.execute(f"ANALYZE {schema}.businesses")
        conn.execute(f"CREATE VIEW {schema}.vegas AS SELECT id FROM {schema}.businesses WHERE city = 'Las Vegas'")
    yield schema
    with psycopg.connect(OWNER_URL, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


def _our_open_connections() -> int:
    with psycopg.connect(OWNER_URL) as conn:
        row = conn.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE application_name = 'one-data-navigator'"
        ).fetchone()
    return row[0]


def test_metadata_calls_work_and_leave_no_connections_open(sample_schema: str) -> None:
    before = _our_open_connections()
    c = _connector(schema=sample_schema)

    assert [s.ok for s in c.test_connection()] == [True, True, True, True]
    assert sample_schema in [n.name for n in c.list_catalog()]
    assert {(n.name, n.kind) for n in c.list_catalog((sample_schema,))} == {("businesses", "table"), ("vegas", "view")}

    table = c.get_schema((sample_schema, "businesses"))
    assert (table.kind, table.description, table.row_count) == ("table", "Business listings", 50)
    assert [(col.name, col.description) for col in table.columns] == [("id", None), ("city", "City name")]
    assert c.get_schema((sample_schema, "vegas")).kind == "view"
    with pytest.raises(ValueError, match="not found"):
        c.get_schema((sample_schema, "missing"))

    columns = c.list_columns((sample_schema,))
    assert [col.name for col in columns[(sample_schema, "businesses")]] == ["id", "city"]

    assert c.estimate_cost(f"SELECT * FROM {sample_schema}.businesses").rows_estimated == 50
    assert c.estimate_cost("SELECT * FROM no_such_table").rows_estimated is None

    c.cancel_job("not-a-pid")
    assert _our_open_connections() == before


def test_bad_password_is_reported_as_an_auth_failure() -> None:
    steps = _connector(password="definitely-wrong").test_connection()
    assert [(s.step, s.ok) for s in steps] == [("auth", False)]
