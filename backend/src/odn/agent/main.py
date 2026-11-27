import hmac
import json
import logging
import os
import re
import uuid
from typing import Annotated, Any

import httpx
from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from fastapi import FastAPI, Header, HTTPException, status
from google.adk.apps import App, ResumabilityConfig
from google.adk.events import Event
from google.adk.sessions import BaseSessionService, InMemorySessionService, Session
from google.genai import types
from pydantic import BaseModel, Field

from odn.agent.agent import CONTINUED_STATE_KEY, TITLE_STATE_KEY, build_root_agent, conversation_title
from odn.agent.identity import BearerAuthMiddleware, bearer_token, user_key
from odn.config import settings

# Our own operational logs (model routing, Copilot call sizes; never message content) at INFO; libraries keep
# their defaults. Uvicorn configures only its own loggers.
_odn_log = logging.getLogger("odn")
if not _odn_log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    _odn_log.addHandler(_handler)
    _odn_log.setLevel(logging.INFO)
    _odn_log.propagate = False

app = FastAPI(title="EasyQuery Agent Runtime")
app.add_middleware(BearerAuthMiddleware)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "agent"}


class CompleteIn(BaseModel):
    messages: list[dict[str, str]] = Field(min_length=1, max_length=4)


@app.post("/internal/complete")
async def internal_complete(body: CompleteIn, x_odn_agent_token: Annotated[str, Header()] = "") -> dict[str, Any]:
    """One platform-model completion for the API (suggested table questions). The platform LLM key stays here; the
    API checks the tenant budget first and records the usage. Requires the shared agent token and a signed-in user."""
    if not settings.agent_token or not hmac.compare_digest(x_odn_agent_token, settings.agent_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "agent token required")
    import litellm

    model = os.environ["LITELLM_MODEL"]
    resp = await litellm.acompletion(
        model=model, api_key=os.environ.get("LITELLM_API_KEY") or None,
        api_base=os.environ.get("LITELLM_API_BASE") or None, messages=body.messages, temperature=0.3, timeout=60,
        num_retries=1,
    )
    usage = getattr(resp, "usage", None)
    return {
        "text": resp.choices[0].message.content or "", "model": model,
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }


def _user_id(_input: object) -> str:
    # Sessions are scoped per tenant+user so threads can never be resumed by someone else.
    key = user_key.get()
    if not key:
        raise PermissionError("unauthenticated agent run")
    return key


APP_NAME = "odn"
# Set by ag_ui_adk on every session it creates for an AG-UI thread.
THREAD_ID_STATE_KEY = "_ag_ui_thread_id"
# ag_ui_adk's durable "already handled" ledger: imported history must never be re-run as new input.
PROCESSED_PREFIX = "_ag_ui_processed_message:"


def _session_service() -> BaseSessionService:
    if not settings.agent_session_db_url:
        return InMemorySessionService()  # tests / no database
    from google.adk.sessions import DatabaseSessionService

    return DatabaseSessionService(db_url=settings.agent_session_db_url)


session_service = _session_service()

adk_agent = ADKAgent.from_app(
    # Resumable so a paused run_sql (cost confirmation) re-runs after the user approves.
    App(name=APP_NAME, root_agent=build_root_agent(), resumability_config=ResumabilityConfig(is_resumable=True)),
    user_id_extractor=_user_id,
    session_service=session_service,
    # New threads get session id == thread id, so the browser can key uploads by its thread id.
    # Older threads (generated session ids) still resolve through ag_ui_adk's thread lookup.
    use_thread_id_as_session_id=True,
    # Durable sessions outlive the in-memory tracking window; idle cleanup must not delete them.
    delete_session_on_cleanup=False,
    save_session_to_memory_on_cleanup=False,
)
add_adk_fastapi_endpoint(app, adk_agent, path="/agent")


def conversation_summary(session: Session) -> dict[str, Any] | None:
    thread_id = session.state.get(THREAD_ID_STATE_KEY)
    if not thread_id:
        return None
    return {
        "threadId": thread_id,
        "sessionId": session.id,
        "title": session.state.get(TITLE_STATE_KEY) or "",
        "updatedAt": session.last_update_time,
        "continuedFrom": (session.state.get(CONTINUED_STATE_KEY) or {}).get("owner"),
    }


async def _user_sessions() -> list[Session]:
    key = user_key.get()
    if not key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthenticated")
    return (await session_service.list_sessions(app_name=APP_NAME, user_id=key)).sessions


# Conversations from before titles were stored are named from their first message (bounded per request).
MAX_TITLE_LOOKUPS = 20


async def _first_message_title(user: str, session_id: str) -> str:
    session = await session_service.get_session(app_name=APP_NAME, user_id=user, session_id=session_id)
    for event in session.events if session else []:
        if event.author == "user" and event.content and event.content.parts:
            text = " ".join(p.text for p in event.content.parts if p.text)
            if text.strip():
                return conversation_title(text)
    return ""


@app.get("/conversations")
async def conversations() -> dict[str, list[dict[str, Any]]]:
    """The caller's own conversations, most recently active first (the Chats tab)."""
    items = [s for s in map(conversation_summary, await _user_sessions()) if s]
    items.sort(key=lambda c: c["updatedAt"], reverse=True)
    user = user_key.get() or ""
    for item in [c for c in items if not c["title"]][:MAX_TITLE_LOOKUPS]:
        item["title"] = await _first_message_title(user, item["sessionId"])
    return {"conversations": items}


@app.delete("/conversations/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(thread_id: str) -> None:
    """Deletes the caller's chat session for a thread; its workspace tables and charts are kept."""
    key = user_key.get()
    matches = [s for s in await _user_sessions() if s.state.get(THREAD_ID_STATE_KEY) == thread_id]
    if not matches:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    for session in matches:
        await session_service.delete_session(app_name=APP_NAME, user_id=key, session_id=session.id)
    # Best effort: forget ag_ui_adk's cached thread -> session mapping so the id is not resumed.
    cache = getattr(adk_agent, "_session_lookup_cache", None)
    if isinstance(cache, dict):
        for cache_key in [k for k in cache if isinstance(k, tuple) and k and k[0] == thread_id]:
            cache.pop(cache_key, None)


AGENT_AUTHOR = "odn_analyst"
THREAD_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ImportIn(BaseModel):
    threadId: str = Field(pattern=THREAD_ID_RE.pattern)
    title: str = Field(max_length=200)
    owner: str = Field(max_length=200)
    sharedAt: str = Field(max_length=40)
    messages: list[dict[str, Any]] = Field(max_length=400)


def _tool_response(content: Any) -> dict[str, Any]:
    try:
        value = json.loads(content) if isinstance(content, str) else content
    except ValueError:
        value = content
    return value if isinstance(value, dict) else {"result": value}


def events_from_messages(messages: list[dict[str, Any]]) -> list[Event]:
    """AG-UI messages (a shared snapshot) as ADK session events, so history and the model's context match."""
    events: list[Event] = []
    invocation = ""
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role == "user" and isinstance(content, str) and content.strip():
            invocation = f"e-{uuid.uuid4()}"
            events.append(Event(id=str(uuid.uuid4()), invocation_id=invocation, author="user",
                                content=types.Content(role="user", parts=[types.Part(text=content)])))
        elif role == "assistant" and invocation:
            parts = [types.Part(text=content)] if isinstance(content, str) and content.strip() else []
            for call in m.get("toolCalls") or []:
                fn = call.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                parts.append(types.Part(function_call=types.FunctionCall(
                    id=call.get("id"), name=fn.get("name") or "unknown", args=args if isinstance(args, dict) else {})))
            if parts:
                events.append(Event(id=str(uuid.uuid4()), invocation_id=invocation, author=AGENT_AUTHOR,
                                    content=types.Content(role="model", parts=parts)))
        elif role == "tool" and invocation and m.get("toolCallId"):
            calls = {p.function_call.id: p.function_call.name for e in events for p in e.content.parts if p.function_call}
            if m["toolCallId"] not in calls:
                continue
            events.append(Event(id=str(uuid.uuid4()), invocation_id=invocation, author=AGENT_AUTHOR,
                                content=types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
                                    id=m["toolCallId"], name=calls[m["toolCallId"]], response=_tool_response(m.get("content"))))])))
    return events


@app.post("/conversations/import", status_code=status.HTTP_201_CREATED)
async def import_conversation(body: ImportIn) -> dict[str, str]:
    """Starts the caller's own conversation from a shared snapshot ("Ask a follow-up")."""
    key = user_key.get()
    if not key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthenticated")
    events = events_from_messages(body.messages)
    if not events:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "nothing to import")
    handled = {e.id for e in events} | {
        p.function_call.id for e in events for p in e.content.parts if p.function_call and p.function_call.id
    }
    state = {
        THREAD_ID_STATE_KEY: body.threadId, "_ag_ui_app_name": APP_NAME, "_ag_ui_user_id": key,
        TITLE_STATE_KEY: conversation_title(body.title) or body.title,
        CONTINUED_STATE_KEY: {"owner": body.owner, "shared_at": body.sharedAt, "title": body.title},
        **{PROCESSED_PREFIX + i: True for i in handled},
    }
    session = await session_service.create_session(app_name=APP_NAME, user_id=key, session_id=body.threadId, state=state)
    for event in events:
        await session_service.append_event(session, event)
    return {"threadId": body.threadId}


# Demo -> personal account: the API says which demo users' chats this caller (verified email) may import; the
# conversations move here, where the session store lives (events are copied, then the demo sessions are deleted).


async def _api(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    token = bearer_token.get()
    async with httpx.AsyncClient(base_url=settings.api_url, timeout=15) as client:
        resp = await client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    return resp.json()


async def _demo_sessions() -> tuple[dict[str, Any], list[tuple[str, Session]]]:
    claim = await _api("GET", "/v1/me/demo-chats")
    keys = [f"{claim['tenant']}:{s}" for s in claim.get("subjects", [])] if claim else []
    found: list[tuple[str, Session]] = []
    for key in keys:
        for s in (await session_service.list_sessions(app_name=APP_NAME, user_id=key)).sessions:
            if s.state.get(THREAD_ID_STATE_KEY):
                found.append((key, s))
    return claim, found


@app.get("/demo-chats")
async def demo_chats() -> dict[str, Any]:
    """Chats from the caller's live demo that can be imported (none unless a personal account, verified email)."""
    _, found = await _demo_sessions()
    titles = [s.state.get(TITLE_STATE_KEY) or "" for _, s in found]
    return {"count": len(found), "titles": [t for t in titles if t][:10]}


@app.post("/demo-chats/import")
async def import_demo_chats() -> dict[str, int]:
    key = user_key.get()
    if not key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthenticated")
    claim, found = await _demo_sessions()
    mine = {s.id for s in await _user_sessions()}
    moved = 0
    for demo_key, listed in found:
        source = await session_service.get_session(app_name=APP_NAME, user_id=demo_key, session_id=listed.id)
        if source is None:
            continue
        if source.id not in mine:
            state = {k: v for k, v in source.state.items() if not k.startswith(("temp:", "app:", "user:"))}
            state["_ag_ui_user_id"] = key
            target = await session_service.create_session(
                app_name=APP_NAME, user_id=key, session_id=source.id, state=state
            )
            for event in source.events:
                # The copied state already holds the result; replaying deltas would only repeat it.
                await session_service.append_event(
                    target, event.model_copy(update={"actions": event.actions.model_copy(update={"state_delta": {}})})
                )
            moved += 1
        await session_service.delete_session(app_name=APP_NAME, user_id=demo_key, session_id=source.id)
    if claim:
        await _api("POST", "/v1/me/demo-chats/imported", json={"subjects": claim.get("subjects", [])})
    return {"imported": moved}
