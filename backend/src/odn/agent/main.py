from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from fastapi import FastAPI
from google.adk.apps import App, ResumabilityConfig
from google.adk.sessions import BaseSessionService

from odn.agent.agent import build_root_agent
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


def _session_service() -> BaseSessionService | None:
    if not settings.agent_session_db_url:
        return None  # in-memory (tests / no database)
    from google.adk.sessions import DatabaseSessionService

    return DatabaseSessionService(db_url=settings.agent_session_db_url)


adk_agent = ADKAgent.from_app(
    # Resumable so a paused run_sql (cost confirmation) re-runs after the user approves.
    App(name="odn", root_agent=build_root_agent(), resumability_config=ResumabilityConfig(is_resumable=True)),
    user_id_extractor=_user_id,
    session_service=_session_service(),
    # Durable sessions outlive the in-memory tracking window; idle cleanup must not delete them.
    delete_session_on_cleanup=False,
    save_session_to_memory_on_cleanup=False,
)
add_adk_fastapi_endpoint(app, adk_agent, path="/agent")
