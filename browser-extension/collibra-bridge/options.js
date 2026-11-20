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
  status.textContent = "Saved. Reload QueryMuse to use it.";
});
