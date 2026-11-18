from fastapi import FastAPI, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from google.auth import exceptions as auth_exc

from odn.api.admin_routes import router as admin_router
from odn.api.artifacts_routes import router as artifacts_router
from odn.api.auth_routes import router as auth_router
from odn.api.me_routes import router as me_router
from odn.api.routes import router
from odn.api.usage_routes import router as usage_router
from odn.api.workspace_routes import router as workspace_router
from odn.config import settings

app = FastAPI(title="One Data Navigator API")
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.include_router(router)
app.include_router(workspace_router)
app.include_router(usage_router)
app.include_router(me_router)
app.include_router(artifacts_router)
app.include_router(auth_router)
app.include_router(admin_router)


@app.exception_handler(auth_exc.RefreshError)
def credentials_rejected(_request: Request, exc: auth_exc.RefreshError) -> JSONResponse:
    # A connection's key was rejected by Google (common for user-supplied keys); not a server error.
    return JSONResponse(
        status_code=409, content={"detail": f"the connection's key was rejected by Google: {exc.args[0]}"}
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "api"}


@app.get("/readyz")
def readyz(response: Response) -> dict[str, object]:
    # Imported lazily so /healthz and tests don't require the api extra.
    import psycopg

    from odn.platform.workspace.storage import check_access

    checks: dict[str, str] = {}
    try:
        with psycopg.connect(settings.database_url, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = f"error: {type(exc).__name__}"
    try:
        check_access()
        checks["gcs"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["gcs"] = f"error: {type(exc).__name__}"

    ready = all(v == "ok" for v in checks.values())
    if not ready:
        response.status_code = 503
    return {"ready": ready, "checks": checks}
