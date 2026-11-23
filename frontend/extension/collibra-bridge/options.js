import { DEFAULT_BASE_URL, validBaseUrl } from "./policy.js";

const input = document.getElementById("base");
const status = document.getElementById("status");

chrome.storage.sync.get("baseUrl").then(({ baseUrl }) => {
  input.value = baseUrl || DEFAULT_BASE_URL;
});

document.getElementById("save").addEventListener("click", async () => {
  const origin = validBaseUrl(input.value.trim());
  if (!origin) {
    status.textContent = "Use https://<name>.collibra.com (or http://localhost for development).";
    return;
  }
  if (origin.startsWith("http://")) {
    const granted = await chrome.permissions.request({ origins: [`${origin}/*`] });
    if (!granted) {
      status.textContent = "Permission for that address was not granted.";
      return;
    }
  }
  await chrome.storage.sync.set({ baseUrl: origin });
  input.value = origin;
  status.textContent = "Saved. Reload EasyQuery to use it.";
});

const details = document.getElementById("details");
document.getElementById("test").addEventListener("click", async () => {
  status.textContent = "Testing…";
  details.style.display = "none";
  const result = await chrome.runtime.sendMessage({ type: "get", path: "users/current", params: {} });
  if (result?.ok) {
    status.textContent = `Connected to ${result.base_url} as ${result.data?.userName ?? "(unknown user)"}.`;
    return;
  }
  status.textContent = result?.message ?? "No answer from the extension.";
  details.textContent = JSON.stringify({ error: result?.error, reason: result?.reason, ...(result?.diagnostics ?? {}) }, null, 2);
  details.style.display = "block";
});
