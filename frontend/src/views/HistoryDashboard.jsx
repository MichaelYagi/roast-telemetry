import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client.js";

function formatDuration(seconds) {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function HistoryDashboard() {
  const [roasts, setRoasts] = useState([]);
  const [filters, setFilters] = useState({ mode: "", status: "" });
  const [loading, setLoading] = useState(true);
  const [importPath, setImportPath] = useState("");
  const [importTitle, setImportTitle] = useState("");
  const [importError, setImportError] = useState(null);
  const [importing, setImporting] = useState(false);
  const navigate = useNavigate();

  function refresh() {
    setLoading(true);
    const params = Object.fromEntries(Object.entries(filters).filter(([, v]) => v));
    return api
      .listRoasts(params)
      .then(setRoasts)
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refresh();
  }, [filters]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleImport(e) {
    e.preventDefault();
    if (!importPath.trim()) return;
    setImportError(null);
    setImporting(true);
    try {
      const summary = await api.importAlog(importPath.trim(), importTitle.trim() || undefined);
      setImportPath("");
      setImportTitle("");
      navigate(`/roasts/${summary.id}`);
    } catch (err) {
      setImportError(err.message);
    } finally {
      setImporting(false);
    }
  }

  const stats = useMemo(() => {
    const complete = roasts.filter((r) => r.duration_s != null);
    const avgDuration = complete.length
      ? complete.reduce((sum, r) => sum + r.duration_s, 0) / complete.length
      : null;
    const withYield = roasts.filter((r) => r.weight_green_g && r.weight_roasted_g);
    const avgLoss = withYield.length
      ? withYield.reduce((sum, r) => sum + (1 - r.weight_roasted_g / r.weight_green_g), 0) / withYield.length
      : null;
    return { total: roasts.length, avgDuration, avgLoss };
  }, [roasts]);

  return (
    <div className="history-view">
      <div className="panel stats-row">
        <div>
          <span className="stat-value">{stats.total}</span>
          <span className="stat-label">Roasts</span>
        </div>
        <div>
          <span className="stat-value">{formatDuration(stats.avgDuration)}</span>
          <span className="stat-label">Avg duration</span>
        </div>
        <div>
          <span className="stat-value">{stats.avgLoss != null ? `${(stats.avgLoss * 100).toFixed(1)}%` : "—"}</span>
          <span className="stat-label">Avg roast loss</span>
        </div>
      </div>

      <form className="panel import-form" onSubmit={handleImport}>
        <h3>Import an .alog file</h3>
        <p className="hint">
          Loads the whole roast straight into history — full curve and events at once, no real-time
          replay. Path is resolved on the server (the machine running the backend).
        </p>
        {importError && <p className="error">{importError}</p>}
        <div className="form-row">
          <label>
            .alog file path (server-side)
            <input value={importPath} onChange={(e) => setImportPath(e.target.value)} placeholder="/path/to/roast.alog" />
          </label>
          <label>
            Title (optional)
            <input value={importTitle} onChange={(e) => setImportTitle(e.target.value)} placeholder="Defaults to the file's own title" />
          </label>
        </div>
        <button type="submit" disabled={importing || !importPath.trim()}>
          {importing ? "Importing…" : "Import"}
        </button>
      </form>

      <div className="panel filters-row">
        <label>
          Mode
          <select value={filters.mode} onChange={(e) => setFilters({ ...filters, mode: e.target.value })}>
            <option value="">All</option>
            <option value="simulator">Simulator</option>
            <option value="alog_playback">Playback</option>
          </select>
        </label>
        <label>
          Status
          <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
            <option value="">All</option>
            <option value="roasting">Roasting</option>
            <option value="complete">Complete</option>
            <option value="aborted">Aborted</option>
          </select>
        </label>
      </div>

      <div className="panel">
        <table className="roast-table">
          <thead>
            <tr>
              <th>Title</th>
              <th>Mode</th>
              <th>Status</th>
              <th>Duration</th>
              <th>Beans</th>
              <th>Created</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={7}>Loading…</td>
              </tr>
            )}
            {!loading && roasts.length === 0 && (
              <tr>
                <td colSpan={7}>No roasts yet.</td>
              </tr>
            )}
            {roasts.map((r) => (
              <tr key={r.id}>
                <td>{r.title}</td>
                <td>{r.mode}</td>
                <td>
                  <span className={`status-pill status-${r.status}`}>{r.status}</span>
                </td>
                <td>{formatDuration(r.duration_s)}</td>
                <td>{r.beans || "—"}</td>
                <td>{new Date(r.created_at).toLocaleString()}</td>
                <td>
                  <Link to={`/roasts/${r.id}`}>View</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
