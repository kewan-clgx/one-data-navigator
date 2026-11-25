/** Finds the tables a SQL statement reads, so chat SQL can open in the workbench on the right connection. */

const IDENT = String.raw`(?:\`[^\`]+\`|"[^"]+"|\[[^\]]+\]|[A-Za-z0-9_$\-]+)`;
const NAME = new RegExp(String.raw`\b(?:from|join)\s+(${IDENT}(?:\s*\.\s*${IDENT})*)`, "gi");
const CTE = /\b([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(/gi;
const WORKSPACE_TABLE = /^q\d+$/i;

function unquote(part: string): string {
  return part.trim().replace(/^[`"[]|[`"\]]$/g, "");
}

/** Table paths referenced by FROM/JOIN, in order and without duplicates; CTE names are skipped. */
export function tablesIn(sql: string): string[][] {
  const text = sql.replace(/--[^\n]*/g, " ").replace(/\/\*[\s\S]*?\*\//g, " ");
  const ctes = new Set([...text.matchAll(CTE)].map((m) => m[1].toLowerCase()));
  const seen = new Set<string>();
  const out: string[][] = [];
  for (const m of text.matchAll(NAME)) {
    // A backticked BigQuery name may hold the whole path: `project.dataset.table`.
    const path = m[1].split(/\s*\.\s*(?=(?:[^`]*`[^`]*`)*[^`]*$)/).flatMap((p) => unquote(p).split(".")).filter(Boolean);
    if (!path.length || path[0] === "(" || (path.length === 1 && ctes.has(path[0].toLowerCase()))) continue;
    const key = path.join(".").toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      out.push(path);
    }
  }
  return out;
}

/** True when the SQL only reads this conversation's workspace results (q1, q2, ...): DuckDB, no warehouse. */
export function isWorkspaceSql(tables: string[][]): boolean {
  return tables.length > 0 && tables.every((t) => t.length === 1 && WORKSPACE_TABLE.test(t[0]));
}

function covers(pattern: string, path: string[]): boolean {
  const parts = pattern.toLowerCase().split(".");
  const target = path.map((p) => p.toLowerCase());
  // Patterns are fully qualified; a shorter table name in the SQL is compared from the end.
  const offset = Math.max(0, parts.length - target.length);
  return parts.slice(offset).every((p, i) => p === "*" || p === target[i]) || parts.every((p, i) => p === "*" || p === target[i]);
}

export type MatchableConnection = { id: string; allowed_objects: string[] };

/** The connection whose allowlist covers the most of these tables; ties keep the current connection. */
export function connectionFor(tables: string[][], connections: MatchableConnection[], currentId: string): string {
  let best = currentId;
  let bestScore = -1;
  for (const c of connections) {
    const score = tables.filter((t) => c.allowed_objects.some((p) => covers(p, t))).length;
    if (score > bestScore || (score === bestScore && c.id === currentId)) {
      best = c.id;
      bestScore = score;
    }
  }
  return bestScore > 0 ? best : currentId || best;
}
