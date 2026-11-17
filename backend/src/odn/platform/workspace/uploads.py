"""User file uploads: allowed types, and CSV/XLSX parsing into Arrow tables for the workspace."""

from __future__ import annotations

import csv
import io
import itertools
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePath

import pyarrow as pa
import pyarrow.csv as pacsv

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_XLSX_ROWS = 1_000_000
HEADER_SCAN_ROWS = 20
MAX_PREAMBLE_CHARS = 2000

# The stored MIME type comes from the extension, never from the client.
MIME_BY_EXTENSION = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
}
TABULAR_EXTENSIONS = {".csv", ".xlsx"}
# Only raster images are ever served inline; everything else downloads (no SVG/HTML rendering of user files).
INLINE_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


class UnsupportedUpload(ValueError):
    pass


def clean_filename(filename: str) -> str:
    name = PurePath(filename.replace("\\", "/")).name.strip()
    name = re.sub(r"[\x00-\x1f\x7f\"]", "", name)[:255]
    if not name or name in {".", ".."}:
        raise UnsupportedUpload("a file name is required")
    return name


def extension_of(filename: str) -> str:
    ext = PurePath(filename).suffix.lower()
    if ext not in MIME_BY_EXTENSION:
        allowed = ", ".join(sorted(MIME_BY_EXTENSION))
        raise UnsupportedUpload(f"unsupported file type {ext or '(none)'}; allowed: {allowed}")
    return ext


@dataclass
class ParsedTable:
    table: pa.Table
    # 1-based row of the header in the file.
    header_row: int
    # Text found above the header (titles, instructions); handed to the model as notes.
    preamble: str


def _column_name(value: object, position: int) -> str:
    """SQL-friendly names: "Field Name\n(What it means)" -> "field_name_what_it_means"."""
    name = re.sub(r"[^0-9a-zA-Z]+", "_", str(value) if value is not None else "").strip("_").lower()[:63]
    if not name:
        return f"column_{position}"
    return f"c_{name}"[:63] if name[0].isdigit() else name


def _unique_names(raw: list[object]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for i, value in enumerate(raw, start=1):
        base = _column_name(value, i)
        name, n = base, 2
        while name in seen:
            name, n = f"{base}_{n}", n + 1
        seen.add(name)
        names.append(name)
    return names


def _filled(row: Sequence[object]) -> int:
    return sum(1 for v in row if v is not None and str(v).strip())


def _header_index(rows: list[Sequence[object]]) -> int:
    """The first row that looks like a header: it fills at least half as many cells as the widest
    of the first rows, so a title or instruction line above the table (often one merged cell) is skipped."""
    counts = [_filled(r) for r in rows[:HEADER_SCAN_ROWS]]
    widest = max(counts, default=0)
    if widest <= 1:
        return 0
    needed = max(2, -(-widest // 2))
    return next(i for i, c in enumerate(counts) if c >= needed)


def _preamble(rows: list[Sequence[object]]) -> str:
    lines = [" ".join(str(v).strip() for v in r if v is not None and str(v).strip()) for r in rows]
    return re.sub(r"[ \t]+", " ", "\n".join(line for line in lines if line)).strip()[:MAX_PREAMBLE_CHARS]


def _column(values: list[object]) -> pa.Array:
    try:
        return pa.array(values)
    except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError):
        # Mixed types in one column: keep everything as text rather than failing the upload.
        return pa.array([None if v is None else str(v) for v in values], type=pa.string())


def parse_csv(data: bytes) -> ParsedTable:
    head = data[:256 * 1024].decode("utf-8-sig", errors="replace").splitlines()[:HEADER_SCAN_ROWS]
    first_rows = list(csv.reader(head))
    skip = _header_index(first_rows)
    try:
        table = pacsv.read_csv(io.BytesIO(data), read_options=pacsv.ReadOptions(skip_rows=skip))
    except pa.ArrowInvalid as exc:
        raise UnsupportedUpload(f"could not read the CSV file: {exc}") from None
    return ParsedTable(
        table.rename_columns(_unique_names(table.column_names)), skip + 1, _preamble(first_rows[:skip])
    )


def parse_xlsx(data: bytes) -> ParsedTable:
    """Reads the first worksheet; the header is detected (title or instruction rows above it are skipped)."""
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many types for corrupt or non-xlsx input
        raise UnsupportedUpload(f"could not read the Excel file: {type(exc).__name__}") from None
    try:
        rows = workbook.worksheets[0].iter_rows(values_only=True)
        first_rows = list(itertools.islice(rows, HEADER_SCAN_ROWS))
        if not any(_filled(r) for r in first_rows):
            raise UnsupportedUpload("the first worksheet is empty")
        header_at = _header_index(first_rows)
        names = _unique_names(list(first_rows[header_at]))
        columns: list[list[object]] = [[] for _ in names]
        for count, row in enumerate(itertools.chain(first_rows[header_at + 1:], rows), start=1):
            if count > MAX_XLSX_ROWS:
                raise UnsupportedUpload(f"the worksheet has more than {MAX_XLSX_ROWS:,} rows")
            if row is None or all(v is None for v in row):
                continue
            for i in range(len(names)):
                columns[i].append(row[i] if i < len(row) else None)
    finally:
        workbook.close()
    table = pa.table({name: _column(values) for name, values in zip(names, columns, strict=True)})
    return ParsedTable(table, header_at + 1, _preamble(first_rows[:header_at]))


def parse_tabular(ext: str, data: bytes) -> ParsedTable:
    return parse_csv(data) if ext == ".csv" else parse_xlsx(data)
