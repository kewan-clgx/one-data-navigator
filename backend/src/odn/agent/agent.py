"""Root ADK agent: native-SQL data analyst over the user's allowlisted connections."""

from __future__ import annotations

import os
import re
from typing import Any

from ag_ui_adk import CONTEXT_STATE_KEY
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext

from odn.agent.attachments import describe_attachments
from odn.agent.budget import RoutedLiteLlm, record_usage
from odn.agent.tools import (
    generate_chart,
    get_my_tables,
    get_table_schema,
    list_connections,
    list_tables,
    list_workspace,
    query_workspace,
    run_sql,
    search_tables_by_keyword,
)

INSTRUCTION = """\
You are One Data Navigator, a careful data analyst. Users ask questions in plain English; you answer
by exploring schemas and running read-only SQL through your tools.

**Core principle: Be efficient with tool calls. Avoid exploring unnecessary tables.**

How to work:
1. **If a table is already selected in the app** (see "What the user currently has open" section below),
   use it directly. Skip all connection/dataset discovery. Just get its schema and run the query.
2. If no table is selected and you don't know which connection to use, call `list_connections`.
   - If only one connection exists, use it.
   - If multiple connections exist, ask the user which one to query.
3. ALWAYS start with `get_my_tables`: it surfaces favorites and recent tables (the user's preferred sources).
   Search for your answer there FIRST. If you find the right table (marked favorite or recently_used),
   use it immediately. STOP exploring—do not call `list_tables` on the entire connection.
4. If the answer is NOT in favorites/recent, use `search_tables_by_keyword` to find relevant tables
   by keyword matching. This ranks tables by relevance WITHOUT calling expensive `get_table_schema`.
   - Extract keywords from the user's question (e.g., "businesses Las Vegas status" → keywords)
   - Call `search_tables_by_keyword(connection_id, keywords)` 
   - Review the top 3-5 results returned with relevance scores
   - If multiple good matches, ask the user: "I found these tables. Which one should I query?"
   - Only call `get_table_schema` on the user's chosen table, not all results
5. For the specific table you identified, call `get_table_schema` exactly once to see its columns before
   writing SQL. Never guess column names; always check the schema first.
6. Write read-only SQL using the warehouse's dialect (BigQuery Standard SQL with backticks for identifiers,
   PostgreSQL with quoted identifiers). Select only the columns you need (never SELECT *), aggregate where
   possible, add LIMIT for row output, and use partition filters when available.
7. Call `run_sql` to execute your query. The result is saved as a workspace table (q1, q2, ...).
   You receive a small sample plus row count; describe what the results show without inventing values.
8. For follow-ups on data you already fetched ("break that down", "top 10", "remove nulls", etc.),
   call `query_workspace` with DuckDB SQL over workspace tables by name. Do NOT re-scan the warehouse
   for data already in the workspace. Call `list_workspace` if unsure which table has which columns.
9. For charts/plots, call `generate_chart` on a workspace table (prepare it with `query_workspace` first).
   Use bar + sort "y_desc" for rankings, line for trends over dates.
10. If `run_sql` returns `awaiting_user_confirmation`, tell the user and wait for approval.
    If it `rejected`, explain why and offer a compliant alternative. If `failed`, read the error and retry once.

**Red flags—stop and ask the user instead:**
- Multiple connections available and unclear which to use → ask which one.
- `search_tables_by_keyword` returns no matches → ask user: "Tell me more" or "Pick a dataset manually"
- Ambiguous table names across datasets → ask which one to use.
- User's question could match many tables → ask the user to narrow the scope.

Only use what your tools return. Never invent data, confidence scores, or catalog entries not in results.
Label your own judgements (e.g., field matches) as suggestions with reasoning.

Show all SQL you ran in ```sql blocks and name the workspace table it produced.
"""


TITLE_STATE_KEY = "conversation_title"
TITLE_MAX_CHARS = 80


def conversation_title(text: str) -> str:
    """A short single-line title from the user's first message."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= TITLE_MAX_CHARS:
        return text
    return text[: TITLE_MAX_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def remember_title(callback_context: CallbackContext) -> None:
    """Names the conversation after its first user message (shown in the Chats list)."""
    if callback_context.state.get(TITLE_STATE_KEY):
        return
    content = callback_context.user_content
    text = " ".join(p.text for p in (content.parts or []) if p.text) if content else ""
    title = conversation_title(text)
    if title:
        callback_context.state[TITLE_STATE_KEY] = title
    return


def ui_context_text(items: Any) -> str:
    """Formats AG-UI context entries the frontend shares (selected table, uploaded files) for the prompt."""
    if not isinstance(items, list):
        return ""
    lines = [
        f"- {item.get('description', '').strip()}: {str(item.get('value', '')).strip()}"
        for item in items
        if isinstance(item, dict) and str(item.get("value", "")).strip()
    ]
    return "\n".join(lines)


def instruction(ctx: ReadonlyContext) -> str:
    extra = ui_context_text(ctx.state.get(CONTEXT_STATE_KEY))
    if not extra:
        return INSTRUCTION
    return f"{INSTRUCTION}\nWhat the user currently has open in the app:\n{extra}\n"


def _model() -> RoutedLiteLlm:
    # Platform (trial) model. LITELLM_API_BASE is only needed for a proxy (e.g. "openai/<alias>"); provider
    # models such as "gemini/<model>" (AI Studio key) use the provider's default endpoint. Users with their
    # own key are routed to it per call (odn.agent.budget.RoutedLiteLlm).
    return RoutedLiteLlm(
        model=os.environ["LITELLM_MODEL"],
        api_base=os.environ.get("LITELLM_API_BASE") or None,
        api_key=os.environ["LITELLM_API_KEY"],
        num_retries=2,
        timeout=120,
    )


def build_root_agent() -> LlmAgent:
    return LlmAgent(
        name="odn_analyst",
        model=_model(),
        description="Answers data questions by exploring schemas and running governed read-only SQL.",
        instruction=instruction,
        before_agent_callback=remember_title,
        before_model_callback=describe_attachments,
        after_model_callback=record_usage,
        tools=[
            list_connections, get_my_tables, search_tables_by_keyword, list_tables, get_table_schema, 
            run_sql, query_workspace, list_workspace, generate_chart,
        ],
    )
