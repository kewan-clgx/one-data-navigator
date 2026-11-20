"use client";

/**
 * Collibra in chat (docs/collibra_int_phase5_plan.md). Tells the agent which Collibra environments the organization
 * configured (admin setting) and whether this browser has the QueryMuse Collibra Bridge, and registers the
 * collibra_browser_* tools (AG-UI frontend tools, run in this browser with the user's own Collibra session) for
 * environments in browser mode. Token-mode environments use the server-side collibra_* tools.
 */

import { useCopilotReadable, useFrontendTool } from "@copilotkit/react-core";

import {
  bridgeOutdated,
  CollibraSource,
  EXTENSION_DOWNLOAD_URL,
  extensionSteps,
  type BridgeInfo,
  type CollibraEnv,
  type Json,
} from "@/lib/collibra";

export type CollibraScope = { env: string; id: string; name: string; type: string | null; url: string | null };

async function run(fn: () => Promise<Json>): Promise<Json> {
  try {
    return await fn();
  } catch (e) {
    return { error: (e as Error).message };
  }
}

function contextText(envs: CollibraEnv[] | null, bridge: BridgeInfo): string {
  if (!envs?.length) {
    return "Collibra is not set up for this organization. An admin can add it in Settings → Collibra.";
  }
  const list = envs.map((e) => `${e.env} (${e.base_url}, ${e.access_mode === "token" ? "server tools collibra_*" : "browser tools collibra_browser_*"})`);
  const lines = [`Collibra environments configured by the organization: ${list.join("; ")}.`];
  const browser = envs.filter((e) => e.access_mode === "browser");
  if (browser.length) {
    const download = `${window.location.origin}${EXTENSION_DOWNLOAD_URL}`;
    if (!bridge || bridgeOutdated(bridge)) {
      lines.push(
        `${bridge ? `The installed QueryMuse Collibra Bridge (v${bridge.version}) is outdated` : "The QueryMuse Collibra Bridge browser extension is NOT installed"}, ` +
          "so browser-mode Collibra is unavailable. If the user asks about Collibra, give these steps briefly " +
          `(download link: ${download}; also in Settings → Collibra): ` +
          extensionSteps(browser[0]).map((s, i) => `${i + 1}. ${s}`).join(" "),
      );
    } else {
      lines.push(`The QueryMuse Collibra Bridge v${bridge.version} is installed: collibra_browser_* tools read Collibra with the user's own session.`);
    }
  }
  return lines.join(" ");
}

/** Registers Collibra context and browser tools; renders nothing. */
export function CollibraBrowserTools({
  envs,
  bridge,
  scope,
}: {
  envs: CollibraEnv[] | null;
  bridge: BridgeInfo;
  scope: CollibraScope | null;
}) {
  const browserEnvs = (envs ?? []).filter((e) => e.access_mode === "browser");
  const available = browserEnvs.length > 0 && bridge !== null && !bridgeOutdated(bridge) ? "enabled" : "disabled";
  const names = browserEnvs.map((e) => e.env).join(", ");

  useCopilotReadable({ description: "Collibra data catalog setup for this user", value: contextText(envs, bridge) });
  useCopilotReadable({
    description: "Collibra asset the user selected in the Collibra tab, if any",
    value: scope
      ? `Selected Collibra asset "${scope.name}" (${scope.type ?? "asset"}, id ${scope.id}, environment ${scope.env}` +
        `${scope.url ? `, ${scope.url}` : ""}). Answer about this asset unless the user asks about something else; ` +
        "use the Collibra tools with this id."
      : "No Collibra asset is selected.",
  });

  const source = (environment?: string): CollibraSource => {
    const env = browserEnvs.find((e) => e.env === (environment ?? "").trim().toLowerCase()) ?? browserEnvs[0];
    if (!env) throw new Error("No Collibra environment in browser mode is configured.");
    return new CollibraSource(env);
  };
  const envParam = {
    name: "environment",
    type: "string" as const,
    description: `Collibra environment (${names || "none"}); defaults to the first`,
    required: false,
  };
  const deps = [available, names];

  useFrontendTool(
    {
      name: "collibra_browser_status",
      description: "Checks whether the user is signed in to Collibra in this browser (QueryMuse Collibra Bridge).",
      available,
      parameters: [envParam],
      handler: async ({ environment }: { environment?: string }) => run(() => source(environment).status()),
    },
    deps,
  );

  useFrontendTool(
    {
      name: "collibra_browser_lookup",
      description:
        "Best first call for Collibra questions: searches, ranks (business terms / accepted assets first) and returns the " +
        "top matches with definitions and related assets in one step, using the user's own Collibra session.",
      available,
      parameters: [
        envParam,
        { name: "text", type: "string", description: "What to look up, e.g. corporate revenue", required: true },
        { name: "top", type: "number", description: "How many matches to describe, 1-10 (default 5)", required: false },
      ],
      handler: async (a: { environment?: string; text: string; top?: number }) =>
        run(() => source(a.environment).lookup(String(a.text), a.top)),
    },
    deps,
  );

  useFrontendTool(
    {
      name: "collibra_browser_search_assets",
      description:
        "Searches Collibra assets by name with the user's own Collibra session. Use short name fragments and synonyms. " +
        "Returns results (id, name, display_name, type, domain, status, url) and paging fields.",
      available,
      parameters: [
        envParam,
        { name: "text", type: "string", description: "Name text to search for", required: true },
        { name: "match", type: "string", description: "ANYWHERE (default), START or EXACT", required: false },
        { name: "limit", type: "number", description: "Results per page, 1-50 (default 10)", required: false },
        { name: "offset", type: "number", description: "Paging offset (next_offset of the previous page)", required: false },
      ],
      handler: async (a: { environment?: string; text: string; match?: string; limit?: number; offset?: number }) =>
        run(() => source(a.environment).search(String(a.text), a.match, a.limit, a.offset)),
    },
    deps,
  );

  useFrontendTool(
    {
      name: "collibra_browser_describe_assets",
      description:
        "Reads definitions and attributes of up to 20 Collibra assets (ids from search or relations) with the user's own " +
        "Collibra session. Returns assets with definition (plain text) and attributes, or a per-asset error.",
      available,
      parameters: [envParam, { name: "asset_ids", type: "string[]", description: "1-20 Collibra asset ids", required: true }],
      handler: async ({ environment, asset_ids }: { environment?: string; asset_ids: string[] }) =>
        run(async () => ((asset_ids ?? []).length ? source(environment).describe(asset_ids) : { error: "give 1-20 asset ids" })),
    },
    deps,
  );

  useFrontendTool(
    {
      name: "collibra_browser_get_relations",
      description:
        "Follows Collibra relations (lineage, term-to-column, table-to-column) of one asset with the user's own Collibra " +
        "session. direction: outgoing (asset is the source) or incoming.",
      available,
      parameters: [
        envParam,
        { name: "asset_id", type: "string", description: "The asset id", required: true },
        { name: "direction", type: "string", description: "outgoing (default) or incoming", required: false },
        { name: "limit", type: "number", description: "Results per page, 1-100 (default 20)", required: false },
        { name: "offset", type: "number", description: "Paging offset", required: false },
      ],
      handler: async (a: { environment?: string; asset_id: string; direction?: string; limit?: number; offset?: number }) =>
        run(() => source(a.environment).relations(String(a.asset_id), a.direction, a.limit, a.offset)),
    },
    deps,
  );

  useFrontendTool(
    {
      name: "collibra_browser_get_api_fields",
      description:
        "Lists the fields linked from a Collibra table/API asset with each field's definition (one page), using the user's " +
        "own Collibra session. Relations are catalog candidates, not proof of schema membership.",
      available,
      parameters: [
        envParam,
        { name: "asset_id", type: "string", description: "The table or API asset id", required: true },
        { name: "limit", type: "number", description: "Fields per page, 1-20 (default 20)", required: false },
        { name: "offset", type: "number", description: "Paging offset", required: false },
      ],
      handler: async (a: { environment?: string; asset_id: string; limit?: number; offset?: number }) =>
        run(() => source(a.environment).fields(String(a.asset_id), a.limit, a.offset)),
    },
    deps,
  );

  return null;
}
