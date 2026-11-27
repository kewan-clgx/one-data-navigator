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
    ConnectorError,
    CostEstimate,
    QueryExecution,
    QueryLimits,
    TableSchema,
)
from odn.platform.governance.audit import AuditRecord
from odn.platform.query import service
from odn.platform.query.service import (
    ConfirmationRequired,
    PreviewSink,
    QueryRejected,
    check_query,
    run_query,
    scan_cap,
)
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


def test_mysql_usage_preserves_catalog_case(audits, monkeypatch):
    class MySQLFake(FakeConnector):
        dialect = "mysql"

        @property
        def default_namespace(self) -> tuple[str, ...]:
            return ("Sales",)

    profile = ConnectionProfile(
        id=uuid.uuid4(), tenant_id=CTX.tenant_id, name="mysql", kind="mysql",
        config={"host": "mysql.example.com", "database": "Sales", "user": "reader"},
        secret_ref="file:test", allowed_objects=["Sales.Orders"],
    )
    recorded = []
    monkeypatch.setattr(
        service, "record_usage",
        lambda ctx, cid, tables, *, preserve_case: recorded.append((tables, preserve_case)),
    )
    result = run_query(
        CTX, profile, "SELECT id FROM Orders", source="chat", sink=PreviewSink(),
        limits=QueryLimits(), connector=MySQLFake(),
    )
    assert result.tables == ("Sales.Orders",)
    assert recorded == [(("Sales.Orders",), True)]
    assert audits[0].dialect == "mysql"


def test_oracle_usage_preserves_quoted_catalog_case(audits, monkeypatch):
    class OracleFake(FakeConnector):
        dialect = "oracle"

        @property
        def default_namespace(self) -> tuple[str, ...]:
            return ("HR",)

    profile = ConnectionProfile(
        id=uuid.uuid4(), tenant_id=CTX.tenant_id, name="oracle", kind="oracle",
        config={"host": "oracle.example.com", "service_name": "reports", "schema": "HR", "user": "READER"},
        secret_ref="file:test", allowed_objects=["HR.Orders"],
    )
    recorded = []
    monkeypatch.setattr(
        service, "record_usage",
        lambda ctx, cid, tables, *, preserve_case: recorded.append((tables, preserve_case)),
    )
    result = run_query(
        CTX, profile, 'SELECT id FROM "HR"."Orders"', source="chat", sink=PreviewSink(),
        limits=QueryLimits(), connector=OracleFake(),
    )
    assert result.tables == ("HR.Orders",)
    assert recorded == [(("HR.Orders",), True)]
    assert audits[0].dialect == "oracle"


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
    with pytest.raises(QueryRejected, match=r"about 2,199 GB, over the 1,100 GB per-query limit"):
        run_query(CTX, PROFILE, "SELECT id FROM orders", source="chat", sink=PreviewSink(),
                  limits=QueryLimits(max_bytes_billed=1024**4), connector=conn, confirmed=True)
    assert conn.executed == []
    assert audits[-1].reason == "estimated bytes exceed the maximum billed limit"


def test_tenant_scan_cap_lowers_the_limit(audits: list[AuditRecord]) -> None:
    capped = dataclasses.replace(CTX, max_bytes_billed=25 * 1024**3)
    with pytest.raises(QueryRejected):
        run_query(capped, PROFILE, "SELECT id FROM orders", source="chat", sink=PreviewSink(),
                  limits=QueryLimits(), connector=FakeConnector(bytes_estimate=30 * 1024**3), confirmed=True)
    run_query(capped, PROFILE, "SELECT id FROM orders", source="chat", sink=PreviewSink(),
              limits=QueryLimits(), connector=FakeConnector(bytes_estimate=20 * 1024**3), confirmed=True)


class _FreePreviewConnector(FakeConnector):
    def free_preview(self, sql: str, limits: QueryLimits) -> QueryExecution | None:
        execution = _Execution(3)
        execution.job_id = None
        return execution

    def estimate_cost(self, sql: str) -> CostEstimate:
        raise AssertionError("a free preview must not be dry-run against the scan cap")


def test_free_preview_skips_the_scan_cap(audits: list[AuditRecord]) -> None:
    conn = _FreePreviewConnector(bytes_estimate=2 * 1024**4)
    outcome = run_query(CTX, PROFILE, "SELECT * FROM proj.sales.orders LIMIT 3", source="explorer",
                        sink=PreviewSink(), limits=QueryLimits(), connector=conn)
    assert (outcome.meta.rows, conn.executed, audits[-1].decision) == (3, [], "allowed")


def test_free_preview_still_enforces_the_policy(audits: list[AuditRecord]) -> None:
    with pytest.raises(QueryRejected):
        run_query(CTX, PROFILE, "SELECT * FROM proj.hr.salaries LIMIT 3", source="explorer",
                  sink=PreviewSink(), limits=QueryLimits(), connector=_FreePreviewConnector())


def test_scan_cap_by_role_and_tenant() -> None:
    admin = dataclasses.replace(CTX, role="admin")
    assert (scan_cap(CTX), scan_cap(admin)) == (100 * 10**9, 200 * 10**9)
    assert scan_cap(dataclasses.replace(admin, max_bytes_billed=25 * 1024**3)) == 25 * 1024**3


class _FailingEstimate(FakeConnector):
    def estimate_cost(self, sql: str) -> CostEstimate:
        raise ConnectorError("Syntax error: Unexpected keyword FORM at [1:10]")


class _NoDryRun(FakeConnector):
    supports_dry_run = False


@pytest.mark.parametrize(
    ("connector", "sql", "status", "cost_known"),
    [
        (FakeConnector(bytes_estimate=5_000_000), "SELECT id FROM orders", "ok", True),
        (FakeConnector(bytes_estimate=11 * 1024**3), "SELECT id FROM orders", "confirm", True),
        (FakeConnector(bytes_estimate=101 * 10**9), "SELECT id FROM orders", "over_cap", True),
        (_FreePreviewConnector(), "SELECT * FROM proj.sales.orders LIMIT 3", "free_preview", True),
        (FakeConnector(), "SELECT * FROM proj.hr.salaries", "rejected", False),
        (_FailingEstimate(), "SELECT id FROM orders", "error", False),
        (_NoDryRun(), "SELECT id FROM orders", "ok", False),
    ],
)
def test_check_query_mirrors_run_decisions_without_running(connector, sql, status, cost_known, audits) -> None:
    result = check_query(CTX, PROFILE, sql, connector=connector)
    assert (result.status, result.cost_known) == (status, cost_known)
    assert connector.executed == [] and audits == []


def test_check_query_messages_are_human_sized() -> None:
    assert check_query(CTX, PROFILE, "SELECT id FROM orders", connector=FakeConnector(bytes_estimate=6_100_000)).message \
        == "This query will scan about 6.1 MB."


def test_row_cap_truncates_and_cancels(audits: list[AuditRecord]) -> None:
    conn, sink = FakeConnector(total_rows=1000), PreviewSink()
    out = run_query(CTX, PROFILE, "SELECT id FROM orders", source="explorer", sink=sink,
                    limits=QueryLimits(max_rows=150), connector=conn)
    assert out.meta.truncated and out.meta.rows == 150 and len(sink.rows) == 150
    assert conn.cancelled == ["job_1"]


def test_connector_failure_is_audited(audits: list[AuditRecord]) -> None:
    class FailedConnector(FakeConnector):
        def execute_paged(self, sql: str, limits: QueryLimits) -> QueryExecution:
            raise ConnectorError("Snowflake request failed (error 123)")

    with pytest.raises(service.QueryFailed, match="error 123"):
        run_query(CTX, PROFILE, "SELECT id FROM orders", source="chat", sink=PreviewSink(),
                  limits=QueryLimits(), connector=FailedConnector())
    assert audits[0].decision == "failed"
    assert audits[0].reason == "Snowflake request failed (error 123)"


def test_truncated_result_closes_stream_without_unsupported_cancel(audits: list[AuditRecord]) -> None:
    closed: list[bool] = []

    class Execution(_Execution):
        def batches(self) -> Iterator[pa.RecordBatch]:
            try:
                yield from super().batches()
            finally:
                closed.append(True)

    class Connector(FakeConnector):
        supports_cancel = False

        def execute_paged(self, sql: str, limits: QueryLimits) -> QueryExecution:
            return Execution(1000)

    conn = Connector()
    result = run_query(CTX, PROFILE, "SELECT id FROM orders", source="chat", sink=PreviewSink(),
                       limits=QueryLimits(max_rows=50), connector=conn)
    assert result.meta.truncated and result.meta.rows == 50
    assert closed == [True] and not conn.cancelled


def test_preview_sink_makes_binary_json_safe() -> None:
    sink = PreviewSink()
    sink.write(pa.record_batch({"h": [b"\xff\x00"], "s": [{"b": b"\x01"}]}))
    assert sink.rows == [{"h": "/wA=", "s": {"b": "AQ=="}}]


def test_decimals_become_numbers() -> None:
    from decimal import Decimal

    from odn.platform.workspace.rows import json_safe

    assert json_safe({"sum": Decimal(200013), "avg": Decimal("44.10")}) == {"sum": 200013, "avg": 44.1}
