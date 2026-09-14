const BASE = "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail || `${res.status} ${res.statusText}`);
    err.status = res.status; // lets callers distinguish e.g. 404 ("doesn't exist yet") from real failures
    throw err;
  }
  if (res.status === 204) return null; // no body -- DELETE endpoints, e.g. -- don't try to parse it
  const contentType = res.headers.get("content-type") || "";
  return contentType.includes("application/json") ? res.json() : res.text();
}

export const api = {
  // devices
  listDevices: () => request("/devices"),

  // serial ports (Configure Roast form's port-picker convenience list)
  listSerialPorts: () => request("/serial-ports"),

  // roasts
  listRoasts: (params = {}) => request(`/roasts?${new URLSearchParams(params)}`),
  createRoast: (payload) => request("/roasts", { method: "POST", body: JSON.stringify(payload) }),
  // modbus_live/ms6514_live only -- START, once already connected via
  // createRoast above (which, for those two modes, only connects/ARMs;
  // see backend/app/api/roasts.py's create_roast docstring).
  beginRecording: (id) => request(`/roasts/${id}/start`, { method: "POST" }),
  getRoast: (id) => request(`/roasts/${id}`),
  deleteRoast: (id) => request(`/roasts/${id}`, { method: "DELETE" }),
  stopRoast: (id) => request(`/roasts/${id}/stop`, { method: "POST" }),
  sendCommand: (id, command) => request(`/roasts/${id}/commands`, { method: "POST", body: JSON.stringify(command) }),
  addNote: (id, note) => request(`/roasts/${id}/notes`, { method: "POST", body: JSON.stringify(note) }),
  addEvent: (id, event) => request(`/roasts/${id}/events`, { method: "POST", body: JSON.stringify(event) }),
  alogDownloadUrl: (id) => `${BASE}/roasts/${id}/alog`,
  importAlog: (path, title) =>
    request(`/roasts/import?${new URLSearchParams({ path, ...(title ? { title } : {}) })}`, { method: "POST" }),

  // presets (saved roast configurations)
  listPresets: () => request("/presets"),
  savePreset: (name, config, controls = {}) =>
    request("/presets", { method: "POST", body: JSON.stringify({ name, config, ...controls }) }),
  updatePreset: (id, name, config, controls = {}) =>
    request(`/presets/${id}`, { method: "PUT", body: JSON.stringify({ name, config, ...controls }) }),
  deletePreset: (id) => request(`/presets/${id}`, { method: "DELETE" }),

  // settings (Ollama connection for AI roast reviews)
  getSettings: () => request("/settings"),
  saveSettings: (settings) => request("/settings", { method: "PUT", body: JSON.stringify(settings) }),
  checkOllama: (url) => request(`/settings/ollama/status?${new URLSearchParams({ url })}`),

  // AI roast review
  getReview: (roastId) => request(`/roasts/${roastId}/review`),
  generateReview: (roastId) => request(`/roasts/${roastId}/review`, { method: "POST" }),
};

export function roastStreamUrl(id) {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}${BASE}/roasts/${id}/stream`;
}

// Plain http(s), not ws:// -- EventSource, unlike WebSocket, works fine
// with a same-origin relative URL.
export function settingsStreamUrl() {
  return `${BASE}/settings/stream`;
}
