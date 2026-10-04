import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import BulkZipDownload from "../components/BulkZipDownload.jsx";
import ComparisonPanel from "../components/ComparisonPanel.jsx";
import SavedViews from "../components/SavedViews.jsx";
import { useConfirm, useNotify } from "../components/DialogProvider.jsx";
import ServerFileChooser from "../components/ServerFileChooser.jsx";
import { formatSeconds } from "../lib/metricFormat.js";
import { endedBeforeDrop, isEmergencyStopped } from "../roastFlags.js";

// What the Import box accepts, and (for the browser file picker) the "accept"
// list -- .alog and .json go through the native reader, .csv/.tsv/.xlsx
// through the roast-log table reader. See alog_playback/roastlog.py.
const IMPORTABLE_EXTENSIONS = [".alog", ".json", ".csv", ".tsv", ".xlsx"];

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
  const { t } = useTranslation();
  const MODE_LABELS = {
    simulator: t("history.modeLabels.simulator"),
    alog_playback: t("history.modeLabels.playback"),
    modbus_live: t("history.modeLabels.modbus"),
    ms6514_live: t("history.modeLabels.ms6514"),
    aillio_live: t("history.modeLabels.aillioBullet"),
    tc4_live: t("history.modeLabels.tc4"),
  };
  const STATUS_LABELS = {
    roasting: t("history.filters.statusRoasting"),
    cooling: t("history.filters.statusCooling"),
    complete: t("history.filters.statusComplete"),
    stopped: t("history.filters.statusStopped"),
    aborted: t("history.filters.statusAborted"),
  };
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
  const [importSummary, setImportSummary] = useState(null);
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
  const folderInputRef = useRef(null);
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
        const names = leftOut.map((id) => `"${titleFor(id)}"`).join(", ") || t("history.selection.theSelection");
        setCompareNote(t("history.selection.notEnoughToCompare", { count: leftOut.length, names }));
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
    // A file whose name starts with a dot is a hidden one -- macOS adds a
    // "._<name>" metadata twin beside every file on any folder copied or
    // zipped from a Mac, plus .DS_Store. They aren't roast logs: every one
    // is left out without a word, not uploaded and not counted as skipped.
    const picked = [...fileList].filter((f) => !f.name.startsWith("."));
    const files = picked.filter((f) => IMPORTABLE_EXTENSIONS.some((ext) => f.name.toLowerCase().endsWith(ext)));
    const skipped = picked.length - files.length;
    if (!files.length) {
      setImportError(skipped ? t("history.import.unsupportedFormat", { extensions: IMPORTABLE_EXTENSIONS.join(", ") }) : null);
      return;
    }
    const settings = await api.getSettings().catch(() => null);
    const maxFiles = settings?.bulk_import_limit || 500;
    if (files.length > maxFiles) {
      setImportError(t("history.import.tooManyFiles", { count: files.length, max: maxFiles }));
      return;
    }
    setImportError(null);
    setImportSummary(null);
    setImporting(true);
    const done = [];
    const failed = [];
    try {
      for (let i = 0; i < files.length; i++) {
        setUploadStatus(files.length > 1 ? t("history.import.uploadingOf", { current: i + 1, total: files.length }) : t("history.import.uploading"));
        try {
          done.push(await api.uploadAlog(files[i]));
        } catch (err) {
          // 409 = this exact file was already imported; skipped, not a failure.
          failed.push(err.status === 409 ? t("history.import.alreadyImported", { name: files[i].name }) : `${files[i].name}: ${err.message}`);
        }
      }
    } finally {
      setUploadStatus(null);
      setImporting(false);
    }
    if (skipped) failed.push(t("history.import.skippedFiles", { count: skipped }));
    if (done.length === 1 && !failed.length) {
      navigate(`/roasts/${done[0].id}`);
      return;
    }
    if (done.length) {
      refresh();
      refreshTrends();
    }
    setImportError(failed.length ? failed.join("\n") : null);
    setImportSummary(done.length > 1 || (done.length && failed.length) ? t("history.import.importedRoasts", { count: done.length }) : null);
  }

  async function handleDelete(id, title) {
    if (!(await confirm(t("history.confirm.deleteOne", { title })))) {
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
      notify(err.message, { title: t("history.notify.deleteFailedTitle") });
    }
  }

  async function handleDeleteSelected() {
    const ids = [...selectedIds];
    if (!ids.length) return;
    const confirmed = await confirm(t("history.confirm.deleteMany", { count: ids.length }));
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
        notify(t("history.notify.deleteFailedSummary", { failedCount: failed.length, total: ids.length, details: titles.join("\n") }), {
          title: t("history.notify.deleteFailedTitle"),
        });
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
            <span className="stat-label">{t("history.stats.finishedRoasts")}</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{trendsLoading ? "…" : formatSeconds(totals.avgDuration)}</span>
            <span className="stat-label">{t("history.stats.avgDuration")}</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{pct(totals.avgLoss)}</span>
            <span className="stat-label">{t("history.stats.avgRoastLoss")}</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{pct(totals.avgDryPct)}</span>
            <span className="stat-label">{t("history.stats.avgDryPct")}</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{pct(totals.avgDtrPct)}</span>
            <span className="stat-label">{t("history.stats.avgDtrPct")}</span>
          </div>
          <div className="stat-tile">
            <span className="stat-value">{shown(totals.flaggedCount)}</span>
            <span className="stat-label">{t("history.stats.withRorFlags")}</span>
          </div>
        </div>
        <p className="hint stats-note">
          {t("history.statsNote.base")}
          {totals.simulatedCount > 0 ? t("history.statsNote.simulatedClause", { count: totals.simulatedCount }) : ""}
          {summary?.missing_recording ? t("history.statsNote.missingClause", { count: summary.missing_recording }) : ""}
          . <Link to="/analysis">{t("history.statsNote.analyseLink")}</Link>
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
          <span className="import-toggle-title">{t("history.import.heading")}</span>
          {!importShown && <span className="import-toggle-hint">{t("history.import.hint")}</span>}
          <span className="import-chevron" aria-hidden="true">
            {importShown ? "▾" : "▸"}
          </span>
        </button>
        {importShown && (
          <div className="import-body">
            {importSummary && <p className="hint import-summary">{importSummary}</p>}
            {importError && <p className="error import-error">{importError}</p>}
            <div className="upload-drop">
              <span>{t("history.import.dropFilesHere", { extensions: IMPORTABLE_EXTENSIONS.join(", ") })}</span>
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
                {t("history.import.chooseFiles")}
              </button>
              <input
                ref={folderInputRef}
                type="file"
                webkitdirectory=""
                directory=""
                multiple
                hidden
                onChange={(e) => {
                  handleUploadFiles(e.target.files);
                  e.target.value = "";
                }}
              />
              <button type="button" onClick={() => folderInputRef.current?.click()} disabled={importing}>
                {t("history.import.chooseFolder")}
              </button>
              {uploadStatus && <span className="hint">{uploadStatus}</span>}
            </div>
            <div className="path-field">
              <span className="field-label">{t("history.import.orServerFile")}</span>
              <div className="path-with-browse">
                <input
                  aria-label={t("history.import.pathAriaLabel")}
                  value={importPath}
                  onChange={(e) => setImportPath(e.target.value)}
                  placeholder="/path/to/roast.alog"
                />
                <button type="button" onClick={() => setChooserOpen(true)}>
                  {t("history.import.browse")}
                </button>
                <button type="submit" disabled={importing || !importPath.trim()}>
                  {importing ? t("history.import.importing") : t("history.import.importBtn")}
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
          {t("history.filters.search")}
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder={t("history.filters.searchPlaceholder")}
          />
        </label>
        <label>
          {t("history.filters.mode")}
          <select value={filters.mode} onChange={(e) => updateFilter("mode", e.target.value)}>
            <option value="">{t("history.filters.all")}</option>
            <option value="simulator">{t("history.filters.modeOptions.simulator")}</option>
            <option value="alog_playback">{t("history.filters.modeOptions.playback")}</option>
            <option value="modbus_live">{t("history.filters.modeOptions.modbusLive")}</option>
            <option value="ms6514_live">{t("history.filters.modeOptions.ms6514Live")}</option>
            <option value="aillio_live">{t("history.filters.modeOptions.aillioLive")}</option>
            <option value="tc4_live">{t("history.filters.modeOptions.tc4Live")}</option>
          </select>
        </label>
        <label>
          {t("history.filters.status")}
          <select value={filters.status} onChange={(e) => updateFilter("status", e.target.value)}>
            <option value="">{t("history.filters.all")}</option>
            <option value="roasting">{t("history.filters.statusRoasting")}</option>
            <option value="cooling">{t("history.filters.statusCooling")}</option>
            <option value="complete">{t("history.filters.statusComplete")}</option>
            <option value="stopped">{t("history.filters.statusStopped")}</option>
            <option value="aborted">{t("history.filters.statusAborted")}</option>
          </select>
        </label>
        <label>
          {t("history.filters.tag")}
          <select value={filters.tag} onChange={(e) => updateFilter("tag", e.target.value)}>
            <option value="">{t("history.filters.all")}</option>
            {allTags.map((tagItem) => (
              <option key={tagItem.tag} value={tagItem.tag}>
                {tagItem.tag} ({tagItem.count})
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("history.filters.roastedBy")}
          <select value={filters.created_by} onChange={(e) => updateFilter("created_by", e.target.value)}>
            <option value="">{t("history.filters.all")}</option>
            {allRoasters.map((r) => (
              <option key={r.created_by_username} value={r.created_by_username}>
                {r.created_by_username} ({r.count})
              </option>
            ))}
          </select>
        </label>
        {anyFilter && (
          <button type="button" className="filters-clear" onClick={clearFilters}>
            {t("history.filters.clearFilters")}
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
                placeholder={t("history.selection.openSavedComparison")}
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
                <strong>{t("history.selection.selected", { count: selectedIds.size })}</strong>
                {offPageSelected > 0 ? t("history.selection.offPage", { count: offPageSelected }) : ""}
              </span>
              {selectedIds.size > maxCompare && <span className="hint">{t("history.selection.compareLimitHint", { max: maxCompare })}</span>}
              <button
                type="button"
                onClick={startCompare}
                disabled={!compareOpen && (selectedIds.size < 2 || selectedIds.size > maxCompare)}
                title={selectedIds.size < 2 && !compareOpen ? t("history.selection.compareTitleMin") : t("history.selection.compareTitleMax", { max: maxCompare })}
              >
                {t("history.selection.compare")}
              </button>
              <button type="button" className="danger" disabled={deletingSelected} onClick={handleDeleteSelected}>
                {deletingSelected ? t("history.selection.deleting") : t("history.selection.delete")}
              </button>
              {selectedIds.size === 1 && <span className="hint">{t("history.selection.tickOneMore")}</span>}
              {compareNote && !compareOpen && <span className="error">{compareNote}</span>}
              {compareOpen && leftOut.length > 0 && (
                <span className="error">{t("history.selection.leftOut", { names: leftOut.map((id) => `"${titleFor(id)}"`).join(", ") })}</span>
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
          {t("history.table.selectAll")}
        </label>
        <div className="table-scroll">
          <table className="roast-table history-table">
            <thead>
              <tr>
                <th className="col-select">
                  <input
                    type="checkbox"
                    aria-label={t("history.table.selectAll")}
                    checked={allSelected}
                    ref={(el) => el && (el.indeterminate = someSelected && !allSelected)}
                    onChange={toggleSelectAll}
                    disabled={roasts.length === 0}
                  />
                </th>
                <th>{t("history.table.roast")}</th>
                <th>{t("history.table.mode")}</th>
                <th>{t("history.table.status")}</th>
                <th>{t("history.table.duration")}</th>
                <th>{t("history.table.tags")}</th>
                <th>{t("history.table.created")}</th>
                <th className="col-actions"></th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr className="table-message">
                  <td colSpan={8}>{t("history.table.loading")}</td>
                </tr>
              )}
              {!loading && roasts.length === 0 && (
                <tr className="table-message">
                  <td colSpan={8}>{anyFilter ? t("history.table.noMatch") : t("history.table.noneYet")}</td>
                </tr>
              )}
              {roasts.map((r) => {
                const created = formatCreated(r.created_at);
                return (
                  <tr key={r.id}>
                    <td className="cell-select">
                      <input
                        type="checkbox"
                        aria-label={t("history.table.selectRoast", { title: r.title })}
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
                    <td className="cell-mode">{r.mode === "alog_playback" && !r.source_alog_path ? t("history.modeLabels.uploadedLog") : MODE_LABELS[r.mode] || r.mode}</td>
                    <td className="cell-status">
                      <span className={`status-pill status-${r.status}`}>{STATUS_LABELS[r.status] || r.status}</span>
                      {isEmergencyStopped(r) && (
                        <span className="row-flag row-flag-emergency-stop" title={t("history.table.emergencyStoppedTitle")}>
                          {t("history.table.emergencyStoppedIcon")}
                        </span>
                      )}
                      {endedBeforeDrop(r) && (
                        <span className="row-flag row-flag-incomplete" title={t("history.table.endedBeforeDropTitle")}>
                          {t("history.table.endedBeforeDropIcon")}
                        </span>
                      )}
                    </td>
                    <td className="cell-duration">{formatSeconds(r.duration_s)}</td>
                    <td className="cell-tags">
                      {r.tags && r.tags.length > 0
                        ? r.tags.map((tagValue) => (
                            <span key={tagValue} className="tag-chip">
                              {tagValue}
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
                        {t("history.table.view")}
                      </Link>
                      <button type="button" className="btn-sm btn-danger-soft" onClick={() => handleDelete(r.id, r.title)}>
                        {t("history.table.delete")}
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
              {t("history.table.pagePrev")}
            </button>
            <span>{t("history.table.pageOf", { page, totalPages, count: totalCount })}</span>
            <button type="button" disabled={page >= totalPages || loading} onClick={() => setPage((p) => p + 1)}>
              {t("history.table.pageNext")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
