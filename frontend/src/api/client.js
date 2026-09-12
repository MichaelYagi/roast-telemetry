const BASE = "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  const contentType = res.headers.get("content-type") || "";
  return contentType.includes("application/json") ? res.json() : res.text();
}

export const api = {
  // machines
  listMachines: (params = {}) => request(`/machines?${new URLSearchParams(params)}`),
  getMachine: (id) => request(`/machines/${id}`),

  // devices
  listDevices: () => request("/devices"),

  // roasts
  listRoasts: (params = {}) => request(`/roasts?${new URLSearchParams(params)}`),
  createRoast: (payload) => request("/roasts", { method: "POST", body: JSON.stringify(payload) }),
  getRoast: (id) => request(`/roasts/${id}`),
  stopRoast: (id) => request(`/roasts/${id}/stop`, { method: "POST" }),
  sendCommand: (id, command) => request(`/roasts/${id}/commands`, { method: "POST", body: JSON.stringify(command) }),
  addNote: (id, note) => request(`/roasts/${id}/notes`, { method: "POST", body: JSON.stringify(note) }),
  addEvent: (id, event) => request(`/roasts/${id}/events`, { method: "POST", body: JSON.stringify(event) }),
  alogDownloadUrl: (id) => `${BASE}/roasts/${id}/alog`,
  importAlog: (path, title) =>
    request(`/roasts/import?${new URLSearchParams({ path, ...(title ? { title } : {}) })}`, { method: "POST" }),
};

export function roastStreamUrl(id) {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}${BASE}/roasts/${id}/stream`;
}
