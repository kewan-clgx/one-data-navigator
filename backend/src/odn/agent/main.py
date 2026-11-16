from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from fastapi import FastAPI
from google.adk.apps import App, ResumabilityConfig

from odn.agent.agent import build_root_agent
from odn.agent.identity import BearerAuthMiddleware, user_key

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


adk_agent = ADKAgent.from_app(
    # Resumable so a paused run_sql (cost confirmation) re-runs after the user approves.
    App(name="odn", root_agent=build_root_agent(), resumability_config=ResumabilityConfig(is_resumable=True)),
    user_id_extractor=_user_id,
)
add_adk_fastapi_endpoint(app, adk_agent, path="/agent")
