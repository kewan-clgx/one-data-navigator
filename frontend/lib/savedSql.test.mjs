import { strict as assert } from "node:assert";
import { test } from "node:test";
import { fileName, suggestName } from "./savedSql.ts";

test("suggests a name from table, grouping, filter value and top-N", () => {
  const sql = "SELECT naics_6_digit_primary_code, COUNT(*) n\nFROM `p.firmo.vw_businesses`\nWHERE state = 'CA'\nGROUP BY 1, naics_6_digit_primary_code ORDER BY n DESC LIMIT 10";
  assert.equal(suggestName(sql), "businesses_by_naics_6_digit_primary_ca_top10");
  assert.equal(suggestName("SELECT COUNT(*) FROM orders"), "orders_count");
  assert.equal(suggestName("SELECT 1", "p.d.my_table"), "my_table");
});

test("file names are lower-case, safe and without .sql", () => {
  assert.equal(fileName("  CA Businesses by NAICS.sql "), "ca_businesses_by_naics");
  assert.equal(fileName("***"), "");
});
