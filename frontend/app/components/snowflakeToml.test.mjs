import { strict as assert } from "node:assert";
import { test } from "node:test";
import { parseSnowflakeToml } from "./snowflakeToml.ts";

test("imports a password connection and scopes the allowlist", () => {
  const parsed = parseSnowflakeToml(`[connections.example]
[connections.example]
account = "org-account"
user = "READER"
password = "a=secret"
role = "READER_ROLE"
warehouse = "WH"
database = "DB"
schema = "PUBLIC"
`);
  assert.deepEqual(parsed, {
    name: "example", account: "org-account", user: "READER", password: "a=secret",
    role: "READER_ROLE", warehouse: "WH", database: "DB", schema: "PUBLIC", allowed: "DB.*.*",
  });
});

test("rejects malformed, unsupported and multiple connections without exposing input", () => {
  assert.throws(() => parseSnowflakeToml("password = 'secret'"), /connections.name/);
  assert.throws(() => parseSnowflakeToml("[connections.a]\npassword = 'secret'\nauthenticator = 'externalbrowser'"),
    /Unsupported TOML fields/);
  assert.throws(() => parseSnowflakeToml("[connections.a]\nx=1\n[connections.b]\nx=2"), /exactly one/);
  assert.throws(() => parseSnowflakeToml("[connections.a]\npassword = 'secret"), (err) =>
    !err.message.includes("secret"));
  assert.throws(() => parseSnowflakeToml("[connections.a]\nsecret_key = 'value'"), (err) =>
    !err.message.includes("secret_key"));
});

test("preserves repeated header text inside a multiline password", () => {
  const parsed = parseSnowflakeToml(`[connections.example]
account = "org-account"
user = "READER"
password = '''
[connections.example]
[connections.example]
'''
warehouse = "WH"
database = "DB"
schema = "PUBLIC"
`);
  assert.equal(parsed.password, "[connections.example]\n[connections.example]\n");
});
