"""Per-tenant daily LLM token budgets, LLM usage recording, and query/LLM telemetry summaries."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from odn.config import settings
from odn.db import tenant_transaction
from odn.tenancy.context import RequestContext


class BudgetStatus(BaseModel):
    daily_token_limit: int
    tokens_used_today: int
    remaining: int
    exceeded: bool
    resets_at: datetime


def _today() -> date:
    return datetime.now(UTC).date()


def budget_status(tenant_id: UUID) -> BudgetStatus:
    with tenant_transaction(tenant_id) as conn:
        override = conn.execute("SELECT daily_token_limit FROM tenant_llm_budgets").fetchone()
        used = conn.execute(
            "SELECT coalesce(sum(prompt_tokens + completion_tokens), 0) AS used FROM llm_usage WHERE usage_date = %s",
            (_today(),),
        ).fetchone()["used"]
    limit = override["daily_token_limit"] if override else settings.llm_daily_token_budget
    return BudgetStatus(
        daily_token_limit=limit,
        tokens_used_today=used,
        remaining=max(limit - used, 0),
        exceeded=used >= limit,
        resets_at=datetime.combine(_today() + timedelta(days=1), time(), tzinfo=UTC),
    )


def record_llm_usage(ctx: RequestContext, model: str, prompt_tokens: int, completion_tokens: int) -> BudgetStatus:
    with tenant_transaction(ctx.tenant_id) as conn:
        conn.execute(
            """
            INSERT INTO llm_usage (tenant_id, user_id, usage_date, model, requests, prompt_tokens, completion_tokens)
            VALUES (%s, %s, %s, %s, 1, %s, %s)
            ON CONFLICT (tenant_id, usage_date, user_id, model) DO UPDATE
              SET requests = llm_usage.requests + 1,
                  prompt_tokens = llm_usage.prompt_tokens + EXCLUDED.prompt_tokens,
                  completion_tokens = llm_usage.completion_tokens + EXCLUDED.completion_tokens,
                  updated_at = now()
            """,
            (ctx.tenant_id, ctx.user_id, _today(), model, prompt_tokens, completion_tokens),
        )
    return budget_status(ctx.tenant_id)


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
            SELECT usage_date AS day, model, sum(requests)::int AS requests,
                   sum(prompt_tokens)::bigint AS prompt_tokens, sum(completion_tokens)::bigint AS completion_tokens,
                   count(DISTINCT user_id)::int AS users
            FROM llm_usage
            WHERE usage_date >= %s
            GROUP BY 1, 2
            ORDER BY 1 DESC, 2
            """,
            (since,),
        ).fetchall()
    return {
        "since": since,
        "queries": [dict(r) for r in queries],
        "llm": [dict(r) for r in llm],
        "budget": budget_status(ctx.tenant_id),
    }
