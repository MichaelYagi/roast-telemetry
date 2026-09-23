import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import BulkZipDownload from "../components/BulkZipDownload.jsx";
import ComparisonPanel from "../components/ComparisonPanel.jsx";
import SavedViews from "../components/SavedViews.jsx";
import { useConfirm, useNotify } from "../components/DialogProvider.jsx";
import ServerFileChooser from "../components/ServerFileChooser.jsx";

function formatDuration(seconds) {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// What the Import box accepts, and (for the browser file picker) the "accept"
// list -- .alog and .json go through the native reader, .csv/.tsv/.xlsx
// through the roast-log table reader. See alog_playback/roastlog.py.
const IMPORTABLE_EXTENSIONS = [".alog", ".json", ".csv", ".tsv", ".xlsx"];

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
  // Configurable in Settings > History -- must stay > 2 there (clamped
  // server-side too), otherwise "compare" stops meaning anything.
  const [maxCompare, setMaxCompare] = useState(20);
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [importPath, setImportPath] = useState("");
  const [importError, setImportError] = useState(null);
  const [importing, setImporting] = useState(false);
  const [chooserOpen, setChooserOpen] = useState(false);
  const [uploadStatus, setUploadStatus] = useState(null); // e.g. "Uploading 2 of 5…"
  const [dragging, setDragging] = useState(false);
  // dragenter/dragleave fire symmetrically at every element boundary inside
  // the drop zone -- moving over the box's own child elements (the label,
  // the "Choose files" button, etc.) fires a leave+enter pair on the form,
  // toggling `dragging` off and back on for each one and visibly flickering
  // the highlight. A depth counter (dragenter: +1, dragleave: -1) only
  // reports "actually left" once it's back at 0, which is what setting
  // `dragging` from plain dragover/dragleave handlers can't tell.
  const dragDepth = useRef(0);
  const fileInputRef = useRef(null);
  // The ticked roasts. A comparison in the address (?compare=id,id,...) starts them
  // off ticked, so a reload or a shared link brings the same comparison back.
  const [selectedIds, setSelectedIds] = useState(
    () => new Set((new URLSearchParams(window.location.search).get("compare") || "").split(",").filter(Boolean))
  );
  const [compareOpen, setCompareOpen] = useState(() => Boolean(new URLSearchParams(window.location.search).get("compare")));
  const [deletingSelected, setDeletingSelected] = useState(false);
  // Totals over every roast that matches the filters (all pages), from
  // GET /analysis/summary -- not just the page of the table below.
  const [summary, setSummary] = useState(null);
  const [trendsLoading, setTrendsLoading] = useState(true);
  const [importOpen, setImportOpen] = useState(readImportOpen);
  const [tempUnit, setTempUnit] = useState("c"); // display only -- needed for a bulk PDF export's charts/tables
  const navigate = useNavigate();

  const [, setSearchParams] = useSearchParams();
  const comparePanelRef = useRef(null);
  // What's known about every roast seen so far (across pages), so a message can
  // name a roast by its title instead of an ID.
  const knownRoasts = useRef(new Map());
  const [compareNote, setCompareNote] = useState(null);
  const [pendingConfig, setPendingConfig] = useState(null); // a saved comparison opened from here
  const titleFor = (id) => knownRoasts.current.get(id)?.label;
  const hasNoRecording = (id) => knownRoasts.current.get(id) && !knownRoasts.current.get(id).hasRecording;

  // The comparison IS the ticked roasts: tick one and it joins, untick it and it
  // leaves. (A roast with no saved recording has no curve, so it's left out.)
  const ticked = [...selectedIds];
  const leftOut = ticked.filter(hasNoRecording);
  const compareIds = compareOpen ? ticked.filter((id) => !hasNoRecording(id)).slice(0, maxCompare) : [];

  // Keep the address in step with the comparison, and close it when nothing is left to compare.
  useEffect(() => {
    if (compareOpen && compareIds.length === 0) {
      setCompareOpen(false);
      return;
    }
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (compareOpen) next.set("compare", compareIds.join(","));
        else next.delete("compare");
        return next;
      },
      { replace: true }
    );
  }, [compareOpen, compareIds.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  function startCompare() {
    if (!compareOpen) {
      const usable = ticked.filter((id) => !hasNoRecording(id));
      if (usable.length < 2) {
        const names = leftOut.map((id) => `"${titleFor(id)}"`).join(", ");
        setCompareNote(`Not enough to compare: ${names || "the selection"} ${leftOut.length === 1 ? "has" : "have"} no recording.`);
        return;
      }
      setCompareNote(null);
      setCompareOpen(true);
    }
    setTimeout(() => comparePanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  }

  // The top totals tiles (Total, Avg duration/loss/dry%/DTR%, Flagged) --
  // "every finished roast the filters match, across all pages" per
  // docs/analysis.html, so paging alone never needs this (see the
  // [filters]-only effect below), but an add/delete does: refresh() alone
  // only re-fetches the current page's rows and the page count, so without
  // this the tiles would keep showing pre-delete/pre-import numbers until
  // a filter was touched.
  function refreshTrends() {
    setTrendsLoading(true);
    return api
      .getAnalysisSummary(filters)
      .then((result) => setSummary(result))
      .catch(() => setSummary(null))
      .finally(() => setTrendsLoading(false));
  }

  function refresh() {
    setLoading(true);
    const filterParams = Object.fromEntries(Object.entries(filters).filter(([, v]) => v));
    const pageParams = { ...filterParams, limit: pageSize, offset: (page - 1) * pageSize };
    return Promise.all([api.listRoasts(pageParams), api.countRoasts(filterParams)])
      .then(([roastsPage, count]) => {
        roastsPage.forEach((r) =>
          knownRoasts.current.set(r.id, {
            // Many roasts share a title, so the date helps tell them apart.
            label: `${r.title}, ${new Date(r.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`,
            hasRecording: Boolean(r.alog_path),
          })
        );
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
  const offPageSelected = [...selectedIds].filter((id) => !roasts.some((r) => r.id === id)).length;

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

  useEffect(() => {
    api.getSettings().then((s) => setTempUnit(s.temperature_unit || "c"));
  }, []);

  // Same filters as the table above (but not the page), so the numbers
  // always describe everything the filters match.
  useEffect(() => {
    setTrendsLoading(true);
    let cancelled = false;
    api
      .getAnalysisSummary(filters)
      .then((result) => {
        if (!cancelled) setSummary(result);
      })
      .catch(() => {
        if (!cancelled) setSummary(null);
      })
      .finally(() => {
        if (!cancelled) setTrendsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filters]);

  const totals = useMemo(() => {
    const group = summary?.groups?.[0];
    const mean = (key) => group?.metrics?.[key]?.mean ?? null;
    return {
      total: summary?.total ?? 0,
      simulatedCount: summary?.simulated_excluded ?? 0,
      avgDuration: mean("duration_s"),
      avgLoss: mean("weight_loss_pct"),
      avgDryPct: mean("dry_pct"),
      avgDtrPct: mean("dtr_pct"),
      flaggedCount: group?.flagged_ror ?? 0,
    };
  }, [summary]);

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
    api.getSettings().then((s) => {
      setPageSize(s.history_page_size || 100);
      setMaxCompare(s.max_compare || 20);
    });
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
      const summary = await api.importAlog(importPath.trim());
      setImportPath("");
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
    const files = [...fileList].filter((f) => IMPORTABLE_EXTENSIONS.some((ext) => f.name.toLowerCase().endsWith(ext)));
    const skipped = fileList.length - files.length;
    if (!files.length) {
      setImportError(skipped ? `Only ${IMPORTABLE_EXTENSIONS.join(", ")} files can be imported.` : null);
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
          done.push(await api.uploadAlog(files[i]));
        } catch (err) {
          failed.push(`${files[i].name}: ${err.message}`);
        }
      }
    } finally {
      setUploadStatus(null);
      setImporting(false);
    }
    if (skipped) failed.push(`${skipped} file${skipped === 1 ? " was" : "s were"} not a supported format and was skipped.`);
    if (done.length === 1 && !failed.length) {
      navigate(`/roasts/${done[0].id}`);
      return;
    }
    if (done.length) {
      refresh();
      refreshTrends();
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
      refreshTrends();
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
      refreshTrends();
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
  const shown = (v) => (trendsLoading ? "…" : v);
  const exportParams = { ...filters };

  return (
    <div className="history-view">
      {compareIds.length > 0 && (
        <div ref={comparePanelRef}>
          <ComparisonPanel
            ids={compareIds}
            onRemoveId={(id) =>
              setSelectedIds((prev) => {
                const next = new Set(prev);
                next.delete(id);
                return next;
              })
            }
            onLoadIds={(ids) => setSelectedIds(new Set(ids))}
            onClose={() => {
              setCompareOpen(false);
              setPendingConfig(null);
            }}
            titleFor={titleFor}
            initialConfig={pendingConfig}
          />
        </div>
      )}

      <div className="panel">
        <div className="stats-grid">
          <div className="stat-tile">
            <span className="stat-value">{shown(totals.total)}</span>
            <span className="stat-label">Finished roasts</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{trendsLoading ? "…" : formatDuration(totals.avgDuration)}</span>
            <span className="stat-label">Avg duration</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{pct(totals.avgLoss)}</span>
            <span className="stat-label">Avg roast loss</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{pct(totals.avgDryPct)}</span>
            <span className="stat-label">Avg Dry %</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{pct(totals.avgDtrPct)}</span>
            <span className="stat-label">Avg DTR %</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{shown(totals.flaggedCount)}</span>
            <span className="stat-label">With RoR flags</span>
          </div>
        </div>
        <p className="hint stats-note">
          Covers every finished roast the filters match, across all pages
          {totals.simulatedCount > 0
            ? `, leaving out ${totals.simulatedCount} simulated roast${totals.simulatedCount === 1 ? "" : "s"}`
            : ""}
          {summary?.missing_recording
            ? `, and ${summary.missing_recording} whose recording file is missing or unreadable`
            : ""}
          . <Link to="/analysis">Analyse these</Link>
        </p>
      </div>

      <form
        className={`panel import-panel${dragging ? " import-dragging" : ""}`}
        onSubmit={handleImport}
        onDragEnter={(e) => {
          e.preventDefault();
          dragDepth.current += 1;
          setDragging(true);
        }}
        onDragOver={(e) => e.preventDefault()}
        onDragLeave={(e) => {
          e.preventDefault();
          dragDepth.current = Math.max(0, dragDepth.current - 1);
          if (dragDepth.current === 0) setDragging(false);
        }}
        onDrop={(e) => {
          e.preventDefault();
          dragDepth.current = 0;
          setDragging(false);
          if (e.dataTransfer?.files?.length) handleUploadFiles(e.dataTransfer.files);
        }}
      >
        <button type="button" className="import-toggle" aria-expanded={importShown} onClick={toggleImport}>
          <span className="import-toggle-title">Import roast logs</span>
          {!importShown && <span className="import-toggle-hint">Drop files here, or click to open</span>}
          <span className="import-chevron" aria-hidden="true">
            {importShown ? "▾" : "▸"}
          </span>
        </button>
        {importShown && (
          <div className="import-body">
            {importError && <p className="error import-error">{importError}</p>}
            <div className="upload-drop">
              <span>Drop {IMPORTABLE_EXTENSIONS.join(", ")} files here, or</span>
              <input
                ref={fileInputRef}
                type="file"
                accept={IMPORTABLE_EXTENSIONS.join(",")}
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
                  aria-label="Roast log file path (server-side)"
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
        <div className="table-toolbar-row">
          {totalCount > 0 && (
            <BulkZipDownload
              params={selectedIds.size > 0 ? { ids: [...selectedIds].join(",") } : exportParams}
              tempUnit={tempUnit}
            />
          )}
          {selectedIds.size === 0 && !compareOpen && (
            <div className="table-toolbar">
              <SavedViews
                kind="compare"
                loadOnly
                placeholder="Open a saved comparison"
                getConfig={() => ({})}
                onLoad={(config) => {
                  setSelectedIds(new Set(Array.isArray(config.ids) ? config.ids : []));
                  setPendingConfig(config);
                  setCompareOpen(true);
                }}
              />
            </div>
          )}
          {selectedIds.size > 0 && (
            <div className="table-toolbar selection-bar">
              <span>
                <strong>{selectedIds.size}</strong> selected
                {offPageSelected > 0 ? ` (${offPageSelected} on other pages)` : ""}
              </span>
              {selectedIds.size > maxCompare && <span className="hint">Compare works with up to {maxCompare} roasts.</span>}
              <button
                type="button"
                onClick={startCompare}
                disabled={!compareOpen && (selectedIds.size < 2 || selectedIds.size > maxCompare)}
                title={selectedIds.size < 2 && !compareOpen ? "Select at least two roasts to compare" : `Compare up to ${maxCompare} roasts`}
              >
                Compare
              </button>
              <button type="button" className="danger" disabled={deletingSelected} onClick={handleDeleteSelected}>
                {deletingSelected ? "Deleting…" : "Delete"}
              </button>
              {selectedIds.size === 1 && <span className="hint">Tick at least one more roast to compare.</span>}
              {compareNote && !compareOpen && <span className="error">{compareNote}</span>}
              {compareOpen && leftOut.length > 0 && (
                <span className="error">Left out {leftOut.map((id) => `"${titleFor(id)}"`).join(", ")}: no recording.</span>
              )}
            </div>
          )}
        </div>
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
                    <td className="cell-mode">{r.mode === "alog_playback" && !r.source_alog_path ? "Uploaded log" : MODE_LABELS[r.mode] || r.mode}</td>
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
