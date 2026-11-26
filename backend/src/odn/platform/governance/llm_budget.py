"""Daily LLM token budgets on the platform key, LLM usage recording, and query/LLM telemetry summaries.

Users without their own LLM key get a daily trial on the platform key: a per-user limit
(ODN_LLM_TRIAL_USER_DAILY_TOKENS) inside the tenant limit (ODN_LLM_DAILY_TOKEN_BUDGET or tenant_llm_budgets).
Usage on a user's own key is recorded (key_source = 'user') but never counts against either limit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from odn.config import settings
from odn.db import tenant_transaction
from odn.tenancy.context import RequestContext

KeySource = Literal["platform", "user"]


class BudgetStatus(BaseModel):
    # "user": the user's own key is used and no platform limit applies.
    key_source: KeySource = "platform"
    # Tenant-wide platform-key usage and limit.
    daily_token_limit: int
    tokens_used_today: int
    remaining: int
    # This user's platform-key (trial) usage and limit; None when not evaluated for a user.
    user_daily_token_limit: int | None = None
    user_tokens_used_today: int | None = None
    exceeded: bool
    # Which limit was hit: "tenant" or "user" (the user's trial).
    exceeded_scope: Literal["tenant", "user"] | None = None
    resets_at: datetime


def _today() -> date:
    return datetime.now(UTC).date()


def budget_status(tenant_id: UUID, user_id: UUID | None = None) -> BudgetStatus:
    with tenant_transaction(tenant_id, user_id) as conn:
        override = conn.execute("SELECT daily_token_limit FROM tenant_llm_budgets").fetchone()
        usage = conn.execute(
            """
            SELECT coalesce(sum(prompt_tokens + completion_tokens), 0)::bigint AS tenant_used,
                   coalesce(sum(prompt_tokens + completion_tokens) FILTER (WHERE user_id = %s), 0)::bigint AS user_used
            FROM llm_usage WHERE usage_date = %s AND key_source = 'platform'
            """,
            (user_id, _today()),
        ).fetchone()
        has_own_key = user_id is not None and conn.execute("SELECT 1 FROM user_llm_keys").fetchone() is not None
    limit = override["daily_token_limit"] if override else settings.llm_daily_token_budget
    used = usage["tenant_used"]
    status = BudgetStatus(
        daily_token_limit=limit,
        tokens_used_today=used,
        remaining=max(limit - used, 0),
        exceeded=False,
        resets_at=datetime.combine(_today() + timedelta(days=1), time(), tzinfo=UTC),
    )
    if has_own_key:
        status.key_source = "user"
        return status
    if user_id is not None:
        status.user_daily_token_limit = settings.llm_trial_user_daily_tokens
        status.user_tokens_used_today = usage["user_used"]
        if usage["user_used"] >= settings.llm_trial_user_daily_tokens:
            status.exceeded, status.exceeded_scope = True, "user"
    if used >= limit:
        status.exceeded, status.exceeded_scope = True, "tenant"
    return status


def record_llm_usage(
    ctx: RequestContext,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    key_source: KeySource = "platform",
    cached_tokens: int = 0,
) -> BudgetStatus:
    with tenant_transaction(ctx.tenant_id) as conn:
        conn.execute(
            """
            INSERT INTO llm_usage (tenant_id, user_id, usage_date, model, requests, prompt_tokens, completion_tokens,
                                   key_source, cached_tokens)
            VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, usage_date, user_id, model, key_source) DO UPDATE
              SET requests = llm_usage.requests + 1,
                  prompt_tokens = llm_usage.prompt_tokens + EXCLUDED.prompt_tokens,
                  completion_tokens = llm_usage.completion_tokens + EXCLUDED.completion_tokens,
                  cached_tokens = llm_usage.cached_tokens + EXCLUDED.cached_tokens,
                  updated_at = now()
            """,
            (ctx.tenant_id, ctx.user_id, _today(), model, prompt_tokens, completion_tokens, key_source, cached_tokens),
        )
    return budget_status(ctx.tenant_id, ctx.user_id)


def telemetry_summary(ctx: RequestContext, days: int) -> dict[str, Any]:
    """Daily query latency/bytes by engine (warehouse vs DuckDB) and LLM tokens, for this tenant."""
    since = _today() - timedelta(days=days - 1)
    with tenant_transaction(ctx.tenant_id) as conn:
        queries = conn.execute(
            """
            SELECT (occurred_at AT TIME ZONE 'utc')::date AS day,
                   CASE WHEN dialect = 'duckdb' THEN 'duckdb' ELSE 'warehouse' END AS engine,
                   count(*) FILTER (WHERE decision = 'allowed') AS executed,
                   count(*) FILTER (WHERE decision = 'rejected') AS rejected,
                   count(*) FILTER (WHERE decision = 'failed') AS failed,
                   round(percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms)
                         FILTER (WHERE decision = 'allowed'))::int AS p50_ms,
                   round(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)
                         FILTER (WHERE decision = 'allowed'))::int AS p95_ms,
                   coalesce(sum(bytes_estimated), 0)::bigint AS bytes_estimated,
                   coalesce(sum(bytes_processed), 0)::bigint AS bytes_processed
            FROM audit_logs
            WHERE occurred_at >= %s
            GROUP BY 1, 2
            ORDER BY 1 DESC, 2
            """,
            (since,),
        ).fetchall()
        llm = conn.execute(
            """
            SELECT usage_date AS day, model, key_source, sum(requests)::int AS requests,
                   sum(prompt_tokens)::bigint AS prompt_tokens, sum(completion_tokens)::bigint AS completion_tokens,
                   count(DISTINCT user_id)::int AS users
            FROM llm_usage
            WHERE usage_date >= %s
            GROUP BY 1, 2, 3
            ORDER BY 1 DESC, 2, 3
            """,
            (since,),
        ).fetchall()
    return {
        "since": since,
        "queries": [dict(r) for r in queries],
        "llm": [dict(r) for r in llm],
        "budget": budget_status(ctx.tenant_id),
    }
