import {
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
} from "chart.js";
import zoomPlugin from "chartjs-plugin-zoom";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Line } from "react-chartjs-2";
import { api } from "../api/client.js";
import { formatTime, rorAxisRange, seriesLineWidth, tempAxisMax, tempAxisMin, timeAxisFor } from "../chartDefaults.js";
import { celsiusToUnit, unitSuffix } from "../tempUnits.js";
import { TERM_TOOLTIPS } from "../termTooltips.js";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend, zoomPlugin);

const EVENT_COLORS = {
  CHARGE: "#2563eb",
  TURNING_POINT: "#0891b2",
  DRY_END: "#ca8a04",
  FC_START: "#dc2626",
  FC_END: "#b91c1c",
  SC_START: "#9333ea",
  SC_END: "#7e22ce",
  DROP: "#16a34a",
  COOL_END: "#334155",
  CUSTOM: "#64748b",
};

// Continuous simulator control channels that map onto the same named
// channels used for manual burner/air/drum/damper events in .alog files.
const CONTINUOUS_FIELD_BY_CHANNEL = {
  Burner: "heater_pct",
  Air: "fan_pct",
  Drum: "drum_speed_pct",
  Damper: null,
};

// One entry per toggleable curve. `axis` names a scale defined in
// `options.scales` below; `source` says how to build its data.
const SERIES_DEFS = [
  { key: "BT", label: "BT", color: "#1d4ed8", axis: "yTemp", source: "profile", field: "bt", defaultOn: true },
  { key: "ET", label: "ET", color: "#be123c", axis: "yTemp", source: "profile", field: "et", defaultOn: true },
  // Drum space temperature -- a genuine third probe on machines that have
  // one (e.g. the FZ-94's slave-12 probe; see modbus_bridge/engine.py),
  // not a control value. profile[i].dt is null for every mode that
  // doesn't have one, same as any other absent channel -- off by default
  // since most modes never populate it.
  { key: "DT", label: "DT", color: "#c2410c", axis: "yTemp", source: "profile", field: "dt", defaultOn: false },
  { key: "ROR_BT", label: "RoR (BT)", color: "#1d4ed8", axis: "yRor", source: "profile", field: "ror_bt", defaultOn: true },
  { key: "ROR_ET", label: "RoR (ET)", color: "#be123c", axis: "yRor", source: "profile", field: "ror_et", defaultOn: false },
  { key: "Burner", label: "Burner", color: "#f59e0b", axis: "yControl", source: "channel", defaultOn: false },
  // key stays "Air" (matches CONTINUOUS_FIELD_BY_CHANNEL above, CHART_SERIES_KEYS
  // on the backend, and the channel value on historical manual-adjustment
  // events/.alog files); only the displayed label changed to "Fan".
  { key: "Air", label: "Fan", color: "#0891b2", axis: "yControl", source: "channel", defaultOn: false },
  { key: "Drum", label: "Drum", color: "#16a34a", axis: "yControl", source: "channel", defaultOn: false },
  { key: "Damper", label: "Damper", color: "#7c3aed", axis: "yControl", source: "channel", defaultOn: false },
];

// A reference roast's own BT/ET, time-aligned to Charge just like the
// live curves (both are already plotted against time_s from their own
// Charge event, so no re-alignment math is needed -- putting both on the
// same x-axis is the entire trick). Lighter/dashed so they read as "the
// thing you're chasing," not a second live curve -- the
// usual visual distinction for a background profile. Only
// built when a background roast is actually loaded (see
// backgroundSeriesDefs below), not part of the static SERIES_DEFS list
// above, so they never show up as an empty/disabled toggle when nothing's
// loaded.
function backgroundSeriesDefs(label) {
  const suffix = label ? ` (${label})` : " (bg)";
  return [
    { key: "BG_BT", label: `BT${suffix}`, color: "#93c5fd", axis: "yTemp", source: "background", field: "bt", defaultOn: true },
    { key: "BG_ET", label: `ET${suffix}`, color: "#fda4af", axis: "yTemp", source: "background", field: "et", defaultOn: true },
  ];
}

// Any role=EXTRA temperature channels a DeviceProfile declares (see the
// backend's ModbusTempChannel/RoastProfilePoint.extra) -- e.g. a roaster
// with a flue probe beyond BT/ET/DT. Same reasoning as
// backgroundSeriesDefs above: built dynamically (there's no fixed list,
// since which extra channels exist -- if any -- depends entirely on
// which device profile this roast used), not part of the static
// SERIES_DEFS list, so they never show up as an empty toggle otherwise.
const EXTRA_CHANNEL_COLORS = ["#0d9488", "#b45309", "#7c3aed", "#be185d"];
function extraSeriesDefs(labels) {
  return labels.map((label, i) => ({
    key: `EXTRA_${label}`,
    label,
    color: EXTRA_CHANNEL_COLORS[i % EXTRA_CHANNEL_COLORS.length],
    axis: "yTemp",
    source: "extra",
    field: label,
    defaultOn: true,
  }));
}

// The three classic roast phases, each bounded by a pair of named
// milestone events. Colors follow the common green/yellow/red convention
// (drying / Maillard-browning / development).
const PHASE_DEFS = [
  { key: "dry", label: "Dry", color: "#6ee7b7", fromType: "CHARGE", toType: "DRY_END" },
  { key: "maillard", label: "Maillard", color: "#fde68a", fromType: "DRY_END", toType: "FC_START" },
  { key: "dev", label: "Dev", color: "#fca5a5", fromType: "FC_START", toType: "DROP" },
];

function computePhases(events) {
  const byType = Object.fromEntries(events.map((e) => [e.type, e]));
  const charge = byType.CHARGE;
  const drop = byType.DROP;
  const total = charge && drop ? drop.time_s - charge.time_s : null;

  return PHASE_DEFS.map((def) => {
    const from = byType[def.fromType];
    const to = byType[def.toType];
    if (!from || !to) return null;
    const duration = to.time_s - from.time_s;
    return {
      ...def,
      start: from.time_s,
      end: to.time_s,
      duration,
      pct: total ? (duration / total) * 100 : null,
    };
  }).filter(Boolean);
}

// .alog files only log control-channel *adjustments* (discrete
// events), not a continuous stream -- reconstruct a step curve from them
// (flat between adjustments, matching what actually happened) rather
// than interpolating a slope that was never really there.
function stepCurveFromEvents(channelEvents, endTime) {
  if (!channelEvents.length) return [];
  const sorted = [...channelEvents].sort((a, b) => a.time_s - b.time_s);
  const points = sorted.map((e) => ({ x: e.time_s, y: e.value }));
  const last = points[points.length - 1];
  if (endTime != null && endTime > last.x) points.push({ x: endTime, y: last.y });
  return points;
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

// Draws the Dry/Maillard/Dev phase strip above the plot area (in the
// reserved top padding), each segment labeled with its duration and
// share of total roast time -- e.g. "Dry · 4:12 · 44.8%".
const phaseBandsPlugin = {
  id: "phaseBands",
  afterDraw(chart, _args, opts) {
    const phases = opts?.phases || [];
    if (!phases.length) return;
    const { ctx, chartArea, scales } = chart;
    const xScale = scales.x;
    const bandTop = chartArea.top - 34;
    const bandHeight = 18;
    ctx.save();
    ctx.font = "600 10px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    phases.forEach((p) => {
      const x0 = Math.max(chartArea.left, xScale.getPixelForValue(p.start));
      const x1 = Math.min(chartArea.right, xScale.getPixelForValue(p.end));
      if (x1 <= x0) return;
      ctx.fillStyle = p.color;
      ctx.fillRect(x0, bandTop, x1 - x0, bandHeight);
      if (x1 - x0 > 70) {
        const label = `${p.label} · ${formatTime(p.duration)}${p.pct != null ? ` · ${p.pct.toFixed(1)}%` : ""}`;
        ctx.fillStyle = "#1c1917";
        ctx.fillText(label, (x0 + x1) / 2, bandTop + bandHeight / 2);
      }
    });
    ctx.restore();
  },
};

// Alternating light/white horizontal bands are drawn
// behind the curves, keyed to the temperature axis gridlines.
const scopeBandsPlugin = {
  id: "scopeBands",
  beforeDraw(chart) {
    const { ctx, chartArea, scales } = chart;
    const scale = scales.yTemp;
    if (!chartArea || !scale) return;
    const ticks = scale.ticks;
    ctx.save();
    for (let i = 0; i < ticks.length - 1; i++) {
      if (i % 2 !== 0) continue;
      const yTop = scale.getPixelForTick(i + 1);
      const yBottom = scale.getPixelForTick(i);
      ctx.fillStyle = "rgba(15, 23, 42, 0.035)";
      ctx.fillRect(chartArea.left, yTop, chartArea.right - chartArea.left, yBottom - yTop);
    }
    ctx.restore();
  },
};

// Compact "F" / "F/min" unit labels pinned to the top
// corners of the chart, instead of full rotated axis titles.
const axisUnitLabelsPlugin = {
  id: "axisUnitLabels",
  afterDraw(chart, _args, opts) {
    const { ctx, chartArea } = chart;
    ctx.save();
    ctx.font = "600 12px system-ui, sans-serif";
    ctx.fillStyle = "#57534e";
    ctx.textBaseline = "bottom";
    ctx.textAlign = "left";
    if (opts.leftUnit) {
      ctx.fillText(opts.leftUnit, chartArea.left, chartArea.top - 40);
    }
    if (opts.rightUnit) {
      ctx.textAlign = "right";
      ctx.fillText(opts.rightUnit, chartArea.right, chartArea.top - 40);
    }
    ctx.restore();
  },
};

// A milestone is editable (right-click to delete, drag to retime) if
// it's something a human could have marked by hand in the first place --
// CUSTOM isn't a milestone at all (no marker is even drawn for it, see
// below), and TURNING_POINT is a pure auto-detected observation with no
// manual-mark path anywhere in the app (see backend's
// ALWAYS_AUTO_EVENT_TYPES) -- matches the backend's own edit guards
// exactly (RoastSession._find_editable_milestone et al), so a click here
// can never attempt something the API would just reject anyway.
export function isEditableMilestone(ev) {
  return ev.type !== "CUSTOM" && ev.type !== "TURNING_POINT";
}

// Shared by eventMarkersPlugin's draw path and hitTestMarker below, so
// the two can never quietly disagree about where a marker actually is.
// `dragPreviewTimeS` overrides ev.time_s for the one marker currently
// being dragged (see the mousedown/mousemove handlers further down) --
// read from `chart.$dragPreview`, a plain property stashed directly on
// the live Chart.js instance rather than threaded through React props,
// so a drag can redraw at 60fps via chart.update('none') without
// triggering a React re-render on every pixel of mouse movement.
function markerPosition(chart, ev, tempUnit, dragPreviewTimeS) {
  const { scales } = chart;
  const xScale = scales.x;
  const yScale = scales.yTemp;
  const timeS = dragPreviewTimeS != null ? dragPreviewTimeS : ev.time_s;
  const x = xScale.getPixelForValue(timeS);
  // ev.value is always raw Celsius (events aren't part of the
  // already-converted chart datasets below) -- must convert before using
  // it against a scale whose own values are now in tempUnit, or the dot
  // lands at a wildly wrong pixel position.
  const displayValue = ev.value != null ? celsiusToUnit(ev.value, tempUnit) : null;
  const dotY = displayValue != null && yScale ? yScale.getPixelForValue(displayValue) : chart.chartArea.bottom;
  return { x, dotY, timeS, displayValue };
}

// Dark rounded-rectangle callouts (label / time / value) anchored to each
// named milestone's point on the BT curve, with a stem + dot down to the
// actual point. Manual control-channel (CUSTOM) events are excluded here
// -- with dozens of those per roast, boxing each one would bury the
// chart; they're already visualized via the Burner/Fan/Drum/Damper step
// curves and listed in the Events panel instead.
const eventMarkersPlugin = {
  id: "eventMarkers",
  afterDatasetsDraw(chart, _args, opts) {
    const events = (opts?.events || []).filter((e) => e.type !== "CUSTOM");
    if (!events.length) return;
    const tempUnit = opts?.tempUnit || "c";
    const dragPreview = chart.$dragPreview;
    const { ctx, chartArea } = chart;
    ctx.save();
    events.forEach((ev) => {
      const dragging = dragPreview && dragPreview.eventId === ev.id;
      const { x, dotY, timeS, displayValue } = markerPosition(chart, ev, tempUnit, dragging ? dragPreview.timeS : null);
      if (x < chartArea.left || x > chartArea.right) return;
      const color = EVENT_COLORS[ev.type] || EVENT_COLORS.CUSTOM;

      const lines = [ev.label, formatTime(timeS), displayValue != null ? `${displayValue.toFixed(1)}${unitSuffix(tempUnit)}` : null].filter(Boolean);
      ctx.font = "9px system-ui, sans-serif";
      const boxWidth = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 14;
      const boxHeight = lines.length * 11 + 6;
      const boxY = Math.max(chartArea.top + 2, dotY - boxHeight - 14);
      const boxX = Math.min(Math.max(x - boxWidth / 2, chartArea.left), chartArea.right - boxWidth);

      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.setLineDash(dragging ? [3, 3] : []);
      ctx.beginPath();
      ctx.moveTo(x, dotY);
      ctx.lineTo(x, boxY + boxHeight);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, dotY, dragging ? 5 : 3.5, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = "rgba(28, 25, 23, 0.92)";
      roundRect(ctx, boxX, boxY, boxWidth, boxHeight, 4);
      ctx.fill();

      ctx.fillStyle = "#ffffff";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      lines.forEach((line, i) => {
        ctx.font = i === 0 ? "700 9px system-ui, sans-serif" : "9px system-ui, sans-serif";
        ctx.fillText(line, boxX + boxWidth / 2, boxY + 8 + i * 11);
      });
    });
    ctx.restore();
  },
};

ChartJS.register(eventMarkersPlugin, scopeBandsPlugin, axisUnitLabelsPlugin, phaseBandsPlugin);

const MARKER_HIT_RADIUS_PX = 12;

// Plain scroll over the chart used to zoom it directly -- confirmed live
// as a real problem: scrolling the page with the cursor resting over the
// chart accidentally zoomed instead. First fix tried Ctrl (Cmd on Mac)
// as the required modifier, since chartjs-plugin-zoom's wheel.modifierKey
// checks event[modifierKey + "Key"] directly (confirmed against the
// installed plugin's own source) -- but Ctrl/Cmd+scroll is *also* the
// browser's own native "zoom the whole page" gesture, and a trackpad
// pinch gets synthesized as a wheel event with ctrlKey already set, so
// the two collided: confirmed live, pinching over the chart zoomed the
// whole page instead (browser-level, not something this plugin's own
// preventDefault() reliably wins against across browsers). Alt isn't
// reserved by any browser for page-zoom or horizontal scroll (unlike
// Ctrl or Shift), so there's nothing left for it to collide with.
const WHEEL_ZOOM_MODIFIER_KEY = "alt";
// Display label only -- Mac calls this key "Option", everywhere else
// calls it "Alt"; the actual modifierKey value above is the same either
// way (event.altKey doesn't differ by OS).
const WHEEL_ZOOM_MODIFIER_LABEL =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || "")
    ? "Option"
    : "Alt";

// Finds the editable milestone (if any) whose dot sits within
// MARKER_HIT_RADIUS_PX of the given canvas-relative point -- shared by
// the context-menu (right-click) and drag-to-retime (onPanStart) entry
// points below, closest-first so two nearby markers never fight over an
// ambiguous click.
function hitTestMarker(chart, events, x, y, tempUnit) {
  let best = null;
  let bestDist = Infinity;
  for (const ev of events) {
    if (!isEditableMilestone(ev)) continue;
    const { x: mx, dotY } = markerPosition(chart, ev, tempUnit, null);
    const dist = Math.hypot(mx - x, dotY - y);
    if (dist <= MARKER_HIT_RADIUS_PX && dist < bestDist) {
      best = ev;
      bestDist = dist;
    }
  }
  return best;
}

// forwardRef so a parent (the PDF report -- see RoastDetailView.jsx) can pull
// a flat image of the current chart via ref.current.toImage() without this
// component's own internal Chart.js/menu/drag refs leaking out.
const RoastChart = forwardRef(function RoastChart({
  profile = [],
  events = [],
  background = [],
  backgroundLabel = null,
  height = 420,
  title,
  tempUnit = "c",
  // False only on LiveRoastView while a roast is actively roasting/cooling
  // -- zoom/pan and the hide-labels control are for reviewing a finished
  // curve, not for fighting with a chart whose x-axis is still growing
  // every second. Every other caller (history detail, or Live before
  // START / after STOP) leaves this at the default, fully interactive.
  interactive = true,
  // Right-click-to-delete / drag-to-retime an already-marked milestone
  // (item #7 from the real-hardware feedback round).
  // Both optional and independent of `interactive` above: that flag only
  // gates zoom/pan, but editing a milestone has to keep working during
  // an active roast too, per the user's own scoping answer ("both live
  // and afterward"). Omit either to render read-only (no callers do
  // today, but keeps this component honest about being usable that way).
  onDeleteEvent,
  onRetimeEvent,
}, ref) {
  const { t } = useTranslation();
  // A strict undefined check (not `??`) so an explicit `title={null}`
  // (LiveRoastView, which draws its own header elsewhere) still suppresses
  // the header entirely, while simply omitting the prop (RoastDetailView)
  // falls through to the translated default.
  const resolvedTitle = title === undefined ? t("common.roastChart.defaultTitle") : title;
  // Cheap to recompute every tick (profile grows every second during a
  // live roast anyway, same cost the data/availability useMemos below
  // already pay) but stable in *output* -- a sorted, joined string only
  // changes when the actual set of extra-channel labels changes, not on
  // every new sample, so it's what seriesDefs below depends on rather
  // than raw `profile` (which would otherwise churn the array reference,
  // and with it the visible-state merge effect, every single tick).
  const extraLabelsKey = useMemo(() => {
    const labels = new Set();
    for (const p of profile) for (const label of Object.keys(p.extra || {})) labels.add(label);
    return [...labels].sort().join("|");
  }, [profile]);

  // Static live channels plus, only while a background roast is actually
  // loaded, its two reference curves (see backgroundSeriesDefs above),
  // plus any extra temperature channels this roast's own profile data
  // actually has (see extraSeriesDefs above).
  const seriesDefs = useMemo(() => {
    let defs = SERIES_DEFS;
    if (background.length) defs = [...defs, ...backgroundSeriesDefs(backgroundLabel)];
    if (extraLabelsKey) defs = [...defs, ...extraSeriesDefs(extraLabelsKey.split("|"))];
    return defs;
  }, [background.length, backgroundLabel, extraLabelsKey]);

  const [visible, setVisible] = useState(() =>
    Object.fromEntries(SERIES_DEFS.map((s) => [s.key, s.defaultOn]))
  );
  // Holds the *full* settings object (not just chart_series_visible) --
  // PUT /api/settings has no partial-update semantics, it overwrites
  // every field with whatever the request body provides (see
  // backend/app/api/settings.py's update_settings docstring), so a
  // toggle here has to send the whole object back, not just this one
  // field, or every other setting (Ollama config, breakout panels,
  // vertical control layout) would get silently wiped to its default.
  const savedSettingsRef = useRef(null);
  useEffect(() => {
    let cancelled = false;
    api.getSettings().then((s) => {
      if (cancelled) return;
      savedSettingsRef.current = s;
      // Defaults first, saved overrides second -- a series that's never
      // been explicitly saved (a fresh install, or one added in a later
      // version) still gets its own correct defaultOn instead of being
      // forced off by an absent key.
      setVisible((prev) => ({ ...prev, ...s.chart_series_visible }));
    });
    return () => {
      cancelled = true;
    };
  }, []);
  const [hideEventLabels, setHideEventLabels] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  // Right-click context menu: {eventId, x, y} in viewport coordinates
  // (for CSS positioning), or null when closed.
  const [contextMenu, setContextMenu] = useState(null);
  const chartRef = useRef(null);
  useImperativeHandle(ref, () => ({ toImage: () => chartRef.current?.toBase64Image() }), []);
  const menuRef = useRef(null);
  const contextMenuRef = useRef(null);
  // Drag-to-retime state lives in a ref, not React state -- the
  // mousemove handler mutates chart.$dragPreview directly and calls
  // chart.update('none') itself (see startDrag below) specifically to
  // avoid a React re-render on every pixel of mouse movement.
  const dragRef = useRef(null);

  // Closes the right-click context menu on any click outside it, same
  // pattern as the chart-options popover below.
  useEffect(() => {
    if (!contextMenu) return undefined;
    function onPointerDown(e) {
      if (contextMenuRef.current && !contextMenuRef.current.contains(e.target)) setContextMenu(null);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [contextMenu]);

  // Closes the chart-options menu on any click outside it -- a plain
  // popover, no library, since it's just these two controls.
  useEffect(() => {
    if (!menuOpen) return undefined;
    function onPointerDown(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [menuOpen]);

  // Loading (or clearing) a background roast after mount introduces (or
  // drops) BG_BT/BG_ET keys -- this seeds any newly-appeared key at its
  // own defaultOn without touching whatever the user's already chosen
  // for every other channel. A key that disappears (background cleared)
  // is just left stale and unused in `visible`, harmless since the data
  // useMemo below only ever iterates the *current* seriesDefs.
  useEffect(() => {
    setVisible((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const s of seriesDefs) {
        if (!(s.key in next)) {
          next[s.key] = s.defaultOn;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [seriesDefs]);

  const endTime = profile.length ? profile[profile.length - 1].time_s : null;
  const phases = useMemo(() => computePhases(events), [events]);

  const availability = useMemo(() => {
    const out = {};
    for (const s of seriesDefs) {
      if (s.source === "profile") {
        out[s.key] = profile.some((p) => p[s.field] != null);
      } else if (s.source === "background") {
        out[s.key] = background.some((p) => p[s.field] != null);
      } else if (s.source === "extra") {
        out[s.key] = profile.some((p) => p.extra?.[s.field] != null);
      } else {
        const continuousField = CONTINUOUS_FIELD_BY_CHANNEL[s.key];
        const hasContinuous = continuousField && profile.some((p) => p[continuousField] != null);
        const hasEvents = events.some((e) => e.channel === s.key);
        out[s.key] = hasContinuous || hasEvents;
      }
    }
    return out;
  }, [seriesDefs, profile, events, background]);

  function toggle(key) {
    setVisible((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      // Fire-and-forget -- don't block the checkbox's own responsiveness
      // on the network round trip. Only persists for the static
      // SERIES_DEFS keys (see CHART_SERIES_KEYS on the backend); a
      // toggle on a dynamic background/extra-channel key (not one of
      // those) still updates local `visible` above but is silently
      // dropped by the backend's own filter rather than erroring, same
      // as any other unknown key sent to PUT /api/settings.
      if (savedSettingsRef.current) {
        const updated = {
          ...savedSettingsRef.current,
          chart_series_visible: { ...savedSettingsRef.current.chart_series_visible, [key]: next[key] },
        };
        savedSettingsRef.current = updated;
        api.saveSettings(updated).catch(() => {});
      }
      return next;
    });
  }

  const data = useMemo(() => {
    // Rates (°C/min) convert by scale only, no +32 offset -- a 5°C/min
    // rise is an 9°F/min rise, not "5°C/min converted as if it were a
    // temperature". Absolute temperatures (BT/ET/DT) use the real
    // celsiusToUnit conversion instead.
    const convertRor = (v) => (v == null ? null : tempUnit === "f" ? v * 1.8 : v);
    const datasets = seriesDefs.filter((s) => visible[s.key]).map((s) => {
      let points;
      let stepped = false;
      if (s.source === "profile") {
        const convert = s.axis === "yTemp" ? (v) => celsiusToUnit(v, tempUnit) : s.axis === "yRor" ? convertRor : (v) => v;
        points = profile.map((p) => ({ x: p.time_s, y: p[s.field] == null ? null : convert(p[s.field]) }));
      } else if (s.source === "background") {
        // Time-aligned to Charge (x = time_s), same as the live profile
        // above -- both roasts already measure time from their own
        // Charge event, so plotting them on the same x-axis is the whole
        // trick, no extra alignment needed.
        points = background.map((p) => ({ x: p.time_s, y: p[s.field] == null ? null : celsiusToUnit(p[s.field], tempUnit) }));
      } else if (s.source === "extra") {
        points = profile.map((p) => ({ x: p.time_s, y: p.extra?.[s.field] == null ? null : celsiusToUnit(p.extra[s.field], tempUnit) }));
      } else {
        const continuousField = CONTINUOUS_FIELD_BY_CHANNEL[s.key];
        const hasContinuous = continuousField && profile.some((p) => p[continuousField] != null);
        if (hasContinuous) {
          points = profile.map((p) => ({ x: p.time_s, y: p[continuousField] }));
          // Fan/Drum are Delta VFD-L drives that are actually just
          // on/off on the FZ-94 (confirmed against real hardware) -- the
          // real change between two samples is instant, not a ramp, so
          // the curve shouldn't imply one either. Burner (and Damper,
          // whenever it gets real data) stays smooth -- it's a genuine
          // continuous PID setpoint, not a binary drive.
          if (s.key === "Air" || s.key === "Drum") stepped = "before";
        } else {
          points = stepCurveFromEvents(events.filter((e) => e.channel === s.key), endTime);
          stepped = "before";
        }
      }
      return {
        label: s.label,
        data: points,
        // Control channels (Burner/Fan/Drum/Damper, 0-100) are drawn low on the
        // temperature axis, in its 0-100 band, rather than on an axis of their own.
        isControl: s.axis === "yControl",
        borderColor: s.color,
        backgroundColor: s.color,
        pointRadius: 0,
        borderWidth: seriesLineWidth(s.key, s.axis),
        borderDash: s.source === "background" ? [6, 3] : undefined,
        stepped,
        yAxisID: s.axis === "yControl" ? "yTemp" : s.axis,
        tension: stepped ? 0 : 0.15,
      };
    });
    return { datasets };
  }, [seriesDefs, profile, events, background, visible, endTime, tempUnit]);

  // The highest temperature on the chart, so the axis can grow past its default top.
  const tempDataMax = useMemo(() => {
    let max = null;
    for (const d of data.datasets) {
      if (d.yAxisID !== "yTemp" || d.isControl) continue;
      for (const p of d.data) if (p.y != null && (max == null || p.y > max)) max = p.y;
    }
    return max;
  }, [data]);
  const timeAxis = timeAxisFor(interactive, endTime);

  const showTemp = visible.BT || visible.ET;
  const showRor = visible.ROR_BT || visible.ROR_ET;
  const showControl = visible.Burner || visible.Air || visible.Drum || visible.Damper;

  // Drag-to-retime a milestone marker -- started from onPanStart below
  // (chartjs-plugin-zoom's own pan-gesture hook, the one sanctioned way
  // to veto/hijack a pan gesture conditionally: returning false from
  // onPanStart cancels the library's own pan for that gesture, see
  // node_modules/chartjs-plugin-zoom/dist/chartjs-plugin-zoom.esm.js's
  // startPan). From there, tracked with plain window listeners (not
  // Chart.js/Hammer's own pan machinery -- that gesture was cancelled)
  // so the drag keeps working even if the cursor leaves the canvas.
  // $dragPreview is stashed directly on the live Chart.js instance
  // (bypassing React state/props) so every pixel of movement is just
  // chart.update('none'), not a React re-render.
  const handleDragMove = useCallback(
    (e) => {
      const chart = chartRef.current;
      const drag = dragRef.current;
      if (!chart || !drag) return;
      const rect = chart.canvas.getBoundingClientRect();
      const canvasX = e.clientX - rect.left;
      let timeS = chart.scales.x.getValueForPixel(canvasX);
      // Clamped client-side only to the roast's overall recorded range,
      // for obvious live feedback -- neighbor-milestone ordering is
      // validated authoritatively by the backend on drop
      // (RoastSession._retime_milestone), not duplicated here.
      const profileEnd = profile.length ? profile[profile.length - 1].time_s : null;
      timeS = profileEnd != null ? Math.max(0, Math.min(profileEnd, timeS)) : Math.max(0, timeS);
      chart.$dragPreview = { eventId: drag.eventId, timeS };
      chart.update("none");
    },
    [profile]
  );

  const handleDragEnd = useCallback(() => {
    window.removeEventListener("mousemove", handleDragMove);
    window.removeEventListener("mouseup", handleDragEnd);
    const drag = dragRef.current;
    dragRef.current = null;
    const chart = chartRef.current;
    if (!drag || !chart) return;
    const preview = chart.$dragPreview;
    // Clear the preview immediately, before the retime API call even
    // resolves -- the marker visually reverts to its stored position
    // right away, then jumps to the new one once onRetimeEvent's own
    // state update lands. If the backend rejects the move (crosses a
    // neighboring milestone, say), that's the whole error UI: it just
    // never moves from the reverted position, same as a native
    // drag-and-drop rejection.
    chart.$dragPreview = null;
    chart.update("none");
    if (preview && onRetimeEvent) {
      Promise.resolve(onRetimeEvent(drag.eventId, preview.timeS)).catch((err) => {
        console.error("Failed to retime milestone:", err);
      });
    }
  }, [onRetimeEvent, handleDragMove]);

  // Catches a drag left in progress if this component unmounts mid-drag
  // (e.g. navigating away) -- addEventListener'd window listeners
  // otherwise leak past the component's own lifetime.
  useEffect(() => {
    return () => {
      window.removeEventListener("mousemove", handleDragMove);
      window.removeEventListener("mouseup", handleDragEnd);
    };
  }, [handleDragMove, handleDragEnd]);

  function handleContextMenu(e) {
    const chart = chartRef.current;
    if (!chart || !onDeleteEvent) return;
    const rect = chart.canvas.getBoundingClientRect();
    const hit = hitTestMarker(chart, events, e.clientX - rect.left, e.clientY - rect.top, tempUnit);
    if (!hit) return; // not on an editable marker -- let the browser's own context menu through
    e.preventDefault();
    setContextMenu({ eventId: hit.id, x: e.clientX, y: e.clientY });
  }

  const options = useMemo(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      parsing: false,
      normalized: true,
      interaction: { mode: "index", intersect: false },
      layout: { padding: { top: phases.length ? 56 : 20 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => (items.length ? formatTime(items[0].parsed.x) : ""),
            label: (item) => {
              // Data is already converted to tempUnit at the dataset level
              // (see the `data` useMemo above) -- only the unit suffix
              // needs to reflect that here, not the value itself.
              const suffix = item.dataset.isControl ? "" : item.dataset.yAxisID === "yTemp" ? unitSuffix(tempUnit) : item.dataset.yAxisID === "yRor" ? `${unitSuffix(tempUnit)}/min` : "";
              const value = item.parsed.y;
              return ` ${item.dataset.label}: ${value == null ? "—" : `${value.toFixed(1)}${suffix}`}`;
            },
          },
        },
        eventMarkers: { events: hideEventLabels ? [] : events, tempUnit },
        phaseBands: { phases },
        axisUnitLabels: { leftUnit: showTemp ? unitSuffix(tempUnit) : null, rightUnit: showRor ? `${unitSuffix(tempUnit)}/min` : null },
        // Time-axis only (not the temp/RoR/control y-axes) -- this is a
        // time-series chart with three differently-scaled y-axes already
        // fixed to sensible ranges (0-100 for the control lines' band on yTemp, a fixed RoR band,
        // etc.), so zooming those too would mostly just squash or stretch
        // curves rather than reveal anything. Wheel handles both a mouse
        // wheel and a trackpad's two-finger scroll; pinch covers an
        // actual trackpad/touchscreen pinch gesture. Drag-to-pan (no
        // modifier key) is safe alongside the existing hover tooltip --
        // that only fires on mousemove-without-a-button-down. All of it
        // off while !interactive (a live roast's x-axis is still growing
        // every second -- panning/zooming a range that keeps shifting
        // under you doesn't work well, so this is a reviewing-a-curve
        // feature, not a live one).
        zoom: {
          // pan.enabled stays true always -- unlike wheel/pinch zoom
          // below, pan-gesture recognition has to keep running even
          // while !interactive (an active roast) specifically so
          // onPanStart still fires and can hijack a drag that starts on
          // a milestone marker (drag-to-retime has to work live, not
          // just on a finished roast, per the user's own scoping
          // answer). Genuine chart-panning during a live roast is still
          // rejected -- just from inside onPanStart now, not by
          // disabling the gesture recognizer entirely.
          pan: {
            enabled: true,
            mode: "x",
            onPanStart: ({ chart, point }) => {
              const hit = onRetimeEvent ? hitTestMarker(chart, events, point.x, point.y, tempUnit) : null;
              if (hit) {
                dragRef.current = { eventId: hit.id };
                chart.$dragPreview = { eventId: hit.id, timeS: hit.time_s };
                window.addEventListener("mousemove", handleDragMove);
                window.addEventListener("mouseup", handleDragEnd);
                return false;
              }
              if (!interactive) return false; // not a marker, and panning itself is off right now (live roast)
              return undefined; // let normal panning proceed
            },
          },
          zoom: {
            wheel: { enabled: interactive, modifierKey: WHEEL_ZOOM_MODIFIER_KEY },
            pinch: { enabled: interactive },
            mode: "x",
          },
        },
      },
      scales: {
        x: {
          type: "linear",
          min: timeAxis.min,
          ...(timeAxis.max != null ? { max: timeAxis.max } : { suggestedMax: timeAxis.suggestedMax }),
          grid: { color: "#e7e5e4" },
          title: { display: true, text: t("common.roastChart.axisMins"), color: "#78716c" },
          ticks: { callback: (value) => formatTime(value), color: "#78716c", maxTicksLimit: 8, includeBounds: false },
        },
        yTemp: {
          type: "linear",
          position: "left",
          grid: { color: "#e7e5e4" },
          ticks: { color: "#78716c", includeBounds: false },
          display: showTemp || showControl,
          // Without an explicit floor, Chart.js auto-fits to the visible
          // data's own min (e.g. ~82C at Turning Point), starting the
          // axis mid-way up rather than at a real baseline. 0 in both
          // units: the control lines (0-100) sit in the low band of this axis.
          min: tempAxisMin(),
          // 350 C / 527 F unless the roast runs hotter (then the next 50 up).
          max: tempAxisMax(tempUnit, tempDataMax),
        },
        yRor: {
          type: "linear",
          position: "right",
          grid: { drawOnChartArea: false },
          ticks: { color: "#78716c" },
          display: showRor,
          // Fixed 0-25 C/min (0-45 F/min -- a rate conversion, no +32 offset,
          // matching how the RoR data itself is converted above), the usual
          // roasting-chart default, so a single transient spike -- e.g. the
          // huge RoR right after charge -- can't stretch the axis and flatten
          // the rest of the roast's curve into an unreadable line near zero.
          min: rorAxisRange(tempUnit).min,
          max: rorAxisRange(tempUnit).max,
        },
      },
    }),
    [events, phases, showTemp, showRor, showControl, tempUnit, tempDataMax, timeAxis.min, timeAxis.max, hideEventLabels, interactive, onRetimeEvent, handleDragMove, handleDragEnd, t]
  );

  return (
    <div className="scope">
      {resolvedTitle && (
        <div className="scope-header">
          <h2 className="scope-title">{resolvedTitle}</h2>
        </div>
      )}
      <div className="scope-toggles">
        {seriesDefs.map((s) => (
          <label
            key={s.key}
            className={`scope-toggle ${visible[s.key] ? "scope-toggle-on" : ""} ${!availability[s.key] ? "scope-toggle-empty" : ""}`}
            title={availability[s.key] ? undefined : t("common.roastChart.noDataForChannel")}
          >
            <input type="checkbox" checked={!!visible[s.key]} onChange={() => toggle(s.key)} />
            <span className="toggle-swatch" style={{ background: s.color }} />
            <span title={TERM_TOOLTIPS[s.label]}>{s.label}</span>
          </label>
        ))}
      </div>
      <div className="scope-chart-area" style={{ height }}>
        {interactive && (
          <div className="scope-chart-menu" ref={menuRef}>
            <button
              type="button"
              className="scope-chart-menu-btn"
              aria-label={t("common.roastChart.chartOptions")}
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
            >
              ⋮
            </button>
            {menuOpen && (
              <div className="scope-chart-menu-panel">
                <label className="scope-toggle" title={t("common.roastChart.hideEventLabelsHint")}>
                  <input type="checkbox" checked={hideEventLabels} onChange={() => setHideEventLabels((v) => !v)} />
                  <span>{t("common.roastChart.hideEventLabels")}</span>
                </label>
                <button
                  type="button"
                  className="link-like"
                  onClick={() => {
                    chartRef.current?.resetZoom();
                    setMenuOpen(false);
                  }}
                  title={t("common.roastChart.zoomHint", { modifier: WHEEL_ZOOM_MODIFIER_LABEL })}
                >
                  {t("common.roastChart.resetZoom")}
                </button>
              </div>
            )}
          </div>
        )}
        <Line ref={chartRef} data={data} options={options} onContextMenu={handleContextMenu} />
        {contextMenu && (
          <div className="milestone-context-menu" ref={contextMenuRef} style={{ left: contextMenu.x, top: contextMenu.y }}>
            <button
              type="button"
              className="link-like"
              onClick={() => {
                onDeleteEvent(contextMenu.eventId);
                setContextMenu(null);
              }}
            >
              {t("common.roastChart.deleteMilestone")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
});

export default RoastChart;
