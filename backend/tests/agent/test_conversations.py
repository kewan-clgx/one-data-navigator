"""Chats tab: conversation titles, per-user listing and deletion, and app context in the prompt."""

import pytest
from fastapi import HTTPException

from odn.agent import main
from odn.agent.agent import conversation_title, ui_context_text
from odn.agent.identity import user_key


def test_title_is_single_line_and_truncated_on_a_word() -> None:
    assert conversation_title("  How many\n businesses   per state? ") == "How many businesses per state?"
    long = "Show me the total revenue for every county in California, grouped by year and industry code please"
    title = conversation_title(long)
    assert len(title) <= 80 and title.endswith("…") and not title.endswith(" …")


def test_ui_context_skips_empty_values() -> None:
    text = ui_context_text([
        {"description": "Selected table", "value": "Scoped to p.d.t"},
        {"description": "Uploads", "value": "  "},
        "junk",
    ])
    assert text == "- Selected table: Scoped to p.d.t"
    assert ui_context_text(None) == ""


async def _thread(user: str, thread_id: str, title: str = "") -> None:
    state = {main.THREAD_ID_STATE_KEY: thread_id}
    if title:
        state["conversation_title"] = title
    await main.session_service.create_session(app_name=main.APP_NAME, user_id=user, state=state)


async def test_untitled_conversations_are_named_from_their_first_message() -> None:
    from google.adk.events import Event
    from google.genai import types

    session = await main.session_service.create_session(
        app_name=main.APP_NAME, user_id="t:carol", state={main.THREAD_ID_STATE_KEY: "thread-c1"}
    )
    await main.session_service.append_event(
        session, Event(author="user", content=types.Content(role="user", parts=[types.Part(text="Top 5 states?")]))
    )
    token = user_key.set("t:carol")
    try:
        assert (await main.conversations())["conversations"][0]["title"] == "Top 5 states?"
    finally:
        user_key.reset(token)


async def test_lists_only_the_callers_conversations_and_deletes_them() -> None:
    await _thread("t:alice", "thread-a1", "Revenue by state")
    await _thread("t:alice", "thread-a2")
    await _thread("t:bob", "thread-b1", "Bob's question")

    token = user_key.set("t:alice")
    try:
        listed = (await main.conversations())["conversations"]
        assert {c["threadId"] for c in listed} == {"thread-a1", "thread-a2"}
        assert next(c for c in listed if c["threadId"] == "thread-a1")["title"] == "Revenue by state"

        with pytest.raises(HTTPException) as other_users:
            await main.delete_conversation("thread-b1")
        assert other_users.value.status_code == 404

        await main.delete_conversation("thread-a1")
        assert [c["threadId"] for c in (await main.conversations())["conversations"]] == ["thread-a2"]
    finally:
        user_key.reset(token)

    token = user_key.set("t:bob")
    try:
        assert [c["threadId"] for c in (await main.conversations())["conversations"]] == ["thread-b1"]
    finally:
        user_key.reset(token)


async def test_imported_share_reads_back_as_history_and_is_never_rerun() -> None:
    from ag_ui_adk import adk_events_to_messages

    from odn.agent.agent import continued_note

    messages = [
        {"id": "u1", "role": "user", "content": "Top cities?"},
        {"id": "a1", "role": "assistant", "content": "", "toolCalls": [
            {"id": "t1", "type": "function", "function": {"name": "run_sql", "arguments": '{"sql": "SELECT 1"}'}}]},
        {"id": "r1", "role": "tool", "toolCallId": "t1", "content": '{"status": "ok", "workspace_table": "q1"}'},
        {"id": "r2", "role": "tool", "toolCallId": "nope", "content": "{}"},  # no matching call: dropped
        {"id": "a2", "role": "assistant", "content": "Irvine leads."},
    ]
    thread = "6f1c2d3e-4a5b-4c6d-8e7f-001122334455"
    token = user_key.set("t:dana")
    try:
        await main.import_conversation(main.ImportIn(
            threadId=thread, title="Top cities?", owner="Ann", sharedAt="2026-11-20", messages=messages))
        listed = (await main.conversations())["conversations"]
    finally:
        user_key.reset(token)
    assert listed[0]["threadId"] == thread and listed[0]["continuedFrom"] == "Ann"

    session = await main.session_service.get_session(app_name=main.APP_NAME, user_id="t:dana", session_id=thread)
    history = adk_events_to_messages(session.events)
    assert [m.role for m in history] == ["user", "assistant", "tool", "assistant"]
    assert history[1].tool_calls[0].function.name == "run_sql" and history[2].tool_call_id == "t1"
    handled = {k[len(main.PROCESSED_PREFIX):] for k, v in session.state.items() if k.startswith(main.PROCESSED_PREFIX) and v}
    assert {e.id for e in session.events} | {"t1"} <= handled  # the client re-sends these; none run again
    assert "shared by Ann" in continued_note(session.state["odn_continued_from"])


async def test_demo_chats_move_into_the_callers_account(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.adk.events import Event
    from google.genai import types

    demo = await main.session_service.create_session(
        app_name=main.APP_NAME, user_id="demo:visitor1",
        state={main.THREAD_ID_STATE_KEY: "thread-d1", "conversation_title": "Top products"},
    )
    await main.session_service.append_event(
        demo, Event(author="user", content=types.Content(role="user", parts=[types.Part(text="Top products?")]))
    )
    await _thread("demo:stranger", "thread-d2", "Someone else's chat")
    calls = []

    async def api(method: str, path: str, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        return {"tenant": "demo", "subjects": ["visitor1"]} if method == "GET" else {"retired": 1}

    monkeypatch.setattr(main, "_api", api)
    token = user_key.set("personal:pat")
    try:
        assert await main.demo_chats() == {"count": 1, "titles": ["Top products"]}
        assert await main.import_demo_chats() == {"imported": 1}
        mine = (await main.conversations())["conversations"]
        assert [(c["threadId"], c["title"]) for c in mine] == [("thread-d1", "Top products")]
        moved = await main.session_service.get_session(app_name=main.APP_NAME, user_id="personal:pat",
                                                       session_id=demo.id)
        assert [e.content.parts[0].text for e in moved.events] == ["Top products?"]
        assert await main.demo_chats() == {"count": 0, "titles": []}
    finally:
        user_key.reset(token)
    left = await main.session_service.list_sessions(app_name=main.APP_NAME, user_id="demo:visitor1")
    assert left.sessions == []
    assert ("POST", "/v1/me/demo-chats/imported", {"subjects": ["visitor1"]}) in calls
    others = await main.session_service.list_sessions(app_name=main.APP_NAME, user_id="demo:stranger")
    assert len(others.sessions) == 1
