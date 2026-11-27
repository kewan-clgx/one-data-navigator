"""Collect a complete authorized source scope before opening a metadata transaction."""

from __future__ import annotations

from odn.platform.catalog.metadata_store import MetadataContainer, MetadataField, MetadataTable
from odn.platform.connectors.base import BaseConnector, CatalogNode, ConnectorError
from odn.platform.policy.sql_policy import SqlPolicy


def collect_containers(connector: BaseConnector, policy: SqlPolicy) -> list[CatalogNode]:
    """Return all permitted root containers or propagate the enumeration failure."""
    roots = connector.list_catalog_complete(())
    if any(node.kind not in ("project", "dataset", "schema") or not node.path for node in roots):
        raise ConnectorError("metadata root enumeration returned an invalid container")
    if len({node.path for node in roots}) != len(roots):
        raise ConnectorError("metadata root enumeration returned duplicate containers")
    return [node for node in roots if policy.allows_container(node.path)]


def collect_container(connector: BaseConnector, policy: SqlPolicy, root: CatalogNode) -> MetadataContainer:
    """Collect a complete scope, failing closed when column completeness cannot be established."""
    if root.kind not in ("project", "dataset", "schema") or not policy.allows_container(root.path):
        raise ConnectorError("metadata container is not authorized")
    nodes = connector.list_catalog_complete(root.path)
    if any(node.kind not in ("table", "view") or node.path[:-1] != root.path or not node.name
           or node.path[-1:] != (node.name,) for node in nodes):
        raise ConnectorError("metadata listing returned an invalid table")
    if len({node.path for node in nodes}) != len(nodes):
        raise ConnectorError("metadata listing returned duplicate tables")
    tables = [node for node in nodes if policy.allows_object(node.path)]
    columns = connector.list_columns(root.path)
    if any(not columns.get(node.path) for node in tables):
        raise ConnectorError("metadata columns are incomplete")
    return MetadataContainer(
        path=root.path,
        name=root.name,
        kind=root.kind,
        description=root.description,
        tables=tuple(
            MetadataTable(
                path=node.path, name=node.name, kind=node.kind, description=node.description,
                fields=tuple(
                    MetadataField(
                        path=(column.name,), data_type=column.type,
                        nullable=None if connector.dialect == "bigquery" else column.nullable,
                        description=column.description,
                    )
                    for column in columns[node.path]
                ),
            )
            for node in tables
        ),
    )
