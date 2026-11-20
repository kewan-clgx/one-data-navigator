// Content script on QueryMuse pages: relays window.postMessage requests from the QueryMuse app to the extension's
// service worker and posts the answers back. Ignores messages from other windows or without the app marker.
(() => {
  const APP = "querymuse-app";
  const BRIDGE = "querymuse-collibra-bridge";

  function reply(id, payload) {
    window.postMessage({ source: BRIDGE, id, ...payload }, window.location.origin);
  }

  function announce() {
    chrome.runtime.sendMessage({ type: "info" }, (info) => {
      if (chrome.runtime.lastError || !info?.ok) return;
      window.postMessage({ source: BRIDGE, type: "ready", base_url: info.base_url, version: info.version }, window.location.origin);
    });
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== window.location.origin) return;
    const msg = event.data;
    if (!msg || msg.source !== APP) return;
    if (msg.type === "ping") return announce();
    if (msg.type !== "get" || typeof msg.id !== "string") return;
    chrome.runtime.sendMessage({ type: "get", path: msg.path, params: msg.params, base_url: msg.base_url }, (result) => {
      if (chrome.runtime.lastError) {
        reply(msg.id, { type: "result", result: { ok: false, error: "bridge_error", message: "The Collibra bridge extension did not respond; reload the page." } });
        return;
      }
      reply(msg.id, { type: "result", result });
    });
  });

  announce();
})();
