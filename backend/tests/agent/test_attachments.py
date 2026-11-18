"""Chat attachments reach the model as short notes (table, columns, sample rows), never as file content."""

from types import SimpleNamespace
from typing import Any

import httpx
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from odn.agent import attachments, tools


def _ref(uri: str, name: str, mime: str) -> types.Part:
    return types.Part(file_data=types.FileData(file_uri=uri, display_name=name, mime_type=mime))


async def test_attachments_become_notes_and_the_stored_content_is_untouched(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_api(method: str, path: str, **kwargs: Any) -> httpx.Response:
        calls.append(path)
        if path.endswith("/rows"):
            return httpx.Response(200, json={"total": 2, "rows": [{"state": "CA", "n": 3}]})
        return httpx.Response(200, json=[{"name": "q1", "row_count": 2,
                                          "sql_text": "-- uploaded file: counts.csv (header on row 2)\n"
                                                      "-- text above the table in the file:\n-- Use 2024 data only",
                                          "columns": [
            {"name": "state", "type": "string"}, {"name": "n", "type": "int64"}]}])

    monkeypatch.setattr(tools, "_api", fake_api)
    attachments._notes.clear()
    csv = _ref("/api/odn/artifacts/files/abc?inline=true&table=q1", "counts.csv", "text/csv")
    png = _ref("/api/odn/artifacts/files/def?inline=true", "map.png", "image/png")
    external = _ref("gs://bucket/doc.pdf", "doc.pdf", "application/pdf")
    stored = types.Content(role="user", parts=[types.Part(text="chart this"), csv, png, external])
    request = LlmRequest(contents=[stored])
    ctx = SimpleNamespace(session=SimpleNamespace(id="s1"))

    await attachments.describe_attachments(ctx, request)
    await attachments.describe_attachments(ctx, request)  # second model call in the same turn

    parts = request.contents[0].parts
    assert parts[0].text == "chart this"
    assert "workspace table q1 (2 rows; columns: state string, n int64)" in parts[1].text
    assert '"state": "CA"' in parts[1].text
    assert "(treat it as the user's notes): Use 2024 data only" in parts[1].text
    assert "map.png (image/png)" in parts[2].text and "cannot see" in parts[2].text
    assert parts[3].file_data.file_uri == "gs://bucket/doc.pdf"  # not ours: left alone
    assert stored.parts[1].file_data is not None  # the conversation keeps the reference
    assert calls == ["workspace/s1/artifacts", "workspace/s1/artifacts/q1/rows"]  # described once


async def test_notes_are_not_shared_between_users_with_the_same_session_id(monkeypatch) -> None:
    from odn.agent.identity import user_key

    async def fake_api(method: str, path: str, **kwargs: Any) -> httpx.Response:
        owner = user_key.get()
        if path.endswith("/rows"):
            return httpx.Response(200, json={"total": 0, "rows": []})
        return httpx.Response(200, json=[{"name": "q1", "row_count": 1, "sql_text": "", "columns": [
            {"name": f"secret_of_{owner.replace(':', '_')}", "type": "string"}]}])

    monkeypatch.setattr(tools, "_api", fake_api)
    attachments._notes.clear()
    uri = "/api/odn/artifacts/files/abc?inline=true&table=q1"
    notes = {}
    for who in ("acme:alice", "globex:bob"):
        token = user_key.set(who)
        try:
            notes[who] = await attachments.describe_attachment("same-session", uri, "f.csv", "text/csv")
        finally:
            user_key.reset(token)
    assert "secret_of_acme_alice" in notes["acme:alice"]
    assert "secret_of_globex_bob" in notes["globex:bob"] and "alice" not in notes["globex:bob"]
