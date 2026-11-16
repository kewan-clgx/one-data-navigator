import uuid

from odn.platform.catalog import search_index
from odn.platform.catalog.search_index import get_search_index
from odn.platform.connectors.base import CatalogNode, ColumnSchema
from odn.platform.policy.sql_policy import SqlPolicy


class _Connector:
    def __init__(self) -> None:
        self.column_calls = 0

    def list_catalog(self, parent: tuple[str, ...] = ()) -> list[CatalogNode]:
        if not parent:
            return [CatalogNode(kind="dataset", path=("p", d), name=d) for d in ("sales", "hr")]
        return [CatalogNode(kind="table", path=(*parent, t), name=t) for t in ("orders", "secret")]

    def list_columns(self, container: tuple[str, ...]) -> dict[tuple[str, ...], list[ColumnSchema]]:
        self.column_calls += 1
        return {
            (*container, "orders"): [ColumnSchema(name="order_id", type="INT64")],
            (*container, "secret"): [ColumnSchema(name="ssn", type="STRING")],
        }


def _policy(*patterns: str) -> SqlPolicy:
    return SqlPolicy(dialect="bigquery", allowed_objects=frozenset(patterns), default_namespace=("p",))


def test_index_only_contains_allowlisted_objects() -> None:
    search_index._cache.clear()
    idx = get_search_index(uuid.uuid4(), uuid.uuid4(), _Connector(), _policy("p.sales.orders"))  # type: ignore[arg-type]
    assert [d.name for d in idx.datasets] == ["sales"]
    [table] = idx.datasets[0].tables
    assert table.name == "orders" and [c.name for c in table.columns] == ["order_id"]


def test_index_is_cached_per_allowlist() -> None:
    search_index._cache.clear()
    tenant, conn, connector = uuid.uuid4(), uuid.uuid4(), _Connector()
    get_search_index(tenant, conn, connector, _policy("p.sales.*"))  # type: ignore[arg-type]
    get_search_index(tenant, conn, connector, _policy("p.sales.*"))  # type: ignore[arg-type]
    assert connector.column_calls == 1
    wider = get_search_index(tenant, conn, connector, _policy("p.sales.*", "p.hr.*"))  # type: ignore[arg-type]
    assert connector.column_calls == 3 and len(wider.datasets) == 2
