/**
 * SQL dialects and the one syntax highlighter shared by the workbench editor and the chat's SQL blocks: tags map to
 * `sqlh-*` classes, which globals.css colours with the theme's --code-* tokens.
 */
import { MySQL, PLSQL, PostgreSQL, SQLDialect, StandardSQL } from "@codemirror/lang-sql";
import { highlightCode, tagHighlighter, tags as t } from "@lezer/highlight";

export type SqlDialectId = "bigquery" | "postgres" | "mysql" | "snowflake" | "oracle" | "duckdb";

export const DIALECT_LABEL: Record<SqlDialectId, string> = {
  bigquery: "BigQuery",
  postgres: "PostgreSQL",
  mysql: "MySQL",
  snowflake: "Snowflake",
  oracle: "Oracle",
  duckdb: "DuckDB",
};

// lang-sql keeps the standard keyword list internal; PostgreSQL's full list is a good base for other dialects.
const BASE_KEYWORDS = PostgreSQL.spec.keywords ?? "";
const BASE_TYPES = PostgreSQL.spec.types ?? "";

// BigQuery: backtick-quoted identifiers (`project.dataset.table`), "strings", # comments and its common functions.
const BigQuery = SQLDialect.define({
  ...StandardSQL.spec,
  keywords:
    `${BASE_KEYWORDS} qualify struct array unnest safe_cast except replace window partition ` +
    "over rows range unbounded preceding following current tablesample system interval if ifnull",
  builtin:
    "countif safe_divide approx_count_distinct array_agg string_agg date_trunc timestamp_trunc format_date " +
    "parse_date date_diff timestamp_diff regexp_contains regexp_extract coalesce nullif trim lower upper concat " +
    "split generate_array generate_date_array current_date current_timestamp extract cast",
  types: `${BASE_TYPES} int64 float64 numeric bignumeric bool string bytes date datetime time timestamp geography json`,
  identifierQuotes: "`",
  doubleQuotedStrings: true,
  backslashEscapes: true,
  hashComments: true,
});
const Snowflake = SQLDialect.define({
  ...StandardSQL.spec,
  keywords: `${BASE_KEYWORDS} qualify ilike sample`,
  types: `${BASE_TYPES} variant object array number`,
});
const DuckDB = SQLDialect.define({ ...PostgreSQL.spec, keywords: `${PostgreSQL.spec.keywords ?? ""} qualify pivot unpivot` });

export const DIALECTS: Record<SqlDialectId, SQLDialect> = {
  bigquery: BigQuery,
  postgres: PostgreSQL,
  mysql: MySQL,
  snowflake: Snowflake,
  oracle: PLSQL,
  duckdb: DuckDB,
};

export const sqlHighlighter = tagHighlighter([
  { tag: [t.keyword, t.operatorKeyword, t.modifier], class: "sqlh-keyword" },
  { tag: t.string, class: "sqlh-string" },
  { tag: [t.number, t.bool, t.null], class: "sqlh-number" },
  { tag: [t.standard(t.name), t.function(t.variableName)], class: "sqlh-function" },
  { tag: t.typeName, class: "sqlh-type" },
  { tag: [t.lineComment, t.blockComment, t.comment], class: "sqlh-comment" },
  { tag: [t.operator, t.punctuation, t.bracket], class: "sqlh-operator" },
  // Quoted identifiers (`project.dataset.table`, "Table") and special variables read as names, not strings.
  { tag: [t.special(t.string), t.special(t.name), t.propertyName], class: "sqlh-identifier" },
  { tag: t.invalid, class: "sqlh-error" },
]);

export type SqlToken = { text: string; className: string };

/** Static highlighting (no editor): one token list per line. */
export function highlightSql(text: string, dialect: SqlDialectId = "bigquery"): SqlToken[][] {
  const lines: SqlToken[][] = [[]];
  const tree = DIALECTS[dialect].language.parser.parse(text);
  highlightCode(
    text,
    tree,
    sqlHighlighter,
    (piece, className) => lines[lines.length - 1].push({ text: piece, className }),
    () => lines.push([]),
  );
  return lines;
}
