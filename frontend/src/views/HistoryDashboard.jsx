import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client.js";
import { useConfirm, useNotify } from "../components/DialogProvider.jsx";

function formatDuration(seconds) {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function HistoryDashboard() {
  const confirm = useConfirm();
  const notify = useNotify();
  const [roasts, setRoasts] = useState([]);
  const [filters, setFilters] = useState({ mode: "", status: "", tag: "", created_by: "", q: "" });
  // Debounced separately from `filters.q` itself -- typing shouldn't fire
  // a request per keystroke, but the input needs to stay responsive/
  // uncontrolled-feeling, so this local value updates immediately while
  // filters.q (the thing refresh()'s effect actually watches) lags behind it.
  const [searchInput, setSearchInput] = useState("");
  const [allTags, setAllTags] = useState([]);
  const [allRoasters, setAllRoasters] = useState([]);
  // 1-indexed. pageSize comes from Settings > History (default 100 until
  // that loads) -- GET /roasts itself has no hard cap on how many total
  // roasts are reachable, just how many come back per request; paging
  // through offset is what gets to the rest, same as the "don't limit
  // it" ask this was built for.
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(100);
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [importPath, setImportPath] = useState("");
  const [importTitle, setImportTitle] = useState("");
  const [importError, setImportError] = useState(null);
  const [importing, setImporting] = useState(false);
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [deletingSelected, setDeletingSelected] = useState(false);
  // Off by default -- each entry here is a real .alog read+parse
  // server-side (see GET /roasts/stats-batch), so this is opt-in rather
  // than fetched on every History page load. Scoped to the exact same
  // filtered/paged set already showing in the table below.
  const [showTrends, setShowTrends] = useState(false);
  const [trendStats, setTrendStats] = useState([]);
  const [trendsLoading, setTrendsLoading] = useState(false);
  const navigate = useNavigate();

  function refresh() {
    setLoading(true);
    const filterParams = Object.fromEntries(Object.entries(filters).filter(([, v]) => v));
    const pageParams = { ...filterParams, limit: pageSize, offset: (page - 1) * pageSize };
    return Promise.all([api.listRoasts(pageParams), api.countRoasts(filterParams)])
      .then(([roastsPage, count]) => {
        setRoasts(roastsPage);
        setTotalCount(count.total);
      })
      .finally(() => setLoading(false));
  }

  function toggleSelected(id) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const allSelected = roasts.length > 0 && roasts.every((r) => selectedIds.has(r.id));
  const someSelected = roasts.some((r) => selectedIds.has(r.id));

  function toggleSelectAll() {
    setSelectedIds((prev) => {
      if (allSelected) {
        const next = new Set(prev);
        roasts.forEach((r) => next.delete(r.id));
        return next;
      }
      const next = new Set(prev);
      roasts.forEach((r) => next.add(r.id));
      return next;
    });
  }

  useEffect(() => {
    refresh();
  }, [filters, page, pageSize]); // eslint-disable-line react-hooks/exhaustive-deps

  // Same filters/page/pageSize as the table above, so "trends" always
  // means "trends for exactly what I'm looking at right now" -- picking
  // a tag/mode filter while trends are showing re-fetches automatically,
  // same as the table itself does.
  useEffect(() => {
    if (!showTrends) return;
    setTrendsLoading(true);
    const filterParams = Object.fromEntries(Object.entries(filters).filter(([, v]) => v));
    const pageParams = { ...filterParams, limit: pageSize, offset: (page - 1) * pageSize };
    api
      .getRoastStatsBatch(pageParams)
      .then(setTrendStats)
      .finally(() => setTrendsLoading(false));
  }, [showTrends, filters, page, pageSize]); // eslint-disable-line react-hooks/exhaustive-deps

  const trendAverages = useMemo(() => {
    const withDry = trendStats.filter((r) => r.dry_pct != null);
    const withDtr = trendStats.filter((r) => r.dtr_pct != null);
    const flaggedCount = trendStats.filter(
      (r) => r.ror_flags.crashes.length || r.ror_flags.flatlines.length || r.ror_flags.flicks.length
    ).length;
    return {
      avgDryPct: withDry.length ? withDry.reduce((sum, r) => sum + r.dry_pct, 0) / withDry.length : null,
      avgDtrPct: withDtr.length ? withDtr.reduce((sum, r) => sum + r.dtr_pct, 0) / withDtr.length : null,
      flaggedCount,
    };
  }, [trendStats]);

  useEffect(() => {
    const id = setTimeout(() => {
      setFilters((f) => ({ ...f, q: searchInput.trim() }));
      setPage(1); // a new search restarts paging from the top
    }, 300);
    return () => clearTimeout(id);
  }, [searchInput]);

  // Fetched once on mount -- a tag added mid-session won't show up in the
  // filter dropdown until next reload, same tradeoff RoastDetailView.jsx's
  // one-time getSettings() fetch already accepts for temperature unit.
  useEffect(() => {
    api.listTags().then(setAllTags);
    api.listRoasters().then(setAllRoasters);
    api.getSettings().then((s) => setPageSize(s.history_page_size || 100));
  }, []);

  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));

  function updateFilter(key, value) {
    setFilters({ ...filters, [key]: value });
    setPage(1); // a changed filter restarts paging from the top
  }

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

  async function handleDelete(id, title) {
    if (!(await confirm(`Delete "${title}"? This removes it from history and deletes its .alog file. This can't be undone.`))) {
      return;
    }
    try {
      await api.deleteRoast(id);
      setSelectedIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      refresh();
    } catch (err) {
      notify(err.message, { title: "Delete failed" });
    }
  }

  async function handleDeleteSelected() {
    const ids = [...selectedIds];
    if (!ids.length) return;
    const confirmed = await confirm(
      `Delete ${ids.length} roast${ids.length === 1 ? "" : "s"}? This removes them from history and deletes their .alog files. This can't be undone.`
    );
    if (!confirmed) return;
    setDeletingSelected(true);
    try {
      const results = await Promise.allSettled(ids.map((id) => api.deleteRoast(id)));
      const failed = results
        .map((r, i) => (r.status === "rejected" ? { id: ids[i], reason: r.reason.message } : null))
        .filter(Boolean);
      setSelectedIds(new Set());
      refresh();
      if (failed.length) {
        const titles = failed.map((f) => {
          const roast = roasts.find((r) => r.id === f.id);
          return `${roast ? roast.title : f.id}: ${f.reason}`;
        });
        notify(`${failed.length} of ${ids.length} couldn't be deleted:\n${titles.join("\n")}`, { title: "Delete failed" });
      }
    } finally {
      setDeletingSelected(false);
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
        {showTrends && (
          <>
            <div>
              <span className="stat-value">
                {trendsLoading ? "…" : trendAverages.avgDryPct != null ? `${trendAverages.avgDryPct.toFixed(1)}%` : "—"}
              </span>
              <span className="stat-label">Avg Dry %</span>
            </div>
            <div>
              <span className="stat-value">
                {trendsLoading ? "…" : trendAverages.avgDtrPct != null ? `${trendAverages.avgDtrPct.toFixed(1)}%` : "—"}
              </span>
              <span className="stat-label">Avg DTR %</span>
            </div>
            <div>
              <span className="stat-value">{trendsLoading ? "…" : trendAverages.flaggedCount}</span>
              <span className="stat-label">With RoR flags</span>
            </div>
          </>
        )}
        <button type="button" className="link-like" onClick={() => setShowTrends((s) => !s)}>
          {showTrends ? "Hide trends" : "Show trends"}
        </button>
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
          Search
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Title, beans, or tag…"
          />
        </label>
        <label>
          Mode
          <select value={filters.mode} onChange={(e) => updateFilter("mode", e.target.value)}>
            <option value="">All</option>
            <option value="simulator">Simulator</option>
            <option value="alog_playback">Playback</option>
            <option value="modbus_live">Modbus (live)</option>
            <option value="ms6514_live">MS6514 (live)</option>
            <option value="aillio_live">Aillio Bullet (live)</option>
            <option value="tc4_live">TC4+ (live)</option>
          </select>
        </label>
        <label>
          Status
          <select value={filters.status} onChange={(e) => updateFilter("status", e.target.value)}>
            <option value="">All</option>
            <option value="roasting">Roasting</option>
            <option value="cooling">Cooling</option>
            <option value="complete">Complete</option>
            <option value="stopped">Stopped</option>
            <option value="aborted">Aborted</option>
          </select>
        </label>
        <label>
          Tag
          <select value={filters.tag} onChange={(e) => updateFilter("tag", e.target.value)}>
            <option value="">All</option>
            {allTags.map((t) => (
              <option key={t.tag} value={t.tag}>
                {t.tag} ({t.count})
              </option>
            ))}
          </select>
        </label>
        <label>
          Roasted by
          <select value={filters.created_by} onChange={(e) => updateFilter("created_by", e.target.value)}>
            <option value="">All</option>
            {allRoasters.map((r) => (
              <option key={r.created_by_username} value={r.created_by_username}>
                {r.created_by_username} ({r.count})
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="panel">
        {someSelected && (
          <div className="table-toolbar">
            <button
              type="button"
              className="danger"
              disabled={deletingSelected}
              onClick={handleDeleteSelected}
            >
              {deletingSelected ? "Deleting…" : `Delete selected (${selectedIds.size})`}
            </button>
          </div>
        )}
        <table className="roast-table">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  checked={allSelected}
                  ref={(el) => el && (el.indeterminate = someSelected && !allSelected)}
                  onChange={toggleSelectAll}
                  disabled={roasts.length === 0}
                />
              </th>
              <th>Title</th>
              <th>Mode</th>
              <th>Status</th>
              <th>Duration</th>
              <th>Beans</th>
              <th>Tags</th>
              <th>Created</th>
              <th>Roasted by</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={10}>Loading…</td>
              </tr>
            )}
            {!loading && roasts.length === 0 && (
              <tr>
                <td colSpan={10}>No roasts yet.</td>
              </tr>
            )}
            {roasts.map((r) => (
              <tr key={r.id}>
                <td>
                  <input type="checkbox" checked={selectedIds.has(r.id)} onChange={() => toggleSelected(r.id)} />
                </td>
                <td>{r.title}</td>
                <td>{r.mode}</td>
                <td>
                  <span className={`status-pill status-${r.status}`}>{r.status}</span>
                </td>
                <td>{formatDuration(r.duration_s)}</td>
                <td>{r.beans || "—"}</td>
                <td>
                  {r.tags && r.tags.length > 0
                    ? r.tags.map((t) => (
                        <span key={t} className="tag-chip">
                          {t}
                        </span>
                      ))
                    : "—"}
                </td>
                <td>{new Date(r.created_at).toLocaleString()}</td>
                <td>{r.created_by_username || "—"}</td>
                <td>
                  <Link to={`/roasts/${r.id}`}>View</Link>
                  {" · "}
                  <button type="button" className="danger link-like" onClick={() => handleDelete(r.id, r.title)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {totalCount > 0 && (
          <div className="pagination-row">
            <button type="button" disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)}>
              ← Prev
            </button>
            <span>
              Page {page} of {totalPages} ({totalCount} roast{totalCount === 1 ? "" : "s"})
            </span>
            <button type="button" disabled={page >= totalPages || loading} onClick={() => setPage((p) => p + 1)}>
              Next →
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
