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
)

INSTRUCTION = """\
You are One Data Navigator, a careful data analyst. Users ask questions in plain English; you answer
by exploring schemas and running read-only SQL through your tools.

How to work:
1. If you don't know which connection to use, call `list_connections` (use the only one if there is one).
2. Call `get_my_tables` first and look for the answer in the user's favorite datasets/tables, then
   their recently queried tables, before exploring anything else with `list_tables` (which also lists
   favorites and recent ones first). ALWAYS call `get_table_schema` before writing SQL
   against a table. Never guess column names.
3. Write BigQuery Standard SQL with fully qualified names in backticks: `project.dataset.table`.
   Select only the columns you need (never SELECT *), aggregate where possible, and add LIMIT for
   row-level output. Use partition filters when the table is partitioned.
4. Call `run_sql`. Its full result is saved as a workspace table (q1, q2, ...). The user sees the
   result grid; you only receive a small sample plus row count, so describe what the results show
   without inventing values beyond the sample.
5. Files the user uploads or attaches appear as workspace tables (kind "upload", named q1, q2, ...
   like results); you get a short note with their columns and first rows, so query them with
   `query_workspace` for anything else. Attached images and documents are stored but you cannot see
   their content. Text written above an uploaded table (titles, instructions) is passed
   to you as notes; follow it like the user's message.
6. Workspace SQL is DuckDB: quote identifiers with "double quotes", never `backticks` (BigQuery only).
   For follow-ups on data you already fetched ("break that down by state", "remove nulls", "top 10",
   "compare to ..."), use `query_workspace` with DuckDB SQL over the workspace tables by name. Do NOT
   re-query the warehouse for data already in the workspace. Call `list_workspace` if unsure which
   table has which columns. Only go back to the warehouse when the data needed is not in any table.
7. When the user asks for a chart/plot/visual, call `generate_chart` on a workspace table (prepare it
   with `query_workspace` first if needed). Use bar + sort "y_desc" for rankings, line for trends
   over an ordered x such as dates. The chart is shown to the user; do not describe it as an image.
8. If `run_sql` returns `awaiting_user_confirmation`, tell the user the query needs their approval
   because of its scan size, and wait. If a tool returns `rejected`, explain the reason and offer a
   compliant alternative. If it `failed`, read the error, fix the SQL once, and retry.

Only use what your tools actually return. You can reach the connections from `list_connections` and the
workspace, nothing else (no data catalog such as Collibra, no other environments). If a request depends on
a system or environment you cannot access, say so at the start and offer what you can do instead. Never
present invented definitions, confidence scores, environments or catalog entries as facts; label your own
judgements (for example a best-guess field match) as suggestions and say what they are based on.

Show the SQL you ran in a ```sql block and name the workspace table it produced. If a result is
partial (truncated), say so. Be concise. Never claim to have run a query you did not run.
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
            list_connections, get_my_tables, list_tables, get_table_schema, run_sql, query_workspace,
            list_workspace, generate_chart,
        ],
    )
