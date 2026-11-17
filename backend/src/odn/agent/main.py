from typing import Any

from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from fastapi import FastAPI, HTTPException, status
from google.adk.apps import App, ResumabilityConfig
from google.adk.sessions import BaseSessionService, InMemorySessionService, Session

from odn.agent.agent import TITLE_STATE_KEY, build_root_agent, conversation_title
from odn.agent.identity import BearerAuthMiddleware, user_key
from odn.config import settings

app = FastAPI(title="One Data Navigator Agent Runtime")
app.add_middleware(BearerAuthMiddleware)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "agent"}


def _user_id(_input: object) -> str:
    # Sessions are scoped per tenant+user so threads can never be resumed by someone else.
    key = user_key.get()
    if not key:
        raise PermissionError("unauthenticated agent run")
    return key


APP_NAME = "odn"
# Set by ag_ui_adk on every session it creates for an AG-UI thread.
THREAD_ID_STATE_KEY = "_ag_ui_thread_id"


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
