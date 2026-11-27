"""Saves a conversation's first question and its answer as a table question (FAQ) for the tables it used."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from google.adk.agents.callback_context import CallbackContext

from odn.config import settings

log = logging.getLogger(__name__)

CAPTURED_STATE_KEY = "odn_question_captured"
# Set on conversations continued from a shared or saved answer: {"owner": ..., "shared_at": ..., "title": ...}.
CONTINUED_STATE_KEY = "odn_continued_from"
OK = {"ok"}
_tasks: set[asyncio.Task] = set()


@dataclass
class Turn:
    question: str = ""
    answer: str = ""
    connection_ids: set[str] = field(default_factory=set)
    sqls: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    bytes_scanned: int = 0
    failed: bool = False
    pending: bool = False


def _text(content: Any) -> str:
    return "".join(p.text for p in (getattr(content, "parts", None) or []) if getattr(p, "text", None)).strip()


def collect(events: list[Any]) -> Turn | None:
    """The question/answer of a conversation with exactly one user message, or None."""
    turn, calls = Turn(), {}
    for event in events:
        content = getattr(event, "content", None)
        if content is None:
            continue
        if event.author == "user":
            text = _text(content)
            if text:
                if turn.question:
                    return None  # a follow-up already happened; only the first question stands alone
                turn.question = text
            continue
        for part in content.parts or []:
            call, resp = getattr(part, "function_call", None), getattr(part, "function_response", None)
            if call is not None:
                calls[call.id] = (call.name, dict(call.args or {}))
            elif resp is not None and resp.id in calls:
                name, args = calls[resp.id]
                body = resp.response if isinstance(resp.response, dict) else {}
                if name not in ("run_sql", "get_table_schema"):
                    continue
                if args.get("connection_id"):
                    turn.connection_ids.add(str(args["connection_id"]))
                if name == "get_table_schema" and args.get("table") and "error" not in body:
                    turn.tables.append(str(args["table"]))
                elif name == "run_sql":
                    status = body.get("status")
                    if status == "awaiting_user_confirmation":
                        turn.pending = True
                    elif status in OK:
                        turn.pending = False
                        turn.sqls.append(str(args.get("sql", "")))
                        turn.bytes_scanned += int(body.get("bytes_processed") or 0)
                    else:
                        turn.failed = True
        text = _text(content)
        if text and not getattr(event, "partial", False):
            turn.answer = text  # the last model text is the answer
    return turn if turn.question else None


def ready(turn: Turn | None) -> bool:
    return bool(
        turn and turn.answer and not turn.failed and not turn.pending and len(turn.connection_ids) == 1
        and (turn.sqls or turn.tables)
    )


async def _post(payload: dict[str, Any]) -> None:
    from odn.agent.tools import _api

    try:
        resp = await _api("POST", "questions/capture", json=payload, headers={"X-ODN-Agent-Token": settings.agent_token})
        if resp.status_code != 200:
            log.info("question capture skipped: %s %s", resp.status_code, resp.text[:200])
    except Exception:
        log.exception("question capture failed")


def capture_question(callback_context: CallbackContext) -> None:
    """after_agent_callback: once per conversation, store the first question and answer (fire and forget)."""
    state = callback_context.state
    if state.get(CAPTURED_STATE_KEY) or state.get(CONTINUED_STATE_KEY):
        return
    turn = collect(list(callback_context.session.events))
    if not ready(turn):
        return
    state[CAPTURED_STATE_KEY] = True
    payload = {
        "connection_id": next(iter(turn.connection_ids)),
        "question": turn.question[:2000],
        "answer": turn.answer[:50_000],
        "sql": turn.sqls[:10],
        "tables": list(dict.fromkeys(turn.tables))[:10],
        "bytes_scanned": turn.bytes_scanned if turn.sqls else None,
    }
    try:
        task = asyncio.get_running_loop().create_task(_post(payload))
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
