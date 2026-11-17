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
