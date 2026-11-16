import os
import shutil
import tempfile
from collections.abc import Iterator

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from odn.config import settings
from odn.worker import main as worker

TOKEN = "test-token"
H = {"X-ODN-Worker-Token": TOKEN}


class _Blob:
    def __init__(self, root: str, name: str) -> None:
        self.path = os.path.join(root, name.replace("/", "__"))

    def download_to_filename(self, path: str) -> None:
        shutil.copy(self.path, path)

    def upload_from_filename(self, path: str) -> None:
        shutil.copy(path, self.path)


class _Bucket:
    def __init__(self, root: str) -> None:
        self.root = root

    def blob(self, name: str) -> _Blob:
        return _Blob(self.root, name)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    root = tempfile.mkdtemp()
    monkeypatch.setattr(settings, "worker_token", TOKEN)
    monkeypatch.setattr(settings, "gcs_prefix", "odn")
    monkeypatch.setattr(worker, "_bucket", lambda: _Bucket(root))
    table = pa.table({"region": ["W", "E", "W", None] * 250, "amount": list(range(1000))})
    pq.write_table(table, _Bucket(root).blob("odn/t/u/s/a1.parquet").path)
    yield TestClient(worker.app)
    shutil.rmtree(root)


def _query(client: TestClient, sql: str, **extra: object):
    body = {"sql": sql, "inputs": {"q1": "odn/t/u/s/a1.parquet"}, "output_key": "odn/t/u/s/a2.parquet", **extra}
    return client.post("/v1/query", json=body, headers=H)


def test_follow_up_query_writes_artifact(client: TestClient) -> None:
    resp = _query(client, "SELECT region, SUM(amount) AS total FROM q1 WHERE region IS NOT NULL GROUP BY 1 ORDER BY 1")
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["row_count"] == 2 and not out["truncated"]
    assert [c["name"] for c in out["columns"]] == ["region", "total"]
    rows = client.post("/v1/rows", json={"object_key": "odn/t/u/s/a2.parquet"}, headers=H).json()
    assert rows["total"] == 2 and rows["rows"][0]["region"] == "E"


def test_requires_token(client: TestClient) -> None:
    resp = client.post("/v1/query", json={"sql": "SELECT 1", "inputs": {}, "output_key": "odn/x.parquet"})
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM '/etc/hostname'",
        "SELECT * FROM q2",
        "COPY q1 TO '/tmp/x.csv'",
        "SELECT getenv('HOME')",
    ],
)
def test_escape_attempts_rejected(client: TestClient, sql: str) -> None:
    assert _query(client, sql).status_code == 422


@pytest.mark.parametrize("key", ["other/t/a.parquet", "odn/../secrets.parquet", "odn/t/a.csv"])
def test_object_keys_confined_to_prefix(client: TestClient, key: str) -> None:
    resp = client.post("/v1/query", json={"sql": "SELECT 1 AS x", "inputs": {}, "output_key": key}, headers=H)
    assert resp.status_code == 400


def test_row_cap_marks_truncated(client: TestClient) -> None:
    out = _query(client, "SELECT * FROM q1", max_rows=100).json()
    assert out["row_count"] == 100 and out["truncated"]


def test_timeout_interrupts(client: TestClient) -> None:
    resp = _query(
        client,
        "SELECT count(*) FROM q1 a, q1 b, q1 c WHERE a.amount + b.amount + c.amount = -1",
        timeout_s=0.2,
    )
    assert resp.status_code == 408


def test_rows_pagination_and_sort(client: TestClient) -> None:
    resp = client.post(
        "/v1/rows",
        json={"object_key": "odn/t/u/s/a1.parquet", "offset": 10, "limit": 5, "order_by": "amount", "descending": True},
        headers=H,
    )
    body = resp.json()
    assert body["total"] == 1000 and [r["amount"] for r in body["rows"]] == [989, 988, 987, 986, 985]


def _chart(client: TestClient, **spec: object):
    return client.post("/v1/chart", json={"object_key": "odn/t/u/s/a1.parquet", **spec}, headers=H)


def test_chart_aggregates_and_sorts(client: TestClient) -> None:
    out = _chart(client, type="bar", x="region", y="amount", agg="sum", sort="y_desc").json()
    assert [p["x"] for p in out["points"]] == ["W", None, "E"]
    assert out["points"][0]["y"] == sum(range(0, 1000, 4)) + sum(range(2, 1000, 4))
    assert not out["truncated"] and "GROUP BY ALL" in out["sql"]


def test_chart_count_and_truncation(client: TestClient) -> None:
    out = _chart(client, type="bar", x="amount", agg="count", limit=10).json()
    assert len(out["points"]) == 10 and out["truncated"] and out["points"][0] == {"x": 0, "y": 1}


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "bar", "x": 'region") FROM t; --', "y": "amount"},
        {"type": "bar", "x": "region", "y": "nope"},
        {"type": "bar", "x": "region", "y": "amount", "series": "x\" , getenv('HOME') AS \"z"},
        {"type": "bar", "x": "region", "agg": "sum"},
    ],
)
def test_chart_rejects_unknown_columns(client: TestClient, spec: dict) -> None:
    assert _chart(client, **spec).status_code == 400


def test_csv_download(client: TestClient) -> None:
    resp = client.post("/v1/csv", json={"object_key": "odn/t/u/s/a1.parquet"}, headers=H)
    lines = resp.text.strip().splitlines()
    assert resp.headers["content-type"].startswith("text/csv")
    assert lines[0] == '"region","amount"' and len(lines) == 1001
