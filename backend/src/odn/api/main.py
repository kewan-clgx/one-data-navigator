from fastapi import FastAPI, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from google.auth import exceptions as auth_exc

from odn.api.admin_routes import router as admin_router
from odn.api.artifacts_routes import router as artifacts_router
from odn.api.auth_routes import router as auth_router
from odn.api.collibra_routes import admin_router as collibra_admin_router
from odn.api.collibra_routes import jwks_router
from odn.api.collibra_routes import router as collibra_router
from odn.api.me_routes import router as me_router
from odn.api.routes import router
from odn.api.share_routes import admin_router as sharing_admin_router
from odn.api.share_routes import public_router as public_share_router
from odn.api.share_routes import router as share_router
from odn.api.shared_connections_routes import router as shared_connections_router
from odn.api.usage_routes import router as usage_router
from odn.api.wall_routes import public_router as public_wall_router
from odn.api.wall_routes import router as wall_router
from odn.api.workspace_routes import router as workspace_router
from odn.config import settings
from odn.platform.connectors.ssh_tunnel import TunnelError

app = FastAPI(title="EasyQuery API")
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.include_router(shared_connections_router)
app.include_router(router)
app.include_router(workspace_router)
app.include_router(usage_router)
app.include_router(me_router)
app.include_router(artifacts_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(collibra_router)
app.include_router(collibra_admin_router)
app.include_router(jwks_router)
app.include_router(share_router)
app.include_router(sharing_admin_router)
app.include_router(public_share_router)
app.include_router(wall_router)
app.include_router(public_wall_router)


@app.exception_handler(auth_exc.RefreshError)
def credentials_rejected(_request: Request, exc: auth_exc.RefreshError) -> JSONResponse:
    # A connection's key was rejected by Google (common for user-supplied keys); not a server error.
    return JSONResponse(
        status_code=409, content={"detail": f"the connection's key was rejected by Google: {exc.args[0]}"}
    )


@app.exception_handler(TunnelError)
def tunnel_failed(_request: Request, exc: TunnelError) -> JSONResponse:
    # The bastion is the client's infrastructure: report it plainly instead of a 500.
    return JSONResponse(status_code=502, content={"detail": f"SSH tunnel: {exc}"})


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
