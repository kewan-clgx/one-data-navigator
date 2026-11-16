"""Row/column helpers shared by the API and the worker (no heavy dependencies)."""

from __future__ import annotations

import base64
from decimal import Decimal
from typing import Any

import pyarrow as pa


def json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def preview_rows(table: pa.Table, limit: int) -> list[dict[str, Any]]:
    return [json_safe(r) for r in table.slice(0, limit).to_pylist()]


def column_info(schema: pa.Schema) -> list[dict[str, Any]]:
    return [{"name": f.name, "type": str(f.type), "nullable": f.nullable} for f in schema]
