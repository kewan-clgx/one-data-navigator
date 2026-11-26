import { format, type SqlLanguage } from "sql-formatter";

import type { SqlDialectId } from "../app/components/SqlEditor";

const LANGUAGE: Record<SqlDialectId, SqlLanguage> = {
  bigquery: "bigquery",
  postgres: "postgresql",
  mysql: "mysql",
  snowflake: "snowflake",
  oracle: "plsql",
  duckdb: "duckdb",
};

/** Formats SQL for the dialect (upper-case keywords, 2-space indent). Throws on SQL the formatter can't parse. */
export function formatSql(text: string, dialect: SqlDialectId): string {
  return format(text, {
    language: LANGUAGE[dialect],
    keywordCase: "upper",
    functionCase: "upper",
    dataTypeCase: "upper",
    tabWidth: 2,
    linesBetweenQueries: 2,
  });
}

/** Editor dialect for a connection kind; workspace SQL (q1, q2, …) runs on DuckDB. */
export function dialectFor(kind: string | undefined, workspace = false): SqlDialectId {
  if (workspace) return "duckdb";
  switch (kind) {
    case "postgres":
      return "postgres";
    case "mysql":
      return "mysql";
    case "snowflake":
      return "snowflake";
    case "oracle":
      return "oracle";
    default:
      return "bigquery";
  }
}
