"""Build BigQuery aggregate-only profiling SQL from the source's structured schema."""

from __future__ import annotations

from dataclasses import dataclass

from google.cloud.bigquery import SchemaField

_RANGE_TYPES = frozenset({
    "INTEGER", "INT64", "FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC",
    "DATE", "DATETIME", "TIME", "TIMESTAMP",
})


@dataclass(frozen=True)
class ProfileField:
    path: tuple[str, ...]
    data_type: str
    has_range: bool
    schema_signature: tuple[str, ...]


def _identifier(name: str) -> str:
    if not name or any(ord(char) < 32 for char in name):
        raise ValueError("invalid BigQuery identifier")
    return "`" + name.replace("\\", "\\\\").replace("`", "\\`") + "`"


def profile_sql(path: tuple[str, ...], schema: list[SchemaField]) -> tuple[str, list[ProfileField]]:
    """Return one sampled parent-row aggregate, including reachable nested/repeated fields."""
    if len(path) != 3 or any(not part or any(c in part for c in ".`\\") for part in path):
        raise ValueError("unsupported BigQuery table identifier")
    table = _identifier(".".join(path))
    fields: list[ProfileField] = []
    expressions: list[str] = []

    def visit(
        field: SchemaField, parent_expr: str, parent_path: tuple[str, ...], sources: tuple[str, ...],
        parent_signature: tuple[str, ...],
    ) -> None:
        if field.policy_tags and field.policy_tags.names:
            raise ValueError("policy-tagged columns are not supported")
        field_path = (*parent_path, field.name)
        expr = f"{parent_expr}.{_identifier(field.name)}"
        unnests = sources
        if field.mode == "REPEATED":
            alias = f"element_{len(sources)}"
            unnests = (*sources, f"UNNEST({expr}) AS {alias}")
            expr = alias

        dtype = field.field_type.upper()
        signature = (*parent_signature, f"{dtype}:{field.mode}")
        has_range = dtype in _RANGE_TYPES
        n = len(fields)
        fields.append(ProfileField(field_path, dtype, has_range, signature))
        condition = f"{expr} IS NOT NULL"
        if dtype == "STRING":
            condition += f" AND TRIM({expr}) != ''"
        if unnests:
            source = ", ".join(unnests)
            filled = f"EXISTS (SELECT 1 FROM {source} WHERE {condition})"
        else:
            filled = f"({condition})"
        expressions.append(f"COUNTIF({filled}) AS filled_{n}")

        if has_range:
            value = f"IF(IS_NAN({expr}) OR IS_INF({expr}), NULL, {expr})" if dtype in ("FLOAT", "FLOAT64") else expr
            for fn in ("MIN", "MAX"):
                inner = f"(SELECT {fn}({value}) FROM {source})" if unnests else value
                expressions.append(f"CAST({fn}({inner}) AS STRING) AS {fn.lower()}_{n}")
        for child in field.fields:
            visit(child, expr, field_path, unnests, signature)

    for field in schema:
        visit(field, "t", (), (), ())
    if not fields or len(fields) > 10_000:
        raise ValueError("unsupported BigQuery schema size")
    query = "SELECT COUNT(*) AS sample_rows, " + ", ".join(expressions) + f" FROM {table} AS t WHERE RAND() < 0.1"
    if len(query.encode()) > 900_000:
        raise ValueError("BigQuery profiling query exceeds the size limit")
    return query, fields
