import { parse } from "smol-toml";

type ImportedSnowflake = {
  name: string;
  account: string;
  user: string;
  password: string;
  warehouse: string;
  database: string;
  schema: string;
  role: string;
  allowed: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) && !(value instanceof Date);
}

export function parseSnowflakeToml(text: string): ImportedSnowflake {
  if (new TextEncoder().encode(text).length > 100_000) throw new Error("TOML file is too large.");
  // Only normalize the opening headers, never text inside a multiline value.
  const normalized = text.replace(/^(\[connections\.[^\]\r\n]+\])\r?\n\1(?=\r?\n|$)/, "$1");
  let document: unknown;
  try {
    document = parse(normalized);
  } catch {
    throw new Error("Invalid TOML. Check the connection table and fields.");
  }
  if (!isRecord(document) || !isRecord(document.connections)) {
    throw new Error("Expected a [connections.name] table.");
  }
  const entries = Object.entries(document.connections);
  if (entries.length !== 1 || !isRecord(entries[0][1])) {
    throw new Error("Provide exactly one [connections.name] table.");
  }
  const [entryName, config] = entries[0];
  if (!isRecord(config)) throw new Error("Expected a connection table.");
  const allowedFields = new Set(["account", "user", "password", "warehouse", "database", "schema", "role"]);
  const unsupported = Object.keys(config).filter((key) => !allowedFields.has(key));
  if (unsupported.length) throw new Error("Unsupported TOML fields in connection table.");
  const required = ["account", "user", "password", "warehouse", "database", "schema"] as const;
  for (const field of required) {
    if (typeof config[field] !== "string" || !(config[field] as string).trim()) {
      throw new Error(`TOML field "${field}" must be a non-empty string.`);
    }
  }
  if (config.role !== undefined && typeof config.role !== "string") {
    throw new Error('TOML field "role" must be a string.');
  }
  return {
    name: entryName,
    account: config.account as string,
    user: config.user as string,
    password: config.password as string,
    warehouse: config.warehouse as string,
    database: config.database as string,
    schema: config.schema as string,
    role: (config.role as string | undefined) ?? "",
    allowed: `${config.database}.*.*`,
  };
}
