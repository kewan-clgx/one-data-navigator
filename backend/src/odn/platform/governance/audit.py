"""Append-only query audit log."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from odn.db import tenant_transaction

Source = Literal["chat", "explorer", "skill", "workspace"]
Decision = Literal["allowed", "rejected", "failed"]


@dataclass
class AuditRecord:
    tenant_id: UUID
    user_id: UUID
    connection_id: UUID | None
    source: Source
    dialect: str
    sql_text: str
    decision: Decision
    reason: str | None = None
    tables: tuple[str, ...] = field(default_factory=tuple)
    bytes_estimated: int | None = None
    bytes_processed: int | None = None
    rows_returned: int | None = None
    duration_ms: int | None = None
    job_id: str | None = None


def write_audit(record: AuditRecord) -> None:
    with tenant_transaction(record.tenant_id) as conn:
        conn.execute(
            """
            INSERT INTO audit_logs (tenant_id, user_id, connection_id, source, dialect, sql_text, decision,
                                    reason, tables, bytes_estimated, bytes_processed, rows_returned,
                                    duration_ms, job_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.tenant_id,
                record.user_id,
                record.connection_id,
                record.source,
                record.dialect,
                record.sql_text,
                record.decision,
                record.reason,
                list(record.tables),
                record.bytes_estimated,
                record.bytes_processed,
                record.rows_returned,
                record.duration_ms,
                record.job_id,
            ),
        )
