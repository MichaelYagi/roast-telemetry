const BASE = "/api/v1";

// FastAPI sends a plain string for most errors, but a list of {loc, msg}
// objects when a request fails validation -- show those as readable text.
function errorText(detail) {
  if (Array.isArray(detail)) {
    return detail.map((d) => (d && d.msg ? `${(d.loc || []).slice(1).join(".") || "request"}: ${d.msg}` : String(d))).join("; ");
  }
  return typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : "";
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(errorText(body.detail) || `${res.status} ${res.statusText}`);
    err.status = res.status; // lets callers distinguish e.g. 404 ("doesn't exist yet") from real failures
    throw err;
  }
  if (res.status === 204) return null; // no body -- DELETE endpoints, e.g. -- don't try to parse it
  const contentType = res.headers.get("content-type") || "";
  return contentType.includes("application/json") ? res.json() : res.text();
}

// Drops empty values so blank filters aren't sent as "tag=".
function cleanParams(params) {
  return Object.fromEntries(Object.entries(params).filter(([, v]) => v !== "" && v != null && v !== false));
}

export const api = {
  health: () => request("/health"),

  // auth
  authStatus: () => request("/auth/status"),
  register: (username, password) => request("/auth/register", { method: "POST", body: JSON.stringify({ username, password }) }),
  login: (username, password, rememberMe = false) =>
    request("/auth/login", { method: "POST", body: JSON.stringify({ username, password, remember_me: rememberMe }) }),
  logout: () => request("/auth/logout", { method: "POST" }),
  me: () => request("/auth/me"),
  listUsers: () => request("/auth/users"),
  changePassword: (currentPassword, newPassword) =>
    request("/auth/change-password", { method: "POST", body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) }),
  listApiKeys: () => request("/auth/api-keys"),
  createApiKey: (name) => request("/auth/api-keys", { method: "POST", body: JSON.stringify({ name }) }),
  deleteApiKey: (id) => request(`/auth/api-keys/${id}`, { method: "DELETE" }),
  allowUser: (id) => request(`/auth/users/${id}/allow`, { method: "POST" }),
  denyUser: (id) => request(`/auth/users/${id}/deny`, { method: "POST" }),
  resetUserToPending: (id) => request(`/auth/users/${id}/reset-to-pending`, { method: "POST" }),
  deleteUser: (id) => request(`/auth/users/${id}`, { method: "DELETE" }),

  // devices
  listDevices: () => request("/devices"),

  // serial ports (Configure Roast form's port-picker convenience list)
  listSerialPorts: () => request("/serial-ports"),

  // server-side file chooser (History import) -- folders and .alog files on the server
  listServerFiles: (path) => request(`/files${path ? `?${new URLSearchParams({ path })}` : ""}`),

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
  // grams as a query param, matching the backend route's plain-float
  // parameter (backend/app/api/roasts.py's set_weight) -- no request body.
  setWeightRoasted: (id, grams) => request(`/roasts/${id}/weight?${new URLSearchParams({ grams })}`, { method: "POST" }),
  setWeightGreen: (id, grams) => request(`/roasts/${id}/weight-green?${new URLSearchParams({ grams })}`, { method: "POST" }),
  deleteWeightRoasted: (id) => request(`/roasts/${id}/weight`, { method: "DELETE" }),
  deleteWeightGreen: (id) => request(`/roasts/${id}/weight-green`, { method: "DELETE" }),
  setTags: (id, tags) => request(`/roasts/${id}/tags`, { method: "PUT", body: JSON.stringify({ tags }) }),
  listTags: () => request("/roasts/tags"),
  listRoasters: () => request("/roasts/roasters"),
  countRoasts: (params = {}) => request(`/roasts/count?${new URLSearchParams(params)}`),
  getRoastStats: (id) => request(`/roasts/${id}/stats`),
  getRoastStatsBatch: (params = {}) => request(`/roasts/stats-batch?${new URLSearchParams(params)}`),
  addNote: (id, note) => request(`/roasts/${id}/notes`, { method: "POST", body: JSON.stringify(note) }),
  updateNote: (id, noteId, text) => request(`/roasts/${id}/notes/${noteId}`, { method: "PATCH", body: JSON.stringify({ text }) }),
  deleteNote: (id, noteId) => request(`/roasts/${id}/notes/${noteId}`, { method: "DELETE" }),
  setOutcome: (id, outcome) => request(`/roasts/${id}/outcome`, { method: "PUT", body: JSON.stringify(outcome) }),
  setRoastBeans: (id, name) => request(`/roasts/${id}/beans`, { method: "PUT", body: JSON.stringify({ name }) }),
  listBeans: () => request("/beans"),
  createBean: (bean) => request("/beans", { method: "POST", body: JSON.stringify(bean) }),
  updateBean: (id, bean) => request(`/beans/${id}`, { method: "PUT", body: JSON.stringify(bean) }),
  deleteBean: (id) => request(`/beans/${id}`, { method: "DELETE" }),
  listViews: (kind) => request(`/views?${new URLSearchParams(kind ? { kind } : {})}`),
  saveView: (kind, name, config) => request("/views", { method: "POST", body: JSON.stringify({ kind, name, config }) }),
  deleteView: (id) => request(`/views/${id}`, { method: "DELETE" }),

  // activity log (roast deletes/edits, safety/control events -- see backend/app/storage.py's activity_log table)
  listActivity: (params = {}) => request(`/activity?${new URLSearchParams(cleanParams(params))}`),
  countActivity: (params = {}) => request(`/activity/count?${new URLSearchParams(cleanParams(params))}`),
  activityExportUrl: (kind, params = {}) => `${BASE}/activity/export.${kind}?${new URLSearchParams(cleanParams(params))}`,
  getAnalysisMetrics: () => request("/analysis/metrics"),
  getAnalysisTable: (params = {}) => request(`/analysis/table?${new URLSearchParams(cleanParams(params))}`),
  getAnalysisSummary: (params = {}) => request(`/analysis/summary?${new URLSearchParams(cleanParams(params))}`),
  getRoastNumbers: (id) => request(`/analysis/roasts/${id}`),
  analysisExportUrl: (kind, params = {}) => `${BASE}/analysis/export.${kind}?${new URLSearchParams(cleanParams(params))}`,
  requestInsight: (body) =>
    request("/analysis/insights", { method: "POST", body: JSON.stringify(Object.fromEntries(Object.entries(body).filter(([, v]) => v !== "" && v != null))) }),
  getInsight: (jobId) => request(`/analysis/insights/${jobId}`),
  getControl: (id) => request(`/roasts/${id}/control`),
  emergencyStop: (id) => request(`/roasts/${id}/emergency-stop`, { method: "POST" }),
  startProgramFromRoast: (id, sourceRoastId) =>
    request(`/roasts/${id}/control/program/from-roast`, { method: "POST", body: JSON.stringify({ source_roast_id: sourceRoastId }) }),
  startFeedback: (id, config) => request(`/roasts/${id}/control/feedback`, { method: "PUT", body: JSON.stringify(config) }),
  stopAutomation: (id) => request(`/roasts/${id}/control/automation`, { method: "DELETE" }),
  addEvent: (id, event) => request(`/roasts/${id}/events`, { method: "POST", body: JSON.stringify(event) }),
  deleteEvent: (id, eventId) => request(`/roasts/${id}/events/${eventId}`, { method: "DELETE" }),
  retimeEvent: (id, eventId, timeS) =>
    request(`/roasts/${id}/events/${eventId}`, { method: "PATCH", body: JSON.stringify({ time_s: timeS }) }),
  alogDownloadUrl: (id) => `${BASE}/roasts/${id}/alog`,
  csvDownloadUrl: (id) => `${BASE}/roasts/${id}/csv`,
  jsonDownloadUrl: (id) => `${BASE}/roasts/${id}/json`,
  roastlogCsvDownloadUrl: (id) => `${BASE}/roasts/${id}/roastlog.csv`,
  xlsxDownloadUrl: (id) => `${BASE}/roasts/${id}/xlsx`,
  importAlog: (path, title) =>
    request(`/roasts/import?${new URLSearchParams({ path, ...(title ? { title } : {}) })}`, { method: "POST" }),
  // An .alog from this computer: the file itself is the request body.
  uploadAlog: (file, title) =>
    request(`/roasts/import-upload?${new URLSearchParams({ filename: file.name, ...(title ? { title } : {}) })}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    }),

  // presets (saved roast configurations)
  listPresets: () => request("/presets"),
  savePreset: (name, config, controls = {}) =>
    request("/presets", { method: "POST", body: JSON.stringify({ name, config, ...controls }) }),
  updatePreset: (id, name, config, controls = {}) =>
    request(`/presets/${id}`, { method: "PUT", body: JSON.stringify({ name, config, ...controls }) }),
  deletePreset: (id) => request(`/presets/${id}`, { method: "DELETE" }),

  // device profiles (Modbus register maps -- see DeviceProfile in the backend)
  listDeviceProfiles: () => request("/device-profiles"),
  createDeviceProfile: (profile) => request("/device-profiles", { method: "POST", body: JSON.stringify(profile) }),
  updateDeviceProfile: (id, profile) => request(`/device-profiles/${id}`, { method: "PUT", body: JSON.stringify(profile) }),
  deleteDeviceProfile: (id) => request(`/device-profiles/${id}`, { method: "DELETE" }),

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

export function activeRoastStreamUrl() {
  return `${BASE}/roasts/active/stream`;
}
