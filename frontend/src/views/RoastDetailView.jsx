import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import { api } from "../api/client.js";
import RoastChart from "../components/RoastChart.jsx";
import AddMilestoneControl from "../components/AddMilestoneControl.jsx";
import { diffMilestoneEvents, nearestProfileBt } from "../lib/milestoneEdits.js";
import RoastReviewCard from "../components/RoastReviewCard.jsx";
import { isSimulatedRoast } from "../simulated.js";
import { endedBeforeDrop, isEmergencyStopped } from "../roastFlags.js";
import RoastStatsPanel from "../components/RoastStatsPanel.jsx";
import RoastNumbers from "../components/RoastNumbers.jsx";
import OutcomePanel from "../components/OutcomePanel.jsx";
import BeansCard from "../components/BeansCard.jsx";
import BeansField from "../components/BeansField.jsx";
import NotesPanel from "../components/NotesPanel.jsx";
import WeightField from "../components/WeightField.jsx";
import { formatTime } from "../chartDefaults.js";
import { formatEventValue } from "../lib/eventFormat.js";
import { formatSeconds } from "../lib/metricFormat.js";
import { formatTemp } from "../tempUnits.js";
import { downloadRoastPdf, printRoastPdf } from "../lib/roastPdf.js";

// Mirrors the Configure Roast form's <option> labels (LiveRoastView.jsx)
// so history shows the same human-readable name, not the raw mode enum.
// modbus_live covers both USB and Ethernet (see roast.modbus_transport --
// mode alone can't distinguish them, same reasoning as the Configure Roast
// form's own Connection type dropdown), so this is a function, not a plain
// lookup, for that one entry.
function modeLabel(roast, t) {
  if (roast.mode === "modbus_live") {
    return roast.modbus_transport === "tcp"
      ? t("liveRoast.connectionOptions.modbusEthernet")
      : t("liveRoast.connectionOptions.modbusUsb");
  }
  const MODE_LABELS = {
    simulator: t("liveRoast.connectionOptions.simulator"),
    alog_playback: t("liveRoast.connectionOptions.alogPlayback"),
    ms6514_live: t("liveRoast.connectionOptions.ms6514"),
    aillio_live: t("liveRoast.connectionOptions.aillio"),
    tc4_live: t("liveRoast.connectionOptions.tc4"),
    plugin_live: roast.plugin_kind ? `${t("liveRoast.connectionOptions.plugin")} (${roast.plugin_kind})` : t("liveRoast.connectionOptions.plugin"),
  };
  return MODE_LABELS[roast.mode] || roast.mode;
}

// Mirrors backend/app/api/roasts.py's alog_filename() exactly -- same
// input (title + the raw created_at ISO string, sliced not reformatted),
// so this label always matches the filename the browser actually saves,
// without a round trip to ask the server what it named it.
// eslint-disable-next-line no-control-regex -- deliberate: matches
// backend/app/api/roasts.py's _UNSAFE_FILENAME_CHARS exactly, including
// C0 control characters (a title with an embedded CR/LF is a real
// header-injection surface server-side, not just a cosmetic filename
// issue -- see that file's own comment on the shared regex).
const UNSAFE_FILENAME_CHARS = /[\\/:*?"<>|\x00-\x1f]/g;

function alogFilename(title, createdAt) {
  const safeTitle = title.replace(UNSAFE_FILENAME_CHARS, "_").trim() || "roast";
  const timestamp = createdAt.slice(0, 16).replace("T", "_").replace(":", "");
  return `${safeTitle}_${timestamp}.alog`;
}

// Same convention, mirrors backend/app/api/roasts.py's csv_filename().
function csvFilename(title, createdAt) {
  const safeTitle = title.replace(UNSAFE_FILENAME_CHARS, "_").trim() || "roast";
  const timestamp = createdAt.slice(0, 16).replace("T", "_").replace(":", "");
  return `${safeTitle}_${timestamp}.csv`;
}

// Same convention, one per other download format.
// `ext` includes its own leading "." (or, for the roast-log CSV, "_log.csv") so
// this can produce a name distinct from csvFilename's own (different-shaped)
// .csv -- matching backend/app/api/roasts.py's roastlog_csv_filename.
function otherFilename(title, createdAt, ext) {
  const safeTitle = title.replace(UNSAFE_FILENAME_CHARS, "_").trim() || "roast";
  const timestamp = createdAt.slice(0, 16).replace("T", "_").replace(":", "");
  return `${safeTitle}_${timestamp}${ext.startsWith("_") ? ext : `.${ext}`}`;
}

// The chart's height, saved per browser (same idea as LiveRoastView's own
// chart height). The resize handle can make it shorter too now, down to
// a fixed floor (DETAIL_CHART_MIN_HEIGHT) -- it used to clamp to whatever
// height the page happened to *load* with instead, which meant a chart
// that loaded tall (an old saved height, or a bumped-up default) could
// only ever get taller from there, never actually shrunk back down.
// Matches LiveRoastView's own CHART_MIN_HEIGHT floor, same reasoning.
// Renamed (was "roast-telemetry:roastDetailChartHeight") when the default
// below dropped from 420 to 280 -- an old saved height from before that
// change was almost always bigger than the new intended default, which
// would have silently kept showing the old, too-tall chart forever (the
// saved value always wins over the default -- see readSavedDetailChartHeight
// below). A new key means every browser picks up the smaller default once,
// the same as a fresh profile would, instead of being stuck on whatever
// got saved under the old key.
const DETAIL_CHART_HEIGHT_KEY = "roast-telemetry:roastDetailChartHeight:v2";
const DETAIL_CHART_DEFAULT_HEIGHT = 280;
const DETAIL_CHART_MIN_HEIGHT = 260;
// Matches LiveRoastView's own CHART_MAX_HEIGHT -- nothing stopped the
// handle being dragged to an absurd height before this.
const DETAIL_CHART_MAX_HEIGHT = 900;
function readSavedDetailChartHeight() {
  const saved = typeof window !== "undefined" && Number(localStorage.getItem(DETAIL_CHART_HEIGHT_KEY));
  // Clamped against the max too, not just on the next drag -- a height
  // saved before that ceiling existed could otherwise still load
  // oversized once, on this one render, before any interaction.
  return saved >= DETAIL_CHART_MIN_HEIGHT ? Math.min(saved, DETAIL_CHART_MAX_HEIGHT) : DETAIL_CHART_DEFAULT_HEIGHT;
}

export default function RoastDetailView() {
  const { t } = useTranslation();
  const { id } = useParams();
  const [roast, setRoast] = useState(null);
  const [error, setError] = useState(null);
  const [numbersKey, setNumbersKey] = useState(0);
  const [beansText, setBeansText] = useState("");
  const [tempUnit, setTempUnit] = useState("c"); // display only, see Settings > Temperature Unit
  // Deliberately separate from `error` above -- that one *replaces the
  // whole page* (see the early-return a few lines down), which is right
  // for "the roast itself failed to load" but way too disruptive for a
  // failed milestone delete/retime on an otherwise-fine page.
  const [milestoneError, setMilestoneError] = useState(null);
  // A working copy of roast.events that add/delete/retime stage into
  // without touching the server -- Save sends the diff against
  // roast.events (see diffMilestoneEvents), Cancel just drops this back
  // to roast.events. Kept in sync with roast.events only at load time and
  // after a successful Save (reloadAfterMilestoneEdit), never by a plain
  // effect on `roast` -- that object gets a new reference on every
  // unrelated edit too (tags, beans, notes, weights), which would
  // otherwise silently wipe out in-progress milestone edits.
  const [displayedEvents, setDisplayedEvents] = useState([]);
  const [savingMilestones, setSavingMilestones] = useState(false);
  const [newTagInput, setNewTagInput] = useState("");
  const [tagsError, setTagsError] = useState(null);
  const [allTags, setAllTags] = useState([]); // feeds the <datalist> below -- existing tags to autocomplete against
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfError, setPdfError] = useState(null);
  const chartRef = useRef(null); // gives the PDF button a toImage() of exactly what's on screen
  const [chartHeight, setChartHeight] = useState(readSavedDetailChartHeight);
  const chartDragRef = useRef(null);

  function handleChartResizePointerDown(e) {
    chartDragRef.current = { startY: e.clientY, startHeight: chartHeight };
    e.currentTarget.classList.add("dragging");
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function handleChartResizePointerMove(e) {
    if (!chartDragRef.current) return;
    const { startY, startHeight } = chartDragRef.current;
    setChartHeight(Math.min(DETAIL_CHART_MAX_HEIGHT, Math.max(DETAIL_CHART_MIN_HEIGHT, startHeight + (e.clientY - startY))));
  }

  function handleChartResizePointerUp(e) {
    if (!chartDragRef.current) return;
    chartDragRef.current = null;
    e.currentTarget.classList.remove("dragging");
    e.currentTarget.releasePointerCapture(e.pointerId);
    localStorage.setItem(DETAIL_CHART_HEIGHT_KEY, String(chartHeight));
  }

  useEffect(() => {
    api
      .getRoast(id)
      .then((r) => {
        setRoast(r);
        setBeansText(r.beans || "");
        setDisplayedEvents(r.events);
      })
      .catch((err) => setError(err.message));
  }, [id]);

  // Fetched once on mount, same tradeoff as tempUnit's getSettings() call
  // below -- a tag added elsewhere mid-session won't appear in the
  // suggestions until next reload.
  useEffect(() => {
    api.listTags().then(setAllTags);
  }, []);

  // The Beans field saves when it's left, on Enter, or when a suggestion is
  // picked -- only if the name actually changed.
  async function saveBeans(text) {
    const next = text.trim();
    if (next === (roast.beans || "")) return;
    try {
      const saved = await api.setRoastBeans(id, next || null);
      setRoast((r) => (r ? { ...r, beans: saved.beans, bean_id: saved.bean_id } : r));
      setBeansText(saved.beans || "");
      setNumbersKey((k) => k + 1);
    } catch (err) {
      setMilestoneError(err.message);
    }
  }

  async function handleSaveGreenWeight(grams) {
    await api.setWeightGreen(id, grams);
    setRoast((r) => (r ? { ...r, weight_green_g: grams } : r));
    setNumbersKey((k) => k + 1);
  }

  async function handleDeleteGreenWeight() {
    await api.deleteWeightGreen(id);
    setRoast((r) => (r ? { ...r, weight_green_g: null } : r));
    setNumbersKey((k) => k + 1);
  }

  async function handleSaveRoastedWeight(grams) {
    await api.setWeightRoasted(id, grams);
    setRoast((r) => (r ? { ...r, weight_roasted_g: grams } : r));
    setNumbersKey((k) => k + 1);
  }

  async function handleDeleteRoastedWeight() {
    await api.deleteWeightRoasted(id);
    setRoast((r) => (r ? { ...r, weight_roasted_g: null } : r));
    setNumbersKey((k) => k + 1);
  }

  // Both add and remove go through this one function -- always PUTting
  // the roast's *entire* tag list (set_roast_tags' replace-the-whole-set
  // semantics), not a single add/remove call, so there's only one code
  // path to get right rather than two nearly-identical ones.
  async function handleTagsChange(nextTags) {
    setTagsError(null);
    try {
      await api.setTags(id, nextTags);
      setRoast((r) => (r ? { ...r, tags: nextTags } : r));
    } catch (err) {
      setTagsError(err.message);
    }
  }

  function handleAddTag() {
    const tag = newTagInput.trim();
    if (!tag || roast.tags.includes(tag)) {
      setNewTagInput("");
      return;
    }
    setNewTagInput("");
    handleTagsChange([...roast.tags, tag]);
  }

  function handleRemoveTag(tag) {
    handleTagsChange(roast.tags.filter((t) => t !== tag));
  }

  // No websocket on this page (one-time REST fetch, see the effect
  // above) -- unlike LiveRoastView.jsx, there's no server-pushed
  // "event_deleted"/"event_updated" message to pick up. A milestone edit
  // can change more than just the events list though -- duration and
  // reached_drop both get recomputed server-side (RoastSession.
  // _refresh_duration/_refresh_reached_drop), and the separate Stats/
  // Numbers panels below are their own GETs keyed off the milestones --
  // so on success this refetches the whole roast rather than hand-
  // patching `events` alone, and bumps numbersKey so those panels follow.
  // useCallback (not a plain function) -- these are handed to RoastChart
  // as onDeleteEvent/onRetimeEvent, which flow into its own memoized
  // `options` object (via handleDragMove/handleDragEnd's own deps). A new
  // function identity here on *every* render -- even one triggered by
  // something unrelated, like typing in the Add Tag box -- gave `options`
  // a new identity too, which made react-chartjs-2 call chart.update()
  // and reapply the x-axis's fixed min/max from options, silently
  // discarding whatever zoom/pan the user had applied. Confirmed live:
  // zooming in, then typing into the unrelated tag input, reset the
  // chart's visible range back to the full roast every time -- so this
  // (and everything it depends on) has to stay referentially stable too.
  const reloadAfterMilestoneEdit = useCallback(async () => {
    try {
      const fresh = await api.getRoast(id);
      setRoast(fresh);
      setDisplayedEvents(fresh.events); // drop back to the server's own copy -- Save/reset just committed
    } catch (err) {
      setMilestoneError(err.message);
    }
    setNumbersKey((k) => k + 1);
  }, [id]);

  // None of the three below touch the server at all -- they only stage a
  // change into displayedEvents, so a stray drag/click can't silently
  // commit anything. handleSaveMilestoneEdits (below) is the only thing
  // that ever calls the add/delete/retime API.
  const handleDeleteMilestone = useCallback((eventId) => {
    setDisplayedEvents((events) => events.filter((e) => e.id !== eventId));
  }, []);

  const handleRetimeMilestone = useCallback(
    (eventId, timeS) => {
      // Recomputed client-side from the profile, not just copied from the
      // old position -- the dot is plotted straight from `value` (see
      // RoastChart.jsx's markerPosition), so leaving the stale pre-drag
      // reading in place would leave it floating off the BT line at the
      // new x position instead of riding it like it does everywhere else.
      // The server redoes this same nearest-sample snap for real on Save.
      const value = nearestProfileBt(roast.profile, timeS);
      setDisplayedEvents((events) => events.map((e) => (e.id === eventId ? { ...e, time_s: timeS, value } : e)));
    },
    [roast]
  );

  // Always succeeds (nothing to fail -- there's no request yet), unlike
  // before this staged -- kept returning true so AddMilestoneControl's
  // own success-closes-the-picker logic still works unchanged.
  const handleAddMilestone = useCallback(
    (eventType, timeS) => {
      const value = nearestProfileBt(roast.profile, timeS);
      setDisplayedEvents((events) => [
        ...events,
        { id: `pending-${eventType}`, type: eventType, label: eventType, time_s: timeS, value, channel: null },
      ]);
      return true;
    },
    [roast]
  );

  const pendingMilestoneOps = useMemo(
    () => (roast ? diffMilestoneEvents(roast.events, displayedEvents) : []),
    [roast, displayedEvents]
  );

  const handleCancelMilestoneEdits = useCallback(() => {
    setMilestoneError(null);
    setDisplayedEvents(roast.events);
  }, [roast]);

  const handleSaveMilestoneEdits = useCallback(async () => {
    setMilestoneError(null);
    setSavingMilestones(true);
    try {
      // Deletes first (frees up the ordering room a retime/add into that
      // same slot might need), then everything else in ascending target-
      // time order, so each one's "already there" neighbors are as likely
      // as possible to already be in their final position by the time it
      // lands -- not a guarantee the backend's own neighbor-order check
      // can't still reject (see RoastChart.jsx's own comment on this),
      // just the ordering least likely to trip it for the common case of
      // a couple of edits at once.
      const deletes = pendingMilestoneOps.filter((op) => op.kind === "delete");
      const rest = pendingMilestoneOps.filter((op) => op.kind !== "delete").sort((a, b) => a.time_s - b.time_s);
      for (const op of deletes) await api.deleteEvent(id, op.id);
      for (const op of rest) {
        if (op.kind === "retime") await api.retimeEvent(id, op.id, op.time_s);
        else await api.addEvent(id, { type: op.type, label: op.type, time_s: op.time_s });
      }
      await reloadAfterMilestoneEdit();
    } catch (err) {
      // Left staged on purpose -- displayedEvents isn't touched here, so
      // whatever didn't make it (and whatever hadn't been tried yet)
      // stays on screen for the user to fix and retry, instead of
      // vanishing along with the one op that actually failed.
      setMilestoneError(err.message);
    } finally {
      setSavingMilestones(false);
    }
  }, [id, pendingMilestoneOps, reloadAfterMilestoneEdit]);

  // Only meaningful once a snapshot actually exists (see backend's
  // original_events_json) -- null for a roast that finished before this
  // feature shipped, or one still in progress; the Reset button itself is
  // hidden in that case (see the render below), this just backs that.
  const originalMilestoneOps = useMemo(
    () => (roast?.original_events ? diffMilestoneEvents(roast.original_events, displayedEvents) : []),
    [roast, displayedEvents]
  );

  const handleResetMilestonesToOriginal = useCallback(async () => {
    if (!window.confirm(t("roastDetail.milestoneEdits.resetConfirm"))) return;
    setMilestoneError(null);
    setSavingMilestones(true);
    try {
      await api.resetEvents(id);
      await reloadAfterMilestoneEdit();
    } catch (err) {
      setMilestoneError(err.message);
    } finally {
      setSavingMilestones(false);
    }
  }, [id, reloadAfterMilestoneEdit, t]);

  // Pulls the same numbers the Roast Stats/Numbers panels below already
  // show (they're separate GETs, not part of the Roast object itself),
  // grabs a snapshot of the on-screen chart, and hands it all to the PDF
  // builder. Errors here (e.g. no numbers yet for a roast with no Charge
  // marked) still produce a PDF -- buildDoc just skips whatever's missing.
  // Shared by both Download PDF and Print report, so they always build
  // from the exact same data.
  async function loadPdfExtra() {
    const [stats, numbersRow, metricsMeta] = await Promise.all([
      api.getRoastStats(id).catch(() => null),
      api.getRoastNumbers(id).catch(() => null),
      api.getAnalysisMetrics().catch(() => []),
    ]);
    return { stats, numbersRow, metricsMeta };
  }

  async function handleDownloadPdf() {
    setPdfError(null);
    setPdfBusy(true);
    try {
      const extra = await loadPdfExtra();
      const chartImage = chartRef.current?.toImage() || null;
      downloadRoastPdf(roast, tempUnit, chartImage, extra);
    } catch (err) {
      setPdfError(err.message || t("roastDetail.downloads.pdfErrorFallback"));
    } finally {
      setPdfBusy(false);
    }
  }

  // "Print report": the same document as Download PDF, opened in a new tab
  // with its print dialog already up -- see lib/roastPdf.js's printRoastPdf
  // for why this isn't a separate, page-CSS-driven print instead.
  async function handlePrint() {
    setPdfError(null);
    setPdfBusy(true);
    try {
      const extra = await loadPdfExtra();
      const chartImage = chartRef.current?.toImage() || null;
      printRoastPdf(roast, tempUnit, chartImage, extra);
    } catch (err) {
      setPdfError(err.message || t("roastDetail.downloads.pdfErrorFallback"));
    } finally {
      setPdfBusy(false);
    }
  }

  // "Weight loss" convention (a negative percentage,
  // e.g. "-13.2%") -- roast_review.py computes the same ratio but as a
  // positive "percent lost" for the AI review prompt; this is purely a
  // different display convention for the same underlying numbers, not a
  // second formula.
  const weightLossPct =
    roast?.weight_green_g && roast?.weight_roasted_g != null
      ? (((roast.weight_roasted_g / roast.weight_green_g) - 1) * 100).toFixed(1)
      : null;

  // One-time fetch, not the live SSE subscription LiveRoastView uses --
  // a finished roast's page doesn't need to react to a setting saved in
  // another tab while it's open.
  useEffect(() => {
    api.getSettings().then((s) => setTempUnit(s.temperature_unit || "c"));
  }, []);

  if (error) return <p className="error panel">{error}</p>;
  if (!roast) return <p className="panel">{t("roastDetail.loading")}</p>;

  return (
    <div className="detail-view">
      <div className="panel no-print">
        <h2>
          {roast.title}
          {isSimulatedRoast(roast) && (
            <span className="simulated-badge" title={t("roastDetail.simulatedBadgeTitle")}>
              {t("roastDetail.simulatedBadge")}
            </span>
          )}
          {isEmergencyStopped(roast) && (
            <span className="emergency-stop-badge" title={t("roastDetail.emergencyStoppedBadgeTitle")}>
              {t("roastDetail.emergencyStoppedBadge")}
            </span>
          )}
          {endedBeforeDrop(roast) && (
            <span className="incomplete-badge" title={t("roastDetail.endedBeforeDropBadgeTitle")}>
              {t("roastDetail.endedBeforeDropBadge")}
            </span>
          )}
        </h2>
        <p className="sub">
          {roast.mode} · {t("roastDetail.statusLine.status")} <strong>{roast.status}</strong> · {t("roastDetail.statusLine.duration")}{" "}
          {formatSeconds(roast.duration_s)}
        </p>
        <div className="detail-download-list">
          {roast.alog_path && (
            // No target="_blank" -- the response is Content-Disposition:
            // attachment, so it downloads without navigating away; adding
            // _blank just pops an empty new tab in some browsers while the
            // file downloads silently in the background, looking like a
            // no-op click. The native .alog format, which
            // compatible roasting software opens directly, no conversion needed.
            <p>
              {t("roastDetail.downloads.download")} <a href={api.alogDownloadUrl(roast.id)}>{alogFilename(roast.title, roast.created_at)}</a>
            </p>
          )}
          <p>
            {t("roastDetail.downloads.download")} <a href={api.csvDownloadUrl(roast.id)}>{csvFilename(roast.title, roast.created_at)}</a>
          </p>
          <p>
            {t("roastDetail.downloads.download")} <a href={api.jsonDownloadUrl(roast.id)}>{otherFilename(roast.title, roast.created_at, "json")}</a>
          </p>
          <p>
            {t("roastDetail.downloads.download")}{" "}
            <a href={api.roastlogCsvDownloadUrl(roast.id)}>{otherFilename(roast.title, roast.created_at, "_log.csv")}</a>{" "}
            {t("roastDetail.downloads.spreadsheetNote")}
          </p>
          <p>
            {t("roastDetail.downloads.download")} <a href={api.xlsxDownloadUrl(roast.id)}>{otherFilename(roast.title, roast.created_at, "xlsx")}</a>
          </p>
          <p>
            <button type="button" className="link-like" onClick={handleDownloadPdf} disabled={pdfBusy}>
              {pdfBusy ? t("roastDetail.downloads.buildingPdf") : t("roastDetail.downloads.downloadPdf")}
            </button>
            {" · "}
            <button type="button" className="link-like" onClick={handlePrint} disabled={pdfBusy}>
              {pdfBusy ? t("roastDetail.downloads.buildingPdf") : t("roastDetail.downloads.printReport")}
            </button>
          </p>
          {pdfError && <p className="error no-print">{pdfError}</p>}
        </div>
      </div>

      {/* Print-only header -- the interactive one above (with its
          download links/print button) is hidden when printing, so the
          report needs its own plain title/stat line to replace it. */}
      <div className="print-only detail-print-header">
        <h2>{roast.title}</h2>
        <p className="sub">
          {roast.mode} · {t("roastDetail.statusLine.status")} {roast.status} · {t("roastDetail.statusLine.duration")}{" "}
          {formatSeconds(roast.duration_s)}
        </p>
      </div>

      <div className="panel">
        <RoastChart
          ref={chartRef}
          profile={roast.profile}
          extraUnits={roast.extra_units}
          events={displayedEvents}
          tempUnit={tempUnit}
          height={chartHeight}
          // Editing (drag-to-retime, right-click-delete, the hover
          // affordance that goes with both) is only offered at all when
          // there's a real original_events snapshot to fall back to --
          // without onDeleteEvent/onRetimeEvent, RoastChart's own
          // onPanStart/handleContextMenu/handleCanvasMouseMove already
          // no-op (each already gates on these being set), so leaving
          // them unset here is the whole fix, nothing to change on that
          // side. A roast that finished before this feature existed has
          // no snapshot (see backend's original_events_json) and can
          // only ever get a Cancel-before-Save safety net, never a real
          // Reset -- simplest and safest is to not offer the edit at
          // all there rather than ship a weaker, inconsistent version
          // of it.
          onDeleteEvent={roast.original_events ? handleDeleteMilestone : undefined}
          onRetimeEvent={roast.original_events ? handleRetimeMilestone : undefined}
        />
        <div
          className="scope-chart-resize-handle no-print"
          title={t("roastDetail.resizeChartHandle")}
          onPointerDown={handleChartResizePointerDown}
          onPointerMove={handleChartResizePointerMove}
          onPointerUp={handleChartResizePointerUp}
        />
        {["complete", "stopped", "aborted"].includes(roast.status) && roast.original_events && (
          <AddMilestoneControl roast={{ ...roast, events: displayedEvents }} onAdd={handleAddMilestone} />
        )}
        {pendingMilestoneOps.length > 0 && (
          // Nothing above this point ever reaches the server by itself --
          // every add/delete/retime only stages into displayedEvents (see
          // those handlers' own comments), so a stray drag or an
          // accidental right-click-delete can always be walked back with
          // Cancel instead of being a live mistake the moment it happens.
          <div className="milestone-edit-actions no-print">
            <span>{t("roastDetail.milestoneEdits.pendingNotice", { count: pendingMilestoneOps.length })}</span>
            <button type="button" onClick={handleSaveMilestoneEdits} disabled={savingMilestones}>
              {savingMilestones ? t("roastDetail.milestoneEdits.saving") : t("roastDetail.milestoneEdits.save")}
            </button>
            <button type="button" className="link-like" onClick={handleCancelMilestoneEdits} disabled={savingMilestones}>
              {t("roastDetail.milestoneEdits.cancel")}
            </button>
          </div>
        )}
        {originalMilestoneOps.length > 0 && (
          <button type="button" className="link-like no-print" onClick={handleResetMilestonesToOriginal} disabled={savingMilestones}>
            {t("roastDetail.milestoneEdits.resetToOriginal")}
          </button>
        )}
        {milestoneError && <p className="error no-print">{milestoneError}</p>}
      </div>

      <div className="detail-grid">
        <div className="panel">
          <h3>{t("roastDetail.connectionType.heading")}</h3>
          <ul className="kv-list">
            <li>
              <span>{t("roastDetail.connectionType.mode")}</span>
              <span>{modeLabel(roast, t)}</span>
            </li>
            <li>
              <span>{t("roastDetail.connectionType.roastedBy")}</span>
              <span>{roast.created_by_username || "—"}</span>
            </li>
            {roast.mode === "alog_playback" && (
              <>
                <li>
                  <span>{t("roastDetail.connectionType.sourceFile")}</span>
                  <span>{roast.source_alog_path || "—"}</span>
                </li>
                <li>
                  <span>{t("roastDetail.connectionType.speed")}</span>
                  <span>{roast.playback_speed != null ? `${roast.playback_speed}x` : "—"}</span>
                </li>
              </>
            )}
            {roast.mode === "modbus_live" && (
              <>
                <li>
                  <span>{t("roastDetail.connectionType.connection")}</span>
                  <span>
                    {roast.modbus_transport === "tcp"
                      ? `${roast.modbus_host || "—"}:${roast.modbus_tcp_port || "—"}`
                      : roast.modbus_port || "—"}
                  </span>
                </li>
                <li>
                  <span>{t("roastDetail.connectionType.deviceProfile")}</span>
                  <span>{roast.modbus_device_profile_name || t("roastDetail.connectionType.customAdvanced")}</span>
                </li>
              </>
            )}
            {roast.mode === "ms6514_live" && (
              <li>
                <span>{t("roastDetail.connectionType.serialPort")}</span>
                <span>{roast.ms6514_port || "—"}</span>
              </li>
            )}
            {roast.mode === "aillio_live" && (
              <li>
                <span>{t("roastDetail.connectionType.model")}</span>
                <span>{roast.aillio_model ? `Aillio Bullet ${roast.aillio_model.toUpperCase()}` : "—"}</span>
              </li>
            )}
            {roast.mode === "tc4_live" && (
              <li>
                <span>{t("roastDetail.connectionType.serialPort")}</span>
                <span>{roast.tc4_port || "—"}</span>
              </li>
            )}
          </ul>

          <h3>{t("roastDetail.batch.heading")}</h3>
          <ul className="kv-list">
            <li className="beans-row">
              <span>{t("roastDetail.batch.beans")}</span>
              <span>
                <BeansField
                  label=""
                  value={beansText}
                  onChange={setBeansText}
                  onCommit={saveBeans}
                />
              </span>
            </li>
            <li>
              <span>{t("roastDetail.batch.tags")}</span>
              <span className="tag-edit-group">
                {roast.tags.map((tagValue) => (
                  <span key={tagValue} className="tag-chip">
                    {tagValue}
                    <button
                      type="button"
                      className="tag-chip-remove no-print"
                      onClick={() => handleRemoveTag(tagValue)}
                      aria-label={t("roastDetail.batch.removeTag", { tag: tagValue })}
                    >
                      ×
                    </button>
                  </span>
                ))}
                <span className="input-suffix-group no-print">
                  <input
                    type="text"
                    value={newTagInput}
                    onChange={(e) => setNewTagInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleAddTag();
                      }
                    }}
                    placeholder={t("roastDetail.batch.addTagPlaceholder")}
                    list="existing-tags"
                  />
                  <button type="button" onClick={handleAddTag} disabled={!newTagInput.trim()}>
                    {t("roastDetail.batch.add")}
                  </button>
                  {/* Native browser autocomplete against every tag used
                      anywhere -- no library needed, and it degrades to a
                      plain text input on anything that doesn't support
                      <datalist> (rare, but free either way). */}
                  <datalist id="existing-tags">
                    {allTags
                      .filter((tagItem) => !roast.tags.includes(tagItem.tag))
                      .map((tagItem) => (
                        <option key={tagItem.tag} value={tagItem.tag} />
                      ))}
                  </datalist>
                </span>
              </span>
            </li>
            {tagsError && (
              <li>
                <span></span>
                <span className="error">{tagsError}</span>
              </li>
            )}
            <li>
              <span>{t("roastDetail.batch.greenWeight")}</span>
              <span>
                <WeightField value={roast.weight_green_g} onSave={handleSaveGreenWeight} onDelete={handleDeleteGreenWeight} noPrint />
              </span>
            </li>
            <li>
              <span>{t("roastDetail.batch.roastedWeight")}</span>
              <span>
                <WeightField value={roast.weight_roasted_g} onSave={handleSaveRoastedWeight} onDelete={handleDeleteRoastedWeight} noPrint />
              </span>
            </li>
            {weightLossPct != null && (
              <li>
                <span>{t("roastDetail.batch.weightLoss")}</span>
                <span>{weightLossPct}%</span>
              </li>
            )}
          </ul>
        </div>

        <BeansCard beanId={roast.bean_id} refreshKey={numbersKey} />

        <div className="panel">
          <h3>{t("roastDetail.roastStatsHeading")}</h3>
          <RoastStatsPanel roastId={roast.id} refreshKey={numbersKey} />
        </div>

        <div className="panel">
          <h3>{t("roastDetail.numbersHeading")}</h3>
          <RoastNumbers roastId={roast.id} tempUnit={tempUnit} refreshKey={numbersKey} />
        </div>

        <OutcomePanel
          roast={roast}
          onSaved={(saved) => {
            setRoast((r) => (r ? { ...r, ...saved } : r));
            setNumbersKey((k) => k + 1);
          }}
        />

        <div className="panel">
          <h3>{t("roastDetail.eventsHeading")}</h3>
          <ul className="event-feed">
            {roast.events.map((ev) => (
              <li key={ev.id}>
                <strong>{ev.label}</strong> @ {formatTime(ev.time_s)}
                {formatEventValue(ev, formatTemp, tempUnit)}
              </li>
            ))}
          </ul>
        </div>

        <NotesPanel
          roastId={roast.id}
          notes={roast.notes}
          onReplace={(notes) => setRoast((r) => (r ? { ...r, notes } : r))}
        />
      </div>

      <RoastReviewCard roastId={roast.id} roastActive={roast.status === "roasting" || roast.status === "cooling"} />
    </div>
  );
}
