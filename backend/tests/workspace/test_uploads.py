"""Upload parsing: CSV/XLSX become Arrow tables; unsafe names and unsupported types are rejected."""

import io

import pyarrow as pa
import pytest
from openpyxl import Workbook

from odn.platform.workspace.uploads import (
    UnsupportedUpload,
    clean_filename,
    extension_of,
    parse_csv,
    parse_xlsx,
)


def _xlsx(rows: list[list[object]]) -> bytes:
    wb = Workbook()
    for row in rows:
        wb.active.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_csv_infers_types_and_dedupes_headers() -> None:
    table = parse_csv(b"state,count,state,\nCA,3,x,1\nTX,5,y,2\n").table
    assert table.column_names == ["state", "count", "state_2", "column_4"]
    assert table.schema.field("count").type == pa.int64()
    assert table.num_rows == 2


def test_xlsx_uses_first_row_as_header_and_skips_blank_rows() -> None:
    table = parse_xlsx(_xlsx([["name", "amount"], ["a", 1.5], [None, None], ["b", 2]])).table
    assert table.column_names == ["name", "amount"]
    assert table.column("amount").to_pylist() == [1.5, 2]


def test_xlsx_mixed_type_column_falls_back_to_text() -> None:
    table = parse_xlsx(_xlsx([["code"], [10], ["A7"]])).table
    assert table.column("code").to_pylist() == ["10", "A7"]


def test_instruction_rows_above_the_header_become_notes() -> None:
    parsed = parse_xlsx(_xlsx([
        ["Direction:\nmatch each field in the catalog."],
        [],
        ["Field Name\n(What does it mean to us)", "Matched Field", "2024 Score", None],
        ["business name", None, None, None],
        ["employee numbers", None, None, None],
    ]))
    assert parsed.header_row == 3
    assert parsed.preamble == "Direction:\nmatch each field in the catalog."
    assert parsed.table.column_names == ["field_name_what_does_it_mean_to_us", "matched_field", "c_2024_score", "column_4"]
    assert parsed.table.column("field_name_what_does_it_mean_to_us").to_pylist() == ["business name", "employee numbers"]


def test_csv_header_is_found_below_a_title_line() -> None:
    parsed = parse_csv(b"Quarterly export\nState,Count\nCA,3\n")
    assert (parsed.header_row, parsed.preamble, parsed.table.column_names) == (2, "Quarterly export", ["state", "count"])


def test_corrupt_spreadsheet_is_rejected() -> None:
    with pytest.raises(UnsupportedUpload):
        parse_xlsx(b"not a workbook")


def test_filenames_are_reduced_to_a_safe_base_name() -> None:
    assert clean_filename('../../etc/"passwd".csv') == "passwd.csv"
    assert clean_filename("C:\\Users\\me\\report.xlsx") == "report.xlsx"
    with pytest.raises(UnsupportedUpload):
        clean_filename("../")


@pytest.mark.parametrize("name", ["chart.svg", "page.html", "script.js", "noext"])
def test_unsupported_types_are_rejected(name: str) -> None:
    with pytest.raises(UnsupportedUpload):
        extension_of(name)
