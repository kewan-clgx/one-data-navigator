"""Fail-closed SQL policy: single read-only query over allowlisted objects only.

Defense in depth: connectors must also use least-privilege, read-only credentials/transactions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

Dialect = Literal["bigquery", "postgres", "snowflake", "mysql", "oracle", "duckdb"]

MAX_SQL_LENGTH = 100_000

# Number of name parts in a fully qualified table for each dialect.
_QUALIFIED_PARTS: dict[str, int] = {
    "bigquery": 3, "postgres": 2, "snowflake": 3, "mysql": 2, "oracle": 2, "duckdb": 1,
}
_ORACLE_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_$]{0,127}")
_ORACLE_SYSTEM_SCHEMAS = frozenset({
    "SYS", "SYSTEM", "OUTLN", "XDB", "DBSNMP", "CTXSYS", "MDSYS", "ORDSYS", "ORDDATA",
    "OLAPSYS", "WMSYS", "LBACSYS", "DVSYS", "DVF", "AUDSYS", "GSMADMIN_INTERNAL",
    "APPQOSSYS", "OJVMSYS", "REMOTE_SCHEDULER_AGENT", "ANONYMOUS",
})
_SNOWFLAKE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]{0,254}")
_MYSQL_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]{0,63}")
_MYSQL_SYSTEM_DATABASES = frozenset({"information_schema", "performance_schema", "mysql", "sys"})
_MYSQL_FORBIDDEN_FUNCTIONS = frozenset({
    "sleep", "benchmark", "load_file", "get_lock", "release_lock", "release_all_locks",
    "is_free_lock", "is_used_lock", "master_pos_wait", "source_pos_wait", "wait_for_executed_gtid_set",
    "last_insert_id",
})

_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Into,
    exp.Lock,
    exp.Copy,
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Grant,
    exp.Revoke,
    exp.Use,
    exp.Pragma,
    exp.Attach,
    exp.Detach,
    exp.Install,
    exp.Declare,
    exp.Execute,
    exp.ExecuteSql,
    exp.Block,
    exp.Cache,
    exp.LoadData,
    exp.Export,
    exp.Analyze,
    exp.Kill,
    exp.Refresh,
    exp.Comment,
    exp.Show,
    exp.Describe,
    exp.Summarize,
    exp.ReadCSV,
    exp.ReadParquet,
)

_FORBIDDEN_FUNCTIONS: frozenset[str] = frozenset(
    {
        # BigQuery
        "external_query",
        # Postgres: file/system access, side effects, dynamic SQL, DoS
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_advisory_lock",
        "pg_advisory_xact_lock",
        "pg_try_advisory_lock",
        "lo_import",
        "lo_export",
        "lo_get",
        "set_config",
        "current_setting",
        "nextval",
        "setval",
        "query_to_xml",
        "query_to_xml_and_xmlschema",
        "query_to_xmlschema",
        "cursor_to_xml",
        "table_to_xml",
        "schema_to_xml",
        "database_to_xml",
        "txid_current",
        # DuckDB: filesystem / network / catalog access
        "read_csv",
        "read_csv_auto",
        "read_parquet",
        "parquet_scan",
        "read_json",
        "read_json_auto",
        "read_ndjson",
        "read_ndjson_auto",
        "read_text",
        "read_blob",
        "glob",
        "sniff_csv",
        "getenv",
        "duckdb_settings",
        "duckdb_secrets",
    }
)
_FORBIDDEN_FUNCTION_PREFIXES: tuple[str, ...] = ("dblink", "pg_", "duckdb_")

_SYSTEM_SCHEMAS: frozenset[str] = frozenset({"information_schema", "pg_catalog", "pg_toast"})


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None
    tables: tuple[str, ...] = field(default_factory=tuple)


class PolicyViolation(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SqlPolicy:
    """Validates one SQL statement for a connection.

    `allowed_objects` entries are dotted, fully qualified patterns with the dialect's part
    count; each part is an exact name or `*`. Examples: `proj.sales.*`, `sales.orders`.
    `default_namespace` fills missing leading parts (e.g. `("proj", "sales")` or `("public",)`).
    Snowflake, MySQL and Oracle patterns use exact catalog spelling.
    """

    dialect: Dialect
    allowed_objects: frozenset[str]
    default_namespace: tuple[str, ...] = ()

    @property
    def case_sensitive(self) -> bool:
        return self.dialect in ("snowflake", "mysql", "oracle")

    def validate(self, sql: str) -> PolicyDecision:
        try:
            tables = self._check(sql)
        except PolicyViolation as violation:
            return PolicyDecision(allowed=False, reason=violation.reason)
        except Exception as exc:  # noqa: BLE001 - fail closed on any unexpected analyzer error
            return PolicyDecision(allowed=False, reason=f"policy analysis failed: {type(exc).__name__}")
        return PolicyDecision(allowed=True, tables=tables)

    def _check(self, sql: str) -> tuple[str, ...]:
        if not sql or not sql.strip():
            raise PolicyViolation("empty statement")
        if len(sql) > MAX_SQL_LENGTH:
            raise PolicyViolation("statement too long")
        if self.dialect == "mysql" and re.search(r"/\*\s*(?:!|\+|M!)", sql, re.IGNORECASE):
            raise PolicyViolation("MySQL executable comments and optimizer hints are not allowed")
        if self.dialect == "oracle" and re.search(r"/\*\s*\+|--\s*\+", sql):
            raise PolicyViolation("Oracle optimizer hints are not allowed")

        try:
            statements = [s for s in sqlglot.parse(sql, read=self.dialect) if s is not None]
        except SqlglotError:
            raise PolicyViolation("statement could not be parsed") from None
        if len(statements) != 1:
            raise PolicyViolation("exactly one statement is allowed")
        root = statements[0]

        if not isinstance(root, exp.Query):
            raise PolicyViolation(f"only read-only queries are allowed (got {root.key.upper()})")

        for node in root.walk():
            if isinstance(node, _FORBIDDEN_NODES):
                raise PolicyViolation(f"forbidden construct: {node.key.upper()}")
            if self.dialect == "mysql" and isinstance(node, (exp.Parameter, exp.SessionParameter, exp.PropertyEQ)):
                raise PolicyViolation("MySQL variables and assignments are not allowed")
            if self.dialect == "oracle" and (
                isinstance(node, (exp.Parameter, exp.Placeholder))
                or isinstance(node, exp.Column) and node.name.upper() in ("NEXTVAL", "CURRVAL")
            ):
                raise PolicyViolation("Oracle parameters and sequence access are not allowed")
            if isinstance(node, exp.Func):
                name = self._function_name(node)
                if name in _FORBIDDEN_FUNCTIONS or name.startswith(_FORBIDDEN_FUNCTION_PREFIXES):
                    raise PolicyViolation(f"forbidden function: {name}")
                if self.dialect == "snowflake" and (
                    isinstance(node, exp.Anonymous) or isinstance(node.parent, exp.Dot)
                ):
                    raise PolicyViolation(f"forbidden Snowflake function: {name}")
                if self.dialect == "mysql" and (
                    name in _MYSQL_FORBIDDEN_FUNCTIONS
                    or isinstance(node, exp.Anonymous) or isinstance(node.parent, exp.Dot)
                ):
                    raise PolicyViolation(f"forbidden MySQL function: {name}")
                if self.dialect == "oracle" and (
                    isinstance(node, exp.Anonymous) or isinstance(node.parent, exp.Dot)
                ):
                    raise PolicyViolation(f"forbidden Oracle function: {name}")

        cte_refs = self._cte_reference_ids(root)
        referenced: list[str] = []
        for table in root.find_all(exp.Table):
            if id(table) in cte_refs:
                continue
            referenced.append(self._check_table(table))
        return tuple(dict.fromkeys(referenced))

    @staticmethod
    def _function_name(node: exp.Func) -> str:
        if isinstance(node, exp.Anonymous):
            return str(node.name).lower()
        return node.sql_name().lower()

    @staticmethod
    def _cte_reference_ids(root: exp.Expression) -> set[int]:
        ids: set[int] = set()
        for scope in traverse_scope(root):
            for name, selected in scope.selected_sources.items():
                node, source = selected
                if isinstance(source, Scope) and isinstance(node, exp.Table) and name in scope.cte_sources:
                    ids.add(id(node))
        return ids

    def _check_table(self, table: exp.Table) -> str:
        if not isinstance(table.this, exp.Identifier):
            raise PolicyViolation("table functions are not allowed")
        if self.dialect in ("snowflake", "oracle"):
            parts = [
                str(p.name) if p.args.get("quoted") else str(p.name).upper()
                for p in table.parts
            ]
            identifier = _ORACLE_IDENTIFIER if self.dialect == "oracle" else _SNOWFLAKE_IDENTIFIER
            if any(not identifier.fullmatch(p) for p in parts):
                raise PolicyViolation(f"unsupported {self.dialect} identifier")
        elif self.dialect == "mysql":
            parts = [str(p.name) for p in table.parts]
            if any(not _MYSQL_IDENTIFIER.fullmatch(p) for p in parts):
                raise PolicyViolation("unsupported MySQL identifier")
        else:
            parts = [str(p.name).lower() for p in table.parts]
        if any(not p for p in parts):
            raise PolicyViolation("empty table identifier")
        if any(schema in p.lower() for p in parts for schema in _SYSTEM_SCHEMAS):
            raise PolicyViolation("system catalog access is not allowed")
        if self.dialect == "postgres" and parts[-1].startswith("pg_"):
            raise PolicyViolation("system catalog access is not allowed")
        if self.dialect == "mysql" and len(parts) > 1 and parts[0].lower() in _MYSQL_SYSTEM_DATABASES:
            raise PolicyViolation("system catalog access is not allowed")
        if any("." in p for p in parts):
            raise PolicyViolation("ambiguous table identifier")

        expected = _QUALIFIED_PARTS[self.dialect]
        if len(parts) > expected:
            raise PolicyViolation(f"table reference has too many parts: {'.'.join(parts)}")
        missing = expected - len(parts)
        if missing:
            defaults = list(self.default_namespace) if self.case_sensitive else [
                p.lower() for p in self.default_namespace
            ]
            if len(defaults) < missing:
                raise PolicyViolation(f"table reference is not fully qualified: {'.'.join(parts)}")
            parts = defaults[:missing] + parts
        if self.dialect == "oracle" and (
            parts[0].upper() in _ORACLE_SYSTEM_SCHEMAS or parts[-1].upper() == "DUAL"
        ):
            raise PolicyViolation("Oracle system objects are not allowed")

        if not any(self._matches(pattern, parts) for pattern in self.allowed_objects):
            raise PolicyViolation(f"object not in allowlist: {'.'.join(parts)}")
        return ".".join(parts)

    def _matches(self, pattern: str, parts: list[str]) -> bool:
        pattern_parts = (pattern if self.case_sensitive else pattern.lower()).split(".")
        if len(pattern_parts) != len(parts):
            return False
        return all(pp == "*" or pp == p for pp, p in zip(pattern_parts, parts, strict=True))

    def allows_object(self, path: tuple[str, ...]) -> bool:
        """Whether a fully qualified object (e.g. a catalog table) is in the allowlist."""
        if self.dialect == "oracle" and (
            len(path) != 2 or path[0].upper() in _ORACLE_SYSTEM_SCHEMAS or path[-1].upper() == "DUAL"
            or any(not _ORACLE_IDENTIFIER.fullmatch(p) for p in path)
        ):
            return False
        if self.dialect == "mysql" and (
            len(path) != 2 or path[0].lower() in _MYSQL_SYSTEM_DATABASES
            or any(not _MYSQL_IDENTIFIER.fullmatch(p) for p in path)
        ):
            return False
        if self.dialect == "snowflake" and any(
            not _SNOWFLAKE_IDENTIFIER.fullmatch(p) or any(s in p.lower() for s in _SYSTEM_SCHEMAS)
            for p in path
        ):
            return False
        parts = list(path) if self.case_sensitive else [p.lower() for p in path]
        return any(self._matches(pattern, parts) for pattern in self.allowed_objects)

    def allows_container(self, prefix: tuple[str, ...]) -> bool:
        """Whether any allowlisted object can live under this prefix (e.g. a dataset)."""
        if self.dialect == "oracle" and (
            len(prefix) != 1 or prefix[0].upper() in _ORACLE_SYSTEM_SCHEMAS
            or any(not _ORACLE_IDENTIFIER.fullmatch(p) for p in prefix)
        ):
            return False
        if self.dialect == "mysql" and (
            len(prefix) != 1 or prefix[0].lower() in _MYSQL_SYSTEM_DATABASES
            or any(not _MYSQL_IDENTIFIER.fullmatch(p) for p in prefix)
        ):
            return False
        if self.dialect == "snowflake" and any(
            not _SNOWFLAKE_IDENTIFIER.fullmatch(p) or any(s in p.lower() for s in _SYSTEM_SCHEMAS)
            for p in prefix
        ):
            return False
        parts = list(prefix) if self.case_sensitive else [p.lower() for p in prefix]
        return any(self._matches(".".join(pattern.split(".")[: len(parts)]), parts) for pattern in self.allowed_objects)

    @staticmethod
    def validate_pattern(dialect: Dialect, pattern: str) -> None:
        parts = pattern.split(".")
        if len(parts) != _QUALIFIED_PARTS[dialect] or any(not p or p.strip() != p for p in parts):
            raise ValueError(f"allowlist pattern must have {_QUALIFIED_PARTS[dialect]} dotted parts: {pattern!r}")
        if dialect == "snowflake" and any(
            p != "*" and not _SNOWFLAKE_IDENTIFIER.fullmatch(p) for p in parts
        ):
            raise ValueError("Snowflake allowlist parts must be simple identifiers or *")
        if dialect == "mysql" and (
            any(p != "*" and not _MYSQL_IDENTIFIER.fullmatch(p) for p in parts)
            or parts[0].lower() in _MYSQL_SYSTEM_DATABASES
        ):
            raise ValueError("MySQL allowlist parts must be simple identifiers or * and cannot name system databases")
        if dialect == "oracle" and (
            any(p != "*" and not _ORACLE_IDENTIFIER.fullmatch(p) for p in parts)
            or parts[0].upper() in _ORACLE_SYSTEM_SCHEMAS or parts[-1].upper() == "DUAL"
        ):
            raise ValueError("Oracle allowlist parts must be simple exact-case identifiers or *, not system objects")
