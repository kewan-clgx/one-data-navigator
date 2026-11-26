from types import SimpleNamespace

import pytest

from odn.platform.connectors.base import QueryLimits
from odn.platform.connectors.bigquery import BigQueryConnector, BigQueryRowsExecution, _plain_table_preview


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM `p-1.ds.t` LIMIT 10;", ("p-1.ds.t", 10)),
        ("select * from `p-1`.ds.t u limit 5", ("p-1.ds.t", 5)),
        ("SELECT * FROM `p-1.ds.t`", None),  # no LIMIT: would list the whole table
        ("SELECT * FROM `p-1.ds.t` LIMIT 50000", None),
        ("SELECT * FROM ds.t LIMIT 5", None),  # not fully qualified
        ("SELECT a FROM `p-1.ds.t` LIMIT 5", None),
        ("SELECT * EXCEPT (a) FROM `p-1.ds.t` LIMIT 5", None),
        ("SELECT * FROM `p-1.ds.t` WHERE a = 1 LIMIT 5", None),
        ("SELECT * FROM `p-1.ds.t` ORDER BY a LIMIT 5", None),
        ("SELECT * FROM `p-1.ds.t` TABLESAMPLE SYSTEM (1 PERCENT) LIMIT 5", None),
        ("SELECT * FROM `p-1.ds.t` a JOIN `p-1.ds.u` b USING (id) LIMIT 5", None),
        ("WITH x AS (SELECT 1) SELECT * FROM `p-1.ds.t` LIMIT 5", None),
    ],
)
def test_only_plain_table_previews_qualify(sql: str, expected: tuple[str, int] | None) -> None:
    assert _plain_table_preview(sql) == expected


class _Client:
    def __init__(self, table_type: str) -> None:
        self.table_type = table_type
        self.listed: list[int] = []

    def get_table(self, table_id: str) -> SimpleNamespace:
        return SimpleNamespace(table_id=table_id, table_type=self.table_type)

    def list_rows(self, table: SimpleNamespace, max_results: int) -> object:
        self.listed.append(max_results)
        return object()


def _connector(client: _Client) -> BigQueryConnector:
    conn = BigQueryConnector.__new__(BigQueryConnector)
    conn._client = client
    return conn


def test_tables_are_listed_not_queried() -> None:
    client = _Client("TABLE")
    execution = _connector(client).free_preview("SELECT * FROM `p-1.ds.t` LIMIT 10", QueryLimits())
    assert isinstance(execution, BigQueryRowsExecution) and execution.bytes_processed == 0
    assert client.listed == [10]


def test_views_fall_back_to_a_query() -> None:
    client = _Client("VIEW")
    assert _connector(client).free_preview("SELECT * FROM `p-1.ds.v` LIMIT 10", QueryLimits()) is None
    assert client.listed == []
