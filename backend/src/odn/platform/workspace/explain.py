"""Deterministic, plain-English explanations of results, built only from executed SQL and recorded lineage.

No LLM is involved, so an explanation can never describe a calculation that did not run.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

_AGG_VERBS = {
    "count": "counted",
    "sum": "summed",
    "avg": "averaged",
    "min": "took the minimum of",
    "max": "took the maximum of",
    "quantile_cont": "took a percentile of",
    "median": "took the median of",
}
_MAX_ITEMS = 4


def _short(table: str) -> str:
    # Uploaded files keep their name (e.g. "counts.csv"); warehouse tables drop project and dataset.
    if table.lower().endswith((".csv", ".xlsx")):
        return table
    return table.split(".")[-1]


def _join(items: list[str]) -> str:
    items = list(dict.fromkeys(items))
    if len(items) > _MAX_ITEMS:
        items = [*items[:_MAX_ITEMS], f"{len(items) - _MAX_ITEMS} more"]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _col(node: exp.Expression) -> str:
    return node.name if isinstance(node, exp.Column) else node.sql()


def _aggregate_phrase(agg: exp.AggFunc) -> str:
    key = agg.key.lower()
    if isinstance(agg, exp.Count):
        inner = agg.this
        if isinstance(inner, exp.Distinct):
            return f"counted distinct {_join([_col(e) for e in inner.expressions])}"
        if inner is None or isinstance(inner, exp.Star | exp.Literal):
            return "counted rows"
        return f"counted {_col(inner)}"
    verb = _AGG_VERBS.get(key) or _AGG_VERBS.get(agg.sql_name().lower(), f"applied {agg.sql_name()} to")
    target = agg.this
    return f"{verb} {_col(target) if target is not None else 'values'}"


def _condition_phrase(cond: exp.Expression) -> str:
    if isinstance(cond, exp.Not) and isinstance(cond.this, exp.Is):
        return f"excluding missing {_col(cond.this.this)}"
    if isinstance(cond, exp.Is) and isinstance(cond.expression, exp.Null):
        return f"only rows where {_col(cond.this)} is missing"
    if isinstance(cond, exp.EQ | exp.NEQ | exp.GT | exp.GTE | exp.LT | exp.LTE | exp.In | exp.Like):
        return cond.sql()
    text = cond.sql()
    return text if len(text) <= 80 else text[:77] + "..."


def _conjuncts(cond: exp.Expression | None) -> list[exp.Expression]:
    if cond is None:
        return []
    if isinstance(cond, exp.And):
        return _conjuncts(cond.left) + _conjuncts(cond.right)
    return [cond.unnest()]


def _final_select(root: exp.Expression) -> exp.Select | None:
    if isinstance(root, exp.Select):
        return root
    if isinstance(root, exp.Subquery):
        return _final_select(root.this)
    return None


def explain_sql(sql: str, dialect: str, inputs: list[str]) -> str:
    """One sentence describing what the query computed from `inputs` (table names as shown to users)."""
    source = _join(inputs) if inputs else "its source"
    try:
        root = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        return f"Computed a result from {source}; the logic could not be summarized automatically (see SQL)."
    if isinstance(root, exp.SetOperation):
        return f"Combined the results of several queries over {source} (see SQL)."
    select = _final_select(root)
    if select is None:
        return f"Computed a result from {source} (see SQL)."

    aggs = [a for a in select.find_all(exp.AggFunc) if not a.find_ancestor(exp.Window, exp.Subquery)]
    windows = [w for w in select.find_all(exp.Window) if not w.find_ancestor(exp.Subquery)]
    group = select.args.get("group")
    group_cols: list[str] = []
    if group is not None:
        if group.args.get("all"):
            group_cols = [
                e.alias_or_name for e in select.expressions if not e.find(exp.AggFunc) and not e.find(exp.Window)
            ]
        else:
            group_cols = [_col(e) for e in group.expressions]

    if aggs:
        action = _join([_aggregate_phrase(a) for a in aggs]).capitalize()
    elif select.args.get("distinct"):
        action = f"Listed distinct {_join([e.alias_or_name for e in select.expressions])}"
    elif any(isinstance(e, exp.Star) for e in select.expressions):
        action = "Selected all columns"
    else:
        action = f"Selected {_join([e.alias_or_name for e in select.expressions])}"

    if len(inputs) > 1 and select.find(exp.Join):
        source = f"{inputs[0]} joined with {_join(inputs[1:])}"
    parts = [f"{action} from {source}"]
    if group_cols:
        parts.append(f"per {_join(group_cols)}")
    filters = [_condition_phrase(c) for c in _conjuncts(select.args.get("where") and select.args["where"].this)]
    if filters:
        parts.append(f"where {_join(filters)}")
    having = [_condition_phrase(c) for c in _conjuncts(select.args.get("having") and select.args["having"].this)]
    if having:
        parts.append(f"keeping groups where {_join(having)}")
    if windows:
        names = _join([w.this.sql_name().lower().replace("_", " ") for w in windows])
        parts.append(f"with {names} calculated across rows")
    if root.find(exp.CTE):
        parts.append("using intermediate steps")

    order = select.args.get("order")
    limit = select.args.get("limit")
    limit_n = limit.expression.sql() if limit is not None and limit.expression is not None else None
    if order is not None and order.expressions:
        first = order.expressions[0]
        direction = "highest first" if first.args.get("desc") else "lowest first"
        key = _col(first.this)
        parts.append(f"keeping the top {limit_n} by {key} ({direction})" if limit_n else f"sorted by {key} ({direction})")
    elif limit_n:
        parts.append(f"limited to {limit_n} rows")
    return ", ".join(parts) + "."


def explain_lineage(
    *,
    kind: str,
    source_tables: list[str],
    derived_from: list[str],
    truncated: bool,
    row_count: int,
    bytes_processed: int | None = None,
) -> str:
    sources = _join([_short(t) for t in source_tables]) if source_tables else None
    if kind == "warehouse":
        text = f"Read directly from {sources} in the warehouse" if sources else "Source tables could not be verified"
        if bytes_processed is not None:
            text += f" ({bytes_processed / 1024**3:,.2f} GB scanned)"
        text += "."
    else:
        upstream = _join(derived_from) if derived_from else "earlier results"
        text = f"Derived from {upstream} in this conversation without a new warehouse scan"
        text += f"; originally from {sources}." if sources else "; original source could not be verified."
    if truncated:
        text += f" Partial result: capped at {row_count:,} rows."
    return text


def explain_artifact(
    *,
    kind: str,
    sql: str,
    source_tables: list[str],
    derived_from: list[str],
    truncated: bool,
    row_count: int,
    bytes_processed: int | None = None,
) -> str:
    if kind == "upload":
        name = source_tables[0] if source_tables else "a file"
        return f"Imported from the uploaded file {name}: {row_count:,} rows. No warehouse scan."
    if kind == "warehouse":
        what = explain_sql(sql, "bigquery", [_short(t) for t in source_tables])
    else:
        what = explain_sql(sql, "duckdb", derived_from)
    return what + " " + explain_lineage(
        kind=kind, source_tables=source_tables, derived_from=derived_from, truncated=truncated,
        row_count=row_count, bytes_processed=bytes_processed,
    )


def explain_chart(*, chart_type: str, x: str, y: str | None, agg: str, series: str | None, sort: str,
                  limit: int, points: int, truncated: bool, source: str, source_truncated: bool) -> str:
    measure = "row count" if agg == "count" else (y or "value") if agg == "none" else f"{agg} of {y}"
    text = f"{chart_type.capitalize()} chart of {measure} by {x}"
    if series:
        text += f", split by {series}"
    text += f", from {source}"
    if sort == "y_desc":
        text += f"; the {points} highest values are shown" if truncated else "; sorted highest first"
    elif truncated:
        text += f"; only the first {points} of more points are shown"
    text += "."
    if source_truncated:
        text += f" The underlying result {source} was partial."
    return text
