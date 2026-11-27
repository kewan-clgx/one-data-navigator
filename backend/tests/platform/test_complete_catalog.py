from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from odn.platform.connectors.base import ConnectorError
from odn.platform.connectors.bigquery import BigQueryConnector
from odn.platform.connectors.postgres import PostgresConnector


@pytest.mark.parametrize("parent", [(), ("project", "dataset")])
def test_bigquery_complete_catalog_does_not_stop_at_legacy_limit(parent):
    connector = BigQueryConnector.__new__(BigQueryConnector)
    connector.project = "project"
    connector._client = MagicMock()
    datasets = [SimpleNamespace(dataset_id=f"d{i}") for i in range(10_001)]
    tables = [SimpleNamespace(table_id=f"t{i}", table_type="TABLE") for i in range(10_001)]
    connector._client.list_datasets.side_effect = lambda project, max_results: datasets[:max_results]
    connector._client.list_tables.side_effect = lambda dataset, max_results: tables[:max_results]

    assert len(connector.list_catalog(parent)) == 10_000
    result = connector.list_catalog_complete(parent)
    assert len(result) == 10_001
    assert result[-1].path[-1] == ("t10000" if parent else "d10000")


def test_bigquery_complete_catalog_propagates_page_failure():
    connector = BigQueryConnector.__new__(BigQueryConnector)
    connector.project = "project"
    connector._client = MagicMock()

    def pages():
        yield SimpleNamespace(dataset_id="first")
        raise RuntimeError("later page failed")

    connector._client.list_datasets.return_value = pages()
    with pytest.raises(RuntimeError, match="later page failed"):
        connector.list_catalog_complete()


@pytest.mark.parametrize("parent", [(), ("public",)])
def test_postgres_complete_catalog_removes_limit(connector_for_postgres, parent):
    connector = connector_for_postgres
    cursor = MagicMock()
    cursor.fetchall.return_value = (
        [{"table_name": "orders", "table_type": "BASE TABLE"}]
        if parent else [{"schema_name": "public"}]
    )
    connector._metadata = MagicMock()
    connector._metadata.return_value.__enter__.return_value = cursor
    connector.list_catalog_complete(parent)
    sql, params = cursor.execute.call_args.args
    assert " LIMIT " not in sql
    assert params == (("public",) if parent else ())

    connector.list_catalog(parent)
    sql, params = cursor.execute.call_args.args
    assert " LIMIT " in sql
    assert params[-1] == 10_000


@pytest.fixture
def connector_for_postgres():
    return PostgresConnector(host="localhost", port=5432, database="test", user="reader")


@pytest.mark.parametrize("kind", ["mysql", "oracle", "snowflake"])
def test_capped_connector_complete_listing_propagates_truncation(kind, monkeypatch):
    if kind == "mysql":
        from odn.platform.connectors.mysql import MySQLConnector

        connector = MySQLConnector.__new__(MySQLConnector)
        parent = ("sales",)
    elif kind == "oracle":
        from odn.platform.connectors.oracle import OracleConnector

        connector = OracleConnector.__new__(OracleConnector)
        parent = ("SALES",)
    else:
        from odn.platform.connectors.snowflake import SnowflakeConnector

        connector = SnowflakeConnector.__new__(SnowflakeConnector)
        parent = ("DB", "SALES")
    cursor = MagicMock()
    cursor.fetchall.return_value = [()] * 10_001
    cursor.fetchmany.return_value = [()] * 10_001
    context = MagicMock()
    context.__enter__.return_value = cursor
    monkeypatch.setattr(connector, "_cursor", lambda: context)
    with pytest.raises(ConnectorError, match="exceeds 10,000"):
        connector.list_catalog_complete(parent)
