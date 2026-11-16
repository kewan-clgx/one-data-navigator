from fastapi.testclient import TestClient

from odn.agent.main import app as agent_app
from odn.api.main import app as api_app
from odn.worker.main import app as worker_app


def test_healthz() -> None:
    for name, app in [("api", api_app), ("agent", agent_app), ("worker", worker_app)]:
        resp = TestClient(app).get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "service": name}
