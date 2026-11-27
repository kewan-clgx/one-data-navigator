import pytest

from odn.platform.catalog.metadata_collect import collect_container, collect_containers
from odn.platform.connectors.base import CatalogNode, ColumnSchema, ConnectorError
from odn.platform.policy.sql_policy import SqlPolicy


class Connector:
    dialect = "bigquery"

    def __init__(self):
        self.fail_root = False
        self.fail_columns = False
        self.tables = [
            CatalogNode(kind="table", path=("p", "d", "orders"), name="orders", description="Orders"),
            CatalogNode(kind="table", path=("p", "d", "secret"), name="secret"),
        ]

    def list_catalog_complete(self, parent=()):
        if not parent:
            if self.fail_root:
                raise ConnectorError("root truncated")
            return [CatalogNode(kind="dataset", path=("p", "d"), name="d")]
        return self.tables

    def list_columns(self, container):
        if self.fail_columns:
            raise ConnectorError("column listing failed")
        return {
            (*container, "orders"): [ColumnSchema(name="customer.id", type="STRING", description="Customer")],
            (*container, "secret"): [ColumnSchema(name="private", type="STRING")],
        }


@pytest.fixture
def policy():
    return SqlPolicy("bigquery", frozenset({"p.d.orders"}))


def test_collect_filters_objects_and_preserves_source_fields(policy):
    connector = Connector()
    [root] = collect_containers(connector, policy)
    result = collect_container(connector, policy, root)
    [table] = result.tables
    assert table.path == ("p", "d", "orders") and table.description == "Orders"
    [field] = table.fields
    assert (field.path, field.description, field.nullable) == (("customer.id",), "Customer", None)


def test_incomplete_scope_never_returns_a_publishable_result(policy):
    connector = Connector()
    [root] = collect_containers(connector, policy)
    connector.fail_columns = True
    with pytest.raises(ConnectorError, match="column listing failed"):
        collect_container(connector, policy, root)
    connector.fail_columns = False
    connector.tables.append(CatalogNode(kind="table", path=("p", "d", "orders"), name="orders"))
    with pytest.raises(ConnectorError, match="duplicate"):
        collect_container(connector, policy, root)
    connector.tables = [CatalogNode(kind="table", path=("p", "d", "missing"), name="missing")]
    wider = SqlPolicy("bigquery", frozenset({"p.d.*"}))
    with pytest.raises(ConnectorError, match="columns are incomplete"):
        collect_container(connector, wider, root)
    connector.fail_root = True
    with pytest.raises(ConnectorError, match="root truncated"):
        collect_containers(connector, policy)
