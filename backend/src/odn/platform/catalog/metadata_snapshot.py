"""Download an authorized, point-in-time browser search index from stored metadata."""

from __future__ import annotations

from typing import TypedDict
from uuid import UUID

from psycopg.rows import dict_row, tuple_row

from odn.db import tenant_transaction
from odn.platform.catalog.metadata_search import MetadataStatus, _read_status
from odn.platform.policy.sql_policy import SqlPolicy

_MAX_TEXT_BYTES = 64 * 1024 * 1024
_MAX_TABLES = 100_000
_MAX_FIELDS = 1_000_000


class SnapshotUnavailable(Exception):
    pass


class SnapshotTooLarge(Exception):
    pass


class SnapshotField(TypedDict):
    name: str
    type: str
    description: str | None


class SnapshotTable(TypedDict):
    path: list[str]
    name: str
    kind: str
    columns: list[SnapshotField]


class SnapshotDataset(TypedDict):
    path: list[str]
    name: str
    tables: list[SnapshotTable]


class MetadataSnapshot(TypedDict):
    status: MetadataStatus
    datasets: list[SnapshotDataset]


def get_snapshot(tenant_id: UUID, user_id: UUID, connection_id: UUID) -> MetadataSnapshot:
    """Return one consistent catalog view, filtering each object through the current policy."""
    with tenant_transaction(tenant_id, user_id, repeatable_read=True) as conn:
        conn.execute(
            "SELECT set_config('statement_timeout', "
            "least(CASE WHEN setting::int = 0 THEN 30000 ELSE setting::int END, 30000)::text, true) "
            "FROM pg_settings WHERE name = 'statement_timeout'"
        )
        with conn.cursor(row_factory=dict_row) as cur:
            connection, status = _read_status(cur, connection_id)
            if status.state in ("not_indexed", "building", "failed"):
                raise SnapshotUnavailable("Metadata index is not ready; build or finish refreshing it first.")
            policy = SqlPolicy(dialect=connection["kind"], allowed_objects=frozenset(connection["allowed_objects"]))
            containers: dict[tuple[str, ...], SnapshotDataset] = {}
            tables: dict[tuple[str, ...], SnapshotTable] = {}
            text_bytes = 0
            for row in cur.execute(
                "SELECT path, name FROM metadata_containers WHERE connection_id = %s ORDER BY path",
                (connection_id,),
            ):
                path = tuple(row["path"])
                if policy.allows_container(path):
                    containers[path] = {"path": row["path"], "name": row["name"], "tables": []}
                    text_bytes += sum(len(part.encode()) for part in path) + len(row["name"].encode())
                    if text_bytes > _MAX_TEXT_BYTES:
                        raise SnapshotTooLarge("Stored index is too large to download; use stored API search instead.")
            for row in cur.execute(
                "SELECT path, container_path, name, kind FROM metadata_tables WHERE connection_id = %s ORDER BY path",
                (connection_id,),
            ):
                path, parent = tuple(row["path"]), tuple(row["container_path"])
                if parent not in containers or not policy.allows_object(path):
                    continue
                if len(tables) >= _MAX_TABLES:
                    raise SnapshotTooLarge("Stored index exceeds the table limit; use stored API search instead.")
                table: SnapshotTable = {
                    "path": row["path"], "name": row["name"], "kind": row["kind"], "columns": [],
                }
                tables[path] = table
                containers[parent]["tables"].append(table)
                text_bytes += sum(len(part.encode()) for part in path) + len(row["name"].encode())
                if text_bytes > _MAX_TEXT_BYTES:
                    raise SnapshotTooLarge("Stored index is too large to download; use stored API search instead.")

        definitions: dict[tuple[str, str], str] = {}
        allowed_paths = {".".join(path) for path in tables}
        with conn.cursor(name="metadata_snapshot_definitions", row_factory=tuple_row) as cur:
            cur.itersize = 2000
            for table_path, field_path, definition in cur.execute(
                "SELECT table_path, field_path, definition FROM data_definitions "
                "WHERE tenant_id = %s AND connection_id = %s AND status <> 'deprecated'",
                (tenant_id, connection_id),
            ):
                if table_path in allowed_paths and field_path:
                    text_bytes += len(table_path.encode()) + len(field_path.encode()) + len(definition.encode())
                    if text_bytes > _MAX_TEXT_BYTES:
                        raise SnapshotTooLarge("Stored index is too large to download; use stored API search instead.")
                    definitions[table_path, field_path] = definition

        count = 0
        with conn.cursor(name="metadata_snapshot_fields", row_factory=tuple_row) as cur:
            cur.itersize = 2000
            cur.execute(
                "SELECT table_path, path, data_type, description FROM metadata_fields "
                "WHERE connection_id = %s ORDER BY table_path, ordinal, path",
                (connection_id,),
            )
            for table_path, path, data_type, description in cur:
                target = tables.get(tuple(table_path))
                if target is None:
                    continue
                count += 1
                if count > _MAX_FIELDS:
                    raise SnapshotTooLarge("Stored index exceeds the field limit; use stored API search instead.")
                name = ".".join(path)
                definition = definitions.get((".".join(table_path), name))
                if definition:
                    description = f"{definition} — {description}" if description else definition
                text_bytes += len(name.encode()) + len(data_type.encode()) + len((description or "").encode())
                if text_bytes > _MAX_TEXT_BYTES:
                    raise SnapshotTooLarge("Stored index is too large to download; use stored API search instead.")
                target["columns"].append({"name": name, "type": data_type, "description": description})
        return {"status": status, "datasets": list(containers.values())}
