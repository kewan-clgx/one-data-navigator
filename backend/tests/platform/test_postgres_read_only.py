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


def _connector() -> PostgresConnector:
    info = psycopg.conninfo.conninfo_to_dict(OWNER_URL)
    return PostgresConnector(
        host=info["host"],
        port=int(info["port"]),
        database=info["dbname"],
        user=info["user"],
        password=info["password"],
        ssl_mode="disable",
        min_size=1,
        max_size=1,
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
