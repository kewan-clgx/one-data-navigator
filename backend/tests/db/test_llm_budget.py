"""LLM budgets and usage: per-tenant accounting, operator overrides, telemetry (skipped if Postgres is down)."""

import uuid

import psycopg
import pytest

from odn.config import settings
from odn.platform.governance.llm_budget import budget_status, record_llm_usage, telemetry_summary
from odn.tenancy.context import RequestContext
from tests.db.test_rls import APP_URL, OWNER_URL, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")


@pytest.fixture(scope="module")
def tenants():
    from odn.db import pool
    from odn.db.migrate import migrate

    migrate(OWNER_URL)
    settings.database_url = APP_URL
    pool.cache_clear()
    rows = []
    with psycopg.connect(OWNER_URL) as conn:
        for _ in range(2):
            t, u = uuid.uuid4(), uuid.uuid4()
            conn.execute("INSERT INTO tenants (id, slug, name) VALUES (%s, %s, 'x')",
                         (t, f"t-{t.hex[:12]}"))
            conn.execute("INSERT INTO users (id, tenant_id, subject) VALUES (%s, %s, 's')", (u, t))
            rows.append(RequestContext(tenant_id=t, user_id=u, subject="s", role="data_lead"))
    yield rows
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = ANY(%s)", ([r.tenant_id for r in rows],))


def test_usage_accumulates_per_tenant_and_stops_at_limit(tenants, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = tenants
    monkeypatch.setattr(settings, "llm_daily_token_budget", 1000)
    status = record_llm_usage(a, "m1", 300, 200)
    assert (status.tokens_used_today, status.remaining, status.exceeded) == (500, 500, False)
    status = record_llm_usage(a, "m1", 400, 100)
    assert (status.tokens_used_today, status.exceeded) == (1000, True)
    assert budget_status(b.tenant_id).tokens_used_today == 0


def test_operator_override_wins(tenants, monkeypatch: pytest.MonkeyPatch) -> None:
    _, b = tenants
    monkeypatch.setattr(settings, "llm_daily_token_budget", 1000)
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("INSERT INTO tenant_llm_budgets (tenant_id, daily_token_limit) VALUES (%s, 50)", (b.tenant_id,))
    record_llm_usage(b, "m1", 60, 0)
    status = budget_status(b.tenant_id)
    assert (status.daily_token_limit, status.exceeded) == (50, True)


def test_app_role_cannot_change_budgets(tenants) -> None:
    a, _ = tenants
    from odn.db import tenant_transaction

    with pytest.raises(psycopg.errors.InsufficientPrivilege), tenant_transaction(a.tenant_id) as conn:
        conn.execute("INSERT INTO tenant_llm_budgets (tenant_id, daily_token_limit) VALUES (%s, 1)", (a.tenant_id,))


def test_telemetry_is_tenant_scoped(tenants) -> None:
    a, b = tenants
    summary = telemetry_summary(a, 7)
    assert sum(r["prompt_tokens"] + r["completion_tokens"] for r in summary["llm"]) == 1000
    assert all(r["model"] == "m1" for r in summary["llm"])
    assert sum(r["prompt_tokens"] for r in telemetry_summary(b, 7)["llm"]) == 60
