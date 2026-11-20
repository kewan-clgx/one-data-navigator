import assert from "node:assert/strict";
import test from "node:test";

import { checkRequest, validBaseUrl } from "../policy.js";

const ID = "0192aaaa-0000-7000-8000-000000000001";

test("allowlisted reads become bounded REST GET paths", () => {
  assert.equal(checkRequest("users/current"), "/rest/2.0/users/current");
  assert.equal(
    checkRequest("assets", { name: "corporate revenue", nameMatchMode: "ANYWHERE", limit: 10, offset: 0 }),
    "/rest/2.0/assets?name=corporate+revenue&nameMatchMode=ANYWHERE&limit=10&offset=0",
  );
  assert.equal(checkRequest(`assets/${ID}`), `/rest/2.0/assets/${ID}`);
  assert.equal(checkRequest("attributes", { assetId: ID, limit: 100 }), `/rest/2.0/attributes?assetId=${ID}&limit=100`);
  assert.equal(checkRequest("relations", { targetId: ID, limit: 5 }), `/rest/2.0/relations?targetId=${ID}&limit=5`);
});

test("anything else is refused before a request is made", () => {
  for (const [path, params] of [
    ["workflowInstances", {}],
    ["../console/users", {}],
    ["/rest/2.0/assets", { name: "x", limit: 1 }],
    ["graphql/knowledgeGraph/v1", {}],
    ["assets", { name: "x" }], // unbounded
    ["assets", { name: "x", limit: 1000 }],
    ["assets", { name: "x", limit: 1, typeId: "y" }], // parameter not allowed
    ["assets", { name: "\u0000", limit: 1 }],
    ["attributes", { assetId: "not-a-uuid", limit: 1 }],
    ["relations", { sourceId: ID, targetId: ID, limit: 1 }],
    ["relations", { limit: 1 }],
  ]) {
    assert.throws(() => checkRequest(path, params), `${path} ${JSON.stringify(params)}`);
  }
});

test("only Collibra origins (or localhost for development)", () => {
  assert.equal(validBaseUrl("https://corelogic.collibra.com"), "https://corelogic.collibra.com");
  assert.equal(validBaseUrl("https://corelogic.collibra.com/"), "https://corelogic.collibra.com");
  assert.equal(validBaseUrl("http://localhost:8199"), "http://localhost:8199");
  for (const bad of [
    "http://corelogic.collibra.com",
    "https://collibra.com.evil.example",
    "https://evil.example/?x=.collibra.com",
    "https://corelogic.collibra.com/rest",
    "https://user:pw@corelogic.collibra.com",
    "javascript:alert(1)",
    "",
  ]) {
    assert.equal(validBaseUrl(bad), null, bad);
  }
});
