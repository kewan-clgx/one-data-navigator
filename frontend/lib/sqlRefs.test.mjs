import { strict as assert } from "node:assert";
import { test } from "node:test";
import { connectionFor, isWorkspaceSql, tablesIn } from "./sqlRefs.ts";

const JOIN_SQL = `SELECT p.name, SUM(oi.sale_price)
FROM \`datahop-514819.easyquery_demo.order_items\` oi
JOIN \`datahop-514819.easyquery_demo.products\` p ON oi.product_id = p.product_id
GROUP BY 1`;

test("finds every table in a join, without duplicates", () => {
  assert.deepEqual(tablesIn(JOIN_SQL + "\nUNION ALL SELECT 1 FROM `datahop-514819.easyquery_demo.products`"), [
    ["datahop-514819", "easyquery_demo", "order_items"],
    ["datahop-514819", "easyquery_demo", "products"],
  ]);
});

test("skips CTE names and subqueries, keeps quoted names", () => {
  const sql = `WITH recent AS (SELECT * FROM "SALES"."PUBLIC"."ORDERS") SELECT * FROM recent JOIN (SELECT 1) x ON true`;
  assert.deepEqual(tablesIn(sql), [["SALES", "PUBLIC", "ORDERS"]]);
});

test("workspace SQL reads only q1, q2, ...", () => {
  assert.equal(isWorkspaceSql(tablesIn("SELECT * FROM q1 JOIN q12 USING (id)")), true);
  assert.equal(isWorkspaceSql(tablesIn(JOIN_SQL)), false);
  assert.equal(isWorkspaceSql([]), false);
});

test("picks the connection whose allowlist covers the tables", () => {
  const connections = [
    { id: "pg", allowed_objects: ["shop.public.*"] },
    { id: "bq", allowed_objects: ["datahop-514819.easyquery_demo.*"] },
  ];
  assert.equal(connectionFor(tablesIn(JOIN_SQL), connections, "pg"), "bq");
  assert.equal(connectionFor([["unknown", "t"]], connections, "pg"), "pg");
  assert.equal(connectionFor([["public", "orders"]], connections, "bq"), "pg");
});
