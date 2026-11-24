import dataclasses
import uuid
from collections.abc import Iterator

import pyarrow as pa
import pytest

from odn.platform.connections.service import ConnectionProfile
from odn.platform.connectors.base import (
    BaseConnector,
    CatalogNode,
    ColumnSchema,
    ConnectionTestStep,
    CostEstimate,
    QueryExecution,
    QueryLimits,
    TableSchema,
)
from odn.platform.governance.audit import AuditRecord
from odn.platform.query import service
from odn.platform.query.service import ConfirmationRequired, PreviewSink, QueryRejected, run_query
from odn.tenancy.context import RequestContext

CTX = RequestContext(tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), subject="u", role="member")
PROFILE = ConnectionProfile(
    id=uuid.uuid4(),
    tenant_id=CTX.tenant_id,
    name="bq",
    kind="bigquery",
    config={"project": "proj"},
    secret_ref="file:x.json",
    allowed_objects=["proj.sales.*"],
)


class _Execution(QueryExecution):
    def __init__(self, total_rows: int) -> None:
        self.job_id = "job_1"
        self._total = total_rows

    def batches(self) -> Iterator[pa.RecordBatch]:
        self.bytes_processed = 123
        for start in range(0, self._total, 100):
            n = min(100, self._total - start)
            yield pa.record_batch({"id": list(range(start, start + n))})


class FakeConnector(BaseConnector):
    dialect = "bigquery"
    supports_dry_run = True
    supports_cancel = True

    def __init__(self, bytes_estimate: int = 1000, total_rows: int = 250) -> None:
        self.bytes_estimate = bytes_estimate
        self.total_rows = total_rows
        self.executed: list[str] = []
        self.cancelled: list[str] = []

    @property
    def default_namespace(self) -> tuple[str, ...]:
        return ("proj", "sales")

    def test_connection(self) -> list[ConnectionTestStep]:
        return []

    def list_catalog(self, parent: tuple[str, ...] = ()) -> list[CatalogNode]:
        return []

    def get_schema(self, path: tuple[str, ...]) -> TableSchema:
        raise NotImplementedError

    def list_columns(self, container: tuple[str, ...]) -> dict[tuple[str, ...], list[ColumnSchema]]:
        raise NotImplementedError

    def estimate_cost(self, sql: str) -> CostEstimate:
        return CostEstimate(bytes_processed=self.bytes_estimate)

    def execute_paged(self, sql: str, limits: QueryLimits) -> QueryExecution:
        self.executed.append(sql)
        return _Execution(self.total_rows)

    def cancel_job(self, job_id: str) -> None:
        self.cancelled.append(job_id)


@pytest.fixture
def usage(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    recorded: list[tuple[str, ...]] = []
    monkeypatch.setattr(service, "record_usage", lambda ctx, cid, tables: recorded.append(tuple(tables)))
    return recorded


@pytest.fixture
def audits(monkeypatch: pytest.MonkeyPatch, usage: list[tuple[str, ...]]) -> list[AuditRecord]:
    records: list[AuditRecord] = []
    monkeypatch.setattr(service, "write_audit", records.append)
    return records


def test_successful_query_records_table_usage(audits: list[AuditRecord], usage: list[tuple[str, ...]]) -> None:
    run_query(CTX, PROFILE, "SELECT id FROM orders", source="chat", sink=PreviewSink(),
              limits=QueryLimits(), connector=FakeConnector())
    assert usage == [("proj.sales.orders",)]


def test_rejected_query_records_no_usage(audits: list[AuditRecord], usage: list[tuple[str, ...]]) -> None:
    with pytest.raises(QueryRejected):
        run_query(CTX, PROFILE, "SELECT * FROM proj.hr.salaries", source="chat", sink=PreviewSink(),
                  limits=QueryLimits(), connector=FakeConnector())
    assert usage == []


def test_allowed_query_streams_and_audits(audits: list[AuditRecord]) -> None:
    conn, sink = FakeConnector(), PreviewSink()
    out = run_query(CTX, PROFILE, "SELECT id FROM orders", source="explorer", sink=sink,
                    limits=QueryLimits(), connector=conn)
    assert out.meta.rows == 250 and not out.meta.truncated and len(sink.rows) == 250
    assert out.tables == ("proj.sales.orders",)
    [audit] = audits
    assert (audit.decision, audit.rows_returned, audit.bytes_processed, audit.job_id) == ("allowed", 250, 123, "job_1")


def test_rejected_query_is_audited_and_never_executed(audits: list[AuditRecord]) -> None:
    conn = FakeConnector()
    with pytest.raises(QueryRejected):
        run_query(CTX, PROFILE, "DELETE FROM orders WHERE TRUE", source="chat", sink=PreviewSink(),
                  limits=QueryLimits(), connector=conn)
    assert conn.executed == []
    assert audits[0].decision == "rejected" and audits[0].reason


def test_expensive_query_requires_confirmation(audits: list[AuditRecord]) -> None:
    conn = FakeConnector(bytes_estimate=50 * 1024**3)
    with pytest.raises(ConfirmationRequired):
        run_query(CTX, PROFILE, "SELECT id FROM orders", source="chat", sink=PreviewSink(),
                  limits=QueryLimits(), connector=conn)
    assert conn.executed == []
    run_query(CTX, PROFILE, "SELECT id FROM orders", source="chat", sink=PreviewSink(),
              limits=QueryLimits(), connector=conn, confirmed=True)
    assert conn.executed == ["SELECT id FROM orders"]


def test_over_billing_cap_rejected_even_if_confirmed(audits: list[AuditRecord]) -> None:
    conn = FakeConnector(bytes_estimate=2 * 1024**4)
    with pytest.raises(QueryRejected):
        run_query(CTX, PROFILE, "SELECT id FROM orders", source="chat", sink=PreviewSink(),
                  limits=QueryLimits(max_bytes_billed=1024**4), connector=conn, confirmed=True)
    assert conn.executed == []


def test_tenant_scan_cap_lowers_the_limit(audits: list[AuditRecord]) -> None:
    capped = dataclasses.replace(CTX, max_bytes_billed=25 * 1024**3)
    with pytest.raises(QueryRejected):
        run_query(capped, PROFILE, "SELECT id FROM orders", source="chat", sink=PreviewSink(),
                  limits=QueryLimits(), connector=FakeConnector(bytes_estimate=30 * 1024**3), confirmed=True)
    run_query(capped, PROFILE, "SELECT id FROM orders", source="chat", sink=PreviewSink(),
              limits=QueryLimits(), connector=FakeConnector(bytes_estimate=20 * 1024**3), confirmed=True)


def test_row_cap_truncates_and_cancels(audits: list[AuditRecord]) -> None:
    conn, sink = FakeConnector(total_rows=1000), PreviewSink()
    out = run_query(CTX, PROFILE, "SELECT id FROM orders", source="explorer", sink=sink,
                    limits=QueryLimits(max_rows=150), connector=conn)
    assert out.meta.truncated and out.meta.rows == 150 and len(sink.rows) == 150
    assert conn.cancelled == ["job_1"]


def test_preview_sink_makes_binary_json_safe() -> None:
    sink = PreviewSink()
    sink.write(pa.record_batch({"h": [b"\xff\x00"], "s": [{"b": b"\x01"}]}))
    assert sink.rows == [{"h": "/wA=", "s": {"b": "AQ=="}}]


def test_decimals_become_numbers() -> None:
    from decimal import Decimal

    from odn.platform.workspace.rows import json_safe

    assert json_safe({"sum": Decimal(200013), "avg": Decimal("44.10")}) == {"sum": 200013, "avg": 44.1}
