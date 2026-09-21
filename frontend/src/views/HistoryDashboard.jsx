import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client.js";
import { useConfirm, useNotify } from "../components/DialogProvider.jsx";
import ServerFileChooser from "../components/ServerFileChooser.jsx";
import { isSimulatedRoast } from "../simulated.js";

function formatDuration(seconds) {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

const MODE_LABELS = {
  simulator: "Simulator",
  alog_playback: "Playback",
  modbus_live: "Modbus",
  ms6514_live: "MS6514",
  aillio_live: "Aillio Bullet",
  tc4_live: "TC4+",
};

const IMPORT_OPEN_KEY = "roastTelemetry.historyImportOpen";

function readImportOpen() {
  try {
    return window.localStorage.getItem(IMPORT_OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

function formatCreated(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return { date: "—", time: "" };
  return {
    date: d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }),
    time: d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }),
  };
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
  const [chooserOpen, setChooserOpen] = useState(false);
  const [uploadStatus, setUploadStatus] = useState(null); // e.g. "Uploading 2 of 5…"
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef(null);
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [deletingSelected, setDeletingSelected] = useState(false);
  // Always fetched, scoped to the exact same filtered/paged set showing in the
  // table below. Each entry is a real .alog read server-side (GET
  // /roasts/stats-batch), so it loads after the list rather than holding it up.
  const [trendStats, setTrendStats] = useState([]);
  const [trendsLoading, setTrendsLoading] = useState(true);
  const [importOpen, setImportOpen] = useState(readImportOpen);
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
    setTrendsLoading(true);
    const filterParams = Object.fromEntries(Object.entries(filters).filter(([, v]) => v));
    const pageParams = { ...filterParams, limit: pageSize, offset: (page - 1) * pageSize };
    api
      .getRoastStatsBatch(pageParams)
      .then(setTrendStats)
      .finally(() => setTrendsLoading(false));
  }, [filters, page, pageSize]); // eslint-disable-line react-hooks/exhaustive-deps

  // Roasts recorded against a built-in simulated device stay in the list but
  // never count toward any average or trend here -- fake data must not move
  // the numbers for real roasts.
  const simulatedIds = useMemo(() => new Set(roasts.filter(isSimulatedRoast).map((r) => r.id)), [roasts]);

  const trendAverages = useMemo(() => {
    const real = trendStats.filter((r) => !simulatedIds.has(r.id));
    const withDry = real.filter((r) => r.dry_pct != null);
    const withDtr = real.filter((r) => r.dtr_pct != null);
    const flaggedCount = real.filter(
      (r) => r.ror_flags.crashes.length || r.ror_flags.flatlines.length || r.ror_flags.flicks.length
    ).length;
    return {
      avgDryPct: withDry.length ? withDry.reduce((sum, r) => sum + r.dry_pct, 0) / withDry.length : null,
      avgDtrPct: withDtr.length ? withDtr.reduce((sum, r) => sum + r.dtr_pct, 0) / withDtr.length : null,
      flaggedCount,
    };
  }, [trendStats, simulatedIds]);

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

  // Files from this computer (chosen or dropped): one request each, in order.
  // A single file opens its roast; several stay here, and any that failed are listed.
  async function handleUploadFiles(fileList) {
    const files = [...fileList].filter((f) => f.name.toLowerCase().endsWith(".alog"));
    const skipped = fileList.length - files.length;
    if (!files.length) {
      setImportError(skipped ? "Only .alog files can be imported." : null);
      return;
    }
    setImportError(null);
    setImporting(true);
    const done = [];
    const failed = [];
    try {
      for (let i = 0; i < files.length; i++) {
        setUploadStatus(files.length > 1 ? `Uploading ${i + 1} of ${files.length}…` : "Uploading…");
        try {
          // The title box applies to a single file only -- one title can't fit several roasts.
          done.push(await api.uploadAlog(files[i], files.length === 1 ? importTitle.trim() || undefined : undefined));
        } catch (err) {
          failed.push(`${files[i].name}: ${err.message}`);
        }
      }
    } finally {
      setUploadStatus(null);
      setImporting(false);
    }
    if (skipped) failed.push(`${skipped} file${skipped === 1 ? " was" : "s were"} not an .alog and was skipped.`);
    if (done.length === 1 && !failed.length) {
      setImportTitle("");
      navigate(`/roasts/${done[0].id}`);
      return;
    }
    if (done.length) {
      setImportTitle("");
      refresh();
    }
    setImportError(failed.length ? failed.join("\n") : null);
    if (done.length > 1 || (done.length && failed.length)) {
      notify(`Imported ${done.length} roast${done.length === 1 ? "" : "s"}.`, { title: "Import finished" });
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
    const real = roasts.filter((r) => !isSimulatedRoast(r));
    const complete = real.filter((r) => r.duration_s != null);
    const avgDuration = complete.length
      ? complete.reduce((sum, r) => sum + r.duration_s, 0) / complete.length
      : null;
    const withYield = real.filter((r) => r.weight_green_g && r.weight_roasted_g);
    const avgLoss = withYield.length
      ? withYield.reduce((sum, r) => sum + (1 - r.weight_roasted_g / r.weight_green_g), 0) / withYield.length
      : null;
    return { total: roasts.length, simulatedCount: roasts.length - real.length, avgDuration, avgLoss };
  }, [roasts]);

  const anyFilter = Object.values(filters).some(Boolean) || Boolean(searchInput);
  function clearFilters() {
    setFilters({ mode: "", status: "", tag: "", created_by: "", q: "" });
    setSearchInput("");
    setPage(1);
  }

  // The import panel opens itself while something is happening in it.
  const importShown = importOpen || dragging || importing || Boolean(importError);
  function toggleImport() {
    const next = !importOpen;
    setImportOpen(next);
    try {
      window.localStorage.setItem(IMPORT_OPEN_KEY, next ? "1" : "0");
    } catch {
      // storage blocked -- remembering the choice is only a convenience
    }
  }

  const pct = (v) => (trendsLoading ? "…" : v != null ? `${v.toFixed(1)}%` : "—");

  return (
    <div className="history-view">
      <div className="panel">
        <div className="stats-grid">
          <div className="stat-tile">
            <span className="stat-value">{stats.total}</span>
            <span className="stat-label">Roasts</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{formatDuration(stats.avgDuration)}</span>
            <span className="stat-label">Avg duration</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{stats.avgLoss != null ? `${(stats.avgLoss * 100).toFixed(1)}%` : "—"}</span>
            <span className="stat-label">Avg roast loss</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{pct(trendAverages.avgDryPct)}</span>
            <span className="stat-label">Avg Dry %</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{pct(trendAverages.avgDtrPct)}</span>
            <span className="stat-label">Avg DTR %</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{trendsLoading ? "…" : trendAverages.flaggedCount}</span>
            <span className="stat-label">With RoR flags</span>
          </div>
        </div>
        {stats.simulatedCount > 0 && (
          <p className="hint stats-note">
            Averages leave out {stats.simulatedCount} simulated roast{stats.simulatedCount === 1 ? "" : "s"}.
          </p>
        )}
      </div>

      <form
        className={`panel import-panel${dragging ? " import-dragging" : ""}`}
        onSubmit={handleImport}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (e.dataTransfer?.files?.length) handleUploadFiles(e.dataTransfer.files);
        }}
      >
        <button type="button" className="import-toggle" aria-expanded={importShown} onClick={toggleImport}>
          <span className="import-toggle-title">Import .alog files</span>
          {!importShown && <span className="import-toggle-hint">Drop files here, or click to open</span>}
          <span className="import-chevron" aria-hidden="true">
            {importShown ? "▾" : "▸"}
          </span>
        </button>
        {importShown && (
          <div className="import-body">
            <label className="import-title-field">
              Title (optional, one file at a time)
              <input value={importTitle} onChange={(e) => setImportTitle(e.target.value)} placeholder="Defaults to the file's own title" />
            </label>
            {importError && <p className="error import-error">{importError}</p>}
            <div className="upload-drop">
              <span>Drop .alog files here, or</span>
              <input
                ref={fileInputRef}
                type="file"
                accept=".alog"
                multiple
                hidden
                onChange={(e) => {
                  handleUploadFiles(e.target.files);
                  e.target.value = ""; // so choosing the same file again still fires
                }}
              />
              <button type="button" onClick={() => fileInputRef.current?.click()} disabled={importing}>
                Choose files…
              </button>
              {uploadStatus && <span className="hint">{uploadStatus}</span>}
            </div>
            <div className="path-field">
              <span className="field-label">Or use a file already on the server (the machine running the backend)</span>
              <div className="path-with-browse">
                <input
                  aria-label=".alog file path (server-side)"
                  value={importPath}
                  onChange={(e) => setImportPath(e.target.value)}
                  placeholder="/path/to/roast.alog"
                />
                <button type="button" onClick={() => setChooserOpen(true)}>
                  Browse…
                </button>
                <button type="submit" disabled={importing || !importPath.trim()}>
                  {importing ? "Importing…" : "Import"}
                </button>
              </div>
            </div>
          </div>
        )}
      </form>
      <ServerFileChooser
        open={chooserOpen}
        onClose={() => setChooserOpen(false)}
        onSelect={(path) => setImportPath(path)}
        startPath={importPath}
      />

      <div className="panel filters-grid">
        <label className="filter-search">
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
        {anyFilter && (
          <button type="button" className="filters-clear" onClick={clearFilters}>
            Clear filters
          </button>
        )}
      </div>

      <div className="panel">
        {someSelected && (
          <div className="table-toolbar">
            <button type="button" className="danger" disabled={deletingSelected} onClick={handleDeleteSelected}>
              {deletingSelected ? "Deleting…" : `Delete selected (${selectedIds.size})`}
            </button>
          </div>
        )}
        <label className="select-all-mobile">
          <input
            type="checkbox"
            checked={allSelected}
            ref={(el) => el && (el.indeterminate = someSelected && !allSelected)}
            onChange={toggleSelectAll}
            disabled={roasts.length === 0}
          />
          Select all
        </label>
        <div className="table-scroll">
          <table className="roast-table history-table">
            <thead>
              <tr>
                <th className="col-select">
                  <input
                    type="checkbox"
                    aria-label="Select all"
                    checked={allSelected}
                    ref={(el) => el && (el.indeterminate = someSelected && !allSelected)}
                    onChange={toggleSelectAll}
                    disabled={roasts.length === 0}
                  />
                </th>
                <th>Roast</th>
                <th>Mode</th>
                <th>Status</th>
                <th>Duration</th>
                <th>Tags</th>
                <th>Created</th>
                <th className="col-actions"></th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr className="table-message">
                  <td colSpan={8}>Loading…</td>
                </tr>
              )}
              {!loading && roasts.length === 0 && (
                <tr className="table-message">
                  <td colSpan={8}>{anyFilter ? "No roasts match these filters." : "No roasts yet."}</td>
                </tr>
              )}
              {roasts.map((r) => {
                const created = formatCreated(r.created_at);
                return (
                  <tr key={r.id}>
                    <td className="cell-select">
                      <input
                        type="checkbox"
                        aria-label={`Select ${r.title}`}
                        checked={selectedIds.has(r.id)}
                        onChange={() => toggleSelected(r.id)}
                      />
                    </td>
                    <td className="cell-title">
                      <Link className="roast-link" to={`/roasts/${r.id}`}>
                        {r.title}
                      </Link>
                      {r.beans && <span className="cell-sub">{r.beans}</span>}
                    </td>
                    <td className="cell-mode">{MODE_LABELS[r.mode] || r.mode}</td>
                    <td className="cell-status">
                      <span className={`status-pill status-${r.status}`}>{r.status}</span>
                    </td>
                    <td className="cell-duration">{formatDuration(r.duration_s)}</td>
                    <td className="cell-tags">
                      {r.tags && r.tags.length > 0
                        ? r.tags.map((t) => (
                            <span key={t} className="tag-chip">
                              {t}
                            </span>
                          ))
                        : null}
                    </td>
                    <td className="cell-created">
                      <span className="created-date">{created.date}</span>
                      <span className="cell-sub">
                        {created.time}
                        {r.created_by_username ? ` · ${r.created_by_username}` : ""}
                      </span>
                    </td>
                    <td className="cell-actions">
                      <Link className="btn-sm" to={`/roasts/${r.id}`}>
                        View
                      </Link>
                      <button type="button" className="btn-sm btn-danger-soft" onClick={() => handleDelete(r.id, r.title)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
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
