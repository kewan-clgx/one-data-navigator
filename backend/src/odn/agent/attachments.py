"""Chat attachments: the model gets a short description of each attached file, never the file itself.

The chat's paperclip uploads files to the artifacts store first (CSV/XLSX become workspace tables) and
attaches only a reference URL to the message. Before every model call those references are replaced
with a compact note (name, table, columns, a few sample rows), so large files cost a few hundred tokens.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from odn.agent import tools

# Reference URLs the frontend attaches (the browser can open them; the model never fetches them).
ATTACHMENT_PREFIX = "/api/odn/artifacts/files/"
SAMPLE_ROWS = tools.LLM_SAMPLE_ROWS
MAX_NOTE_CHARS = 2000

# (session id, reference URL) -> note; a reference describes the same file for the whole conversation.
_notes: dict[tuple[str, str], str] = {}


def _table_name(uri: str) -> str | None:
    values = parse_qs(urlparse(uri).query).get("table")
    return values[0] if values else None


PREAMBLE_MARKER = "-- text above the table in the file:"


def _file_notes(sql_text: str) -> str:
    """Notes the importer found above the header (stored as SQL comments on the upload table)."""
    if PREAMBLE_MARKER not in sql_text:
        return ""
    lines = sql_text.split(PREAMBLE_MARKER, 1)[1].splitlines()
    return " ".join(line.removeprefix("--").strip() for line in lines if line.strip())


async def _table_note(session_id: str, table: str) -> str:
    resp = await tools._api("GET", f"workspace/{session_id}/artifacts")
    meta = next((a for a in resp.json() if a.get("name") == table), None) if resp.status_code == 200 else None
    if meta is None:
        return f"imported as workspace table {table}; query it with query_workspace"
    columns = ", ".join(f'{c["name"]} {c["type"]}' for c in meta.get("columns", []))
    note = (
        f"imported as workspace table {table} ({meta.get('row_count', 0):,} rows; columns: {columns}); "
        "query it with query_workspace"
    )
    notes = _file_notes(meta.get("sql_text", ""))
    if notes:
        note += f". Text written above the table in the file (treat it as the user's notes): {notes}"
    rows = await tools._api(
        "GET", f"workspace/{session_id}/artifacts/{table}/rows", params={"limit": SAMPLE_ROWS}
    )
    if rows.status_code == 200 and rows.json().get("rows"):
        note += f". First rows: {json.dumps(rows.json()['rows'], default=str)}"
    return note


async def describe_attachment(session_id: str, uri: str, name: str, mime_type: str) -> str:
    key = (session_id, uri)
    if key not in _notes:
        table = _table_name(uri)
        detail = (
            await _table_note(session_id, table) if table
            else "saved in Artifacts; you cannot see its contents"
        )
        _notes[key] = f"[The user attached the file {name or 'unnamed'} ({mime_type}): {detail}.]"[:MAX_NOTE_CHARS]
    return _notes[key]


def _is_attachment(part: types.Part) -> bool:
    uri = part.file_data.file_uri if part.file_data else None
    return bool(uri and uri.startswith(ATTACHMENT_PREFIX))


async def describe_attachments(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """before_model_callback: swaps attachment references for text notes in the request sent to the model."""
    session_id = callback_context.session.id
    contents: list[types.Content] = []
    for content in llm_request.contents:
        if not any(_is_attachment(p) for p in content.parts or []):
            contents.append(content)
            continue
        parts: list[types.Part] = []
        for part in content.parts or []:
            if _is_attachment(part):
                fd: Any = part.file_data
                note = await describe_attachment(session_id, fd.file_uri, fd.display_name or "", fd.mime_type or "")
                parts.append(types.Part(text=note))
            else:
                parts.append(part)
        # New Content objects, so the stored conversation keeps its attachment references.
        contents.append(types.Content(role=content.role, parts=parts))
    llm_request.contents = contents
