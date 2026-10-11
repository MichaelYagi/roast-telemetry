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
import { EXTRA_CHANNEL_COLORS, PHASE_COLORS, eventColor, extraChannelColor, phaseColor } from "../chartColors.js";
import { formatTime, rorAxisRange, seriesLineWidth, tempAxisMax, tempAxisMin, timeAxisFor } from "../chartDefaults.js";
import { celsiusToUnit, unitSuffix } from "../tempUnits.js";
import { TERM_TOOLTIPS } from "../termTooltips.js";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend, zoomPlugin);


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
// Lets a channel's color be customized in one place (Settings > Colors,
// which writes breakout_panel_colors -- the same setting the Big/Small
// Readout panels already read for their own boxes) and have it apply here
// too, instead of a second, separate chart-only color setting. Only
// channels with a real Colors-section equivalent are mapped; Damper (and
// the background/extra series below, which are roast-specific, not a
// fixed channel) have no such entry to borrow a color from, so they keep
// their own SERIES_DEFS default untouched.
const SERIES_KEY_TO_BREAKOUT_KEY = {
  Damper: "damper",
  BG_BT: "bg_bt",
  BG_ET: "bg_et",
  BT: "bt",
  ET: "et",
  DT: "dt",
  ROR_BT: "ror_bt",
  ROR_ET: "ror_et",
  Burner: "heater",
  Air: "fan",
  Drum: "drum",
};

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

// Any extra channels this roast has (the backend's
// RoastProfilePoint.extra): a DeviceProfile's role=EXTRA probes -- e.g. a
// flue probe beyond BT/ET/DT -- or an imported log's own extra devices.
// Not all of those are temperatures: the roast's `extra_units` says, per
// label, "temp" (Celsius; converted and shown in degrees), "percent"
// ("Drum Speed", "Fan Speed" -- 0-100, never converted, shown with %) or
// "number" (not a temperature, unit unknown). Only the server decides
// which; a label it doesn't list is a temperature. Same reasoning as
// backgroundSeriesDefs above: built dynamically (there's no fixed list,
// since which extra channels exist -- if any -- depends entirely on
// which device profile this roast used), not part of the static
// SERIES_DEFS list, so they never show up as an empty toggle otherwise.
function extraSeriesDefs(labels, extraUnits = {}) {
  return labels.map((label, i) => {
    const kind = extraUnits[label] || "temp";
    return {
      key: `EXTRA_${label}`,
      label,
      // The default only; colorFor resolves the saved color (see chartColors.js).
      color: EXTRA_CHANNEL_COLORS[i % EXTRA_CHANNEL_COLORS.length],
      extraIndex: i,
      // A non-temperature goes in the 0-100 band with Burner/Fan/Drum.
      axis: kind === "temp" ? "yTemp" : "yControl",
      valueSuffix: kind === "temp" ? undefined : kind === "percent" ? "%" : "",
      isTemperature: kind === "temp",
      source: "extra",
      field: label,
      defaultOn: true,
    };
  });
}

// The three classic roast phases, each bounded by a pair of named
// milestone events. Colors follow the common green/yellow/red convention
// (drying / Maillard-browning / development).
const PHASE_DEFS = [
  { key: "dry", label: "Dry", color: PHASE_COLORS.dry, fromType: "CHARGE", toType: "DRY_END" },
  { key: "maillard", label: "Maillard", color: PHASE_COLORS.maillard, fromType: "DRY_END", toType: "FC_START" },
  { key: "dev", label: "Dev", color: PHASE_COLORS.dev, fromType: "FC_START", toType: "DROP" },
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
    if (ticks.length < 2) return;
    const step = ticks[1].value - ticks[0].value;
    if (!step) return;
    ctx.save();
    ctx.fillStyle = "rgba(15, 23, 42, 0.035)";
    // Which band a given temperature falls in, and whether *that* band is
    // shaded, has to be a fixed property of the temperature itself (band
    // index computed from an absolute value, parity-normalized so a
    // negative index still alternates correctly) -- not of the currently
    // visible tick array's own position (the previous version's `i % 2`
    // on ticks.length). Vertical panning (see zoom.pan.mode's Alt+drag
    // above) slides that array by a tick at a time, which shifted every
    // index by one and flipped every band's shaded/unshaded state each
    // time -- confirmed live: the same 150-200C band toggled between
    // shaded and unshaded as you dragged, which is what actually read as
    // "shaky", not the bands simply moving (they're supposed to move).
    const start = Math.floor(scale.min / step) * step;
    for (let value = start; value < scale.max; value += step) {
      const bandIndex = Math.round(value / step);
      if (((bandIndex % 2) + 2) % 2 !== 0) continue;
      const yTop = scale.getPixelForValue(value + step);
      const yBottom = scale.getPixelForValue(value);
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

// Lays out every milestone's label box together, nudging an overlapping
// one up or down until it clears the ones already placed -- ported from
// roast-telemetry-mobile's own layoutCallouts (src/components/chart/
// RoastChart.tsx), which solves the exact same problem (FC Start/FC End/
// Drop landing close together stacks their boxes on top of each other).
// Called once per draw and once per hit-test (see eventMarkersPlugin and
// hitTestLabel below) instead of computing each box independently, so
// the two can never disagree about where a box actually is -- the whole
// reason this is one function, not the old calloutGeometry's one call
// per event.
function layoutCallouts(chart, events, tempUnit, dragPreview) {
  const { ctx, chartArea } = chart;
  const placed = [];
  const overlaps = (a, b) =>
    a.boxX < b.boxX + b.boxWidth + 2 &&
    b.boxX < a.boxX + a.boxWidth + 2 &&
    a.boxY < b.boxY + b.boxHeight + 2 &&
    b.boxY < a.boxY + a.boxHeight + 2;
  // Set by handleCanvasMouseMove below, read here so the hovered dot can
  // draw bigger/glowing -- the same "stash it directly on the live Chart.js
  // instance" trick $dragPreview already uses, for the same reason (a
  // plain mousemove shouldn't trigger a React re-render on every pixel).
  const hoverId = chart.$hoverEventId;

  for (const ev of [...events].sort((a, b) => a.time_s - b.time_s)) {
    const dragging = Boolean(dragPreview && dragPreview.eventId === ev.id);
    const hovering = !dragging && hoverId === ev.id;
    const { x, dotY, timeS, displayValue } = markerPosition(chart, ev, tempUnit, dragging ? dragPreview.timeS : null);
    if (x < chartArea.left || x > chartArea.right) continue;
    const lines = [ev.label, formatTime(timeS), displayValue != null ? `${displayValue.toFixed(1)}${unitSuffix(tempUnit)}` : null].filter(Boolean);
    ctx.save();
    ctx.font = "9px system-ui, sans-serif";
    const boxWidth = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 14;
    ctx.restore();
    const boxHeight = lines.length * 11 + 6;
    const boxX = Math.min(Math.max(x - boxWidth / 2, chartArea.left), chartArea.right - boxWidth);
    const preferred = Math.max(chartArea.top + 2, dotY - boxHeight - 14);

    const candidates = [preferred];
    for (let k = 1; k <= 6; k++) {
      candidates.push(preferred - k * (boxHeight + 3), preferred + k * (boxHeight + 3));
    }
    const fit = candidates
      .filter((boxY) => boxY >= chartArea.top + 2 && boxY + boxHeight <= chartArea.bottom - 2)
      .map((boxY) => ({ ev, dragging, hovering, x, dotY, lines, boxX, boxY, boxWidth, boxHeight }))
      .find((c) => !placed.some((p) => overlaps(c, p)));

    placed.push(fit ?? { ev, dragging, hovering, x, dotY, lines, boxX, boxY: preferred, boxWidth, boxHeight });
  }
  return placed;
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
    const { ctx } = chart;
    ctx.save();
    layoutCallouts(chart, events, tempUnit, dragPreview).forEach(({ ev, dragging, hovering, x, dotY, lines, boxX, boxY, boxWidth, boxHeight }) => {
      const color = eventColor(opts?.colors, ev.type);

      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.setLineDash(dragging ? [3, 3] : []);
      ctx.beginPath();
      ctx.moveTo(x, dotY);
      // The box can land above *or* below the dot now that overlapping
      // ones get nudged -- connect the stem to whichever edge is nearer
      // the dot, same as the mobile chart's own rendering.
      ctx.lineTo(x, boxY > dotY ? boxY : boxY + boxHeight);
      ctx.stroke();
      ctx.setLineDash([]);

      // A soft halo behind the dot on hover -- the dot itself is tiny by
      // design (it has to sit precisely on the BT curve, not become a big
      // obvious target that obscures it), so hovering is the only thing
      // that can make it feel grabbable without changing how the chart
      // looks at rest. Confirmed real feedback: a 3.5px dot read as
      // "too small to click" even though the actual hit area (see
      // hitTestMarker/MARKER_HIT_RADIUS_PX) is already much larger.
      if (hovering) {
        ctx.save();
        ctx.globalAlpha = 0.3;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(x, dotY, 9, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }

      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, dotY, dragging || hovering ? 5.5 : 3.5, 0, Math.PI * 2);
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

// Generous -- hover/glow, right-click-to-delete, and (see onPanStart's
// own comment) the one-time fallback hit test for starting a drag, where
// "near enough to notice" is exactly what's wanted (see the earlier
// "hard to click the tiny dot" feedback). A separate, *tighter* radius
// was tried here for drag-start specifically, to stop a plain pan/scroll
// gesture starting near a milestone from getting hijacked into grabbing
// it -- that overcorrected: it made a genuinely glowing (hover-confirmed)
// dot fail to drag most of the time, since real cursor precision rarely
// lands inside a radius tighter than the hover zone that was shown as
// "you can grab this." onPanStart now gates on the *live* hover state
// instead of a second magic number -- see its own comment.
const MARKER_HIT_RADIUS_PX = 14;

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
function hitTestMarker(chart, events, x, y, tempUnit, radius = MARKER_HIT_RADIUS_PX) {
  let best = null;
  let bestDist = Infinity;
  for (const ev of events) {
    if (!isEditableMilestone(ev)) continue;
    const { x: mx, dotY } = markerPosition(chart, ev, tempUnit, null);
    const dist = Math.hypot(mx - x, dotY - y);
    if (dist <= radius && dist < bestDist) {
      best = ev;
      bestDist = dist;
    }
  }
  return best;
}

// Finds the editable milestone whose label box contains the given
// canvas-relative point -- a much bigger target than the dot, which sits
// among the curves. Boxes can overlap; the one drawn last (on top) wins.
// Uses the exact same layoutCallouts as the draw path (non-CUSTOM events,
// no drag preview, since this runs on mouse-down before a drag starts),
// so a click always lands on the box that's actually visible.
const LABEL_HIT_PAD_PX = 2;
function hitTestLabel(chart, events, x, y, tempUnit) {
  const layout = layoutCallouts(chart, events.filter((e) => e.type !== "CUSTOM"), tempUnit, null);
  for (const { ev, boxX, boxY, boxWidth, boxHeight } of [...layout].reverse()) {
    if (!isEditableMilestone(ev)) continue;
    if (
      x >= boxX - LABEL_HIT_PAD_PX &&
      x <= boxX + boxWidth + LABEL_HIT_PAD_PX &&
      y >= boxY - LABEL_HIT_PAD_PX &&
      y <= boxY + boxHeight + LABEL_HIT_PAD_PX
    ) {
      return ev;
    }
  }
  return null;
}

// forwardRef so a parent (the PDF report -- see RoastDetailView.jsx) can pull
// a flat image of the current chart via ref.current.toImage() without this
// component's own internal Chart.js/menu/drag refs leaking out.
const RoastChart = forwardRef(function RoastChart({
  profile = [],
  events = [],
  background = [],
  backgroundLabel = null,
  height = 280,
  title,
  tempUnit = "c",
  // The roast's extra_units ({label: "temp" | "percent" | "number"}) --
  // see extraSeriesDefs.
  extraUnits = {},
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
  // plus any extra channels this roast's own profile data
  // actually has (see extraSeriesDefs above).
  const extraUnitsKey = JSON.stringify(extraUnits || {});
  const seriesDefs = useMemo(() => {
    let defs = SERIES_DEFS;
    if (background.length) defs = [...defs, ...backgroundSeriesDefs(backgroundLabel)];
    if (extraLabelsKey) defs = [...defs, ...extraSeriesDefs(extraLabelsKey.split("|"), JSON.parse(extraUnitsKey))];
    return defs;
  }, [background.length, backgroundLabel, extraLabelsKey, extraUnitsKey]);

  const [visible, setVisible] = useState(() =>
    Object.fromEntries(SERIES_DEFS.map((s) => [s.key, s.defaultOn]))
  );
  // key -> hex override, from the same breakout_panel_colors setting the
  // Big/Small Readout panels already use (see SERIES_KEY_TO_BREAKOUT_KEY
  // above) -- {} until settings load, same as every other saved-setting
  // field on this component.
  const [channelColors, setChannelColors] = useState({});
  function colorFor(seriesDef) {
    if (seriesDef.source === "extra") return extraChannelColor(channelColors, seriesDef.field, seriesDef.extraIndex);
    const breakoutKey = SERIES_KEY_TO_BREAKOUT_KEY[seriesDef.key];
    return (breakoutKey && channelColors[breakoutKey]) || seriesDef.color;
  }
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
      setChannelColors(s.breakout_panel_colors || {});
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

  // Chart.js reapplies an axis's config min/max on every chart.update()
  // that a new `options` object triggers -- including one that only
  // toggled a series checkbox or hideEventLabels, nothing to do with the
  // axis range itself -- which silently discarded any zoom/pan the user
  // had applied (confirmed live). Below (in the `options` useMemo), each
  // interactive-only axis's min/max is computed via this instead of used
  // directly: it remembers the last *genuinely new* default range (a
  // different roast, a tempUnit switch) per axis, and while that hasn't
  // changed, hands back whatever the live chart's own current (possibly
  // panned/zoomed) range already is instead of the freshly recomputed
  // default -- so only an actual reason to reset the view resets it.
  const axisRangeRef = useRef({});
  function stableAxisRange(axisId, computedMin, computedMax) {
    const key = `${computedMin}:${computedMax}`;
    const cached = axisRangeRef.current[axisId];
    const chart = chartRef.current;
    if (cached && cached.key === key && chart && chart.scales[axisId]) {
      return { min: chart.scales[axisId].min, max: chart.scales[axisId].max };
    }
    axisRangeRef.current[axisId] = { key };
    return { min: computedMin, max: computedMax };
  }

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
        points = profile.map((p) => ({
          x: p.time_s,
          y: p.extra?.[s.field] == null ? null : s.isTemperature ? celsiusToUnit(p.extra[s.field], tempUnit) : p.extra[s.field],
        }));
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
        // Burner/Fan/Drum/Damper are percentages; an extra channel says its own.
        valueSuffix: s.valueSuffix ?? (s.axis === "yControl" ? "%" : undefined),
        borderColor: colorFor(s),
        backgroundColor: colorFor(s),
        pointRadius: 0,
        borderWidth: seriesLineWidth(s.key, s.axis),
        borderDash: s.source === "background" ? [6, 3] : undefined,
        stepped,
        yAxisID: s.axis === "yControl" ? "yTemp" : s.axis,
        tension: stepped ? 0 : 0.15,
      };
    });
    return { datasets };
  }, [seriesDefs, profile, events, background, visible, endTime, tempUnit, channelColors]);

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
    // Clear the preview immediately -- the marker visually reverts to its
    // stored position right away, then jumps straight to the new one
    // once onRetimeEvent's own state update lands (synchronous today --
    // RoastDetailView's handleRetimeMilestone only stages this into
    // local state, it doesn't call the API; the actual backend
    // neighbor-order check only runs later, at Save, see
    // RoastDetailView.jsx's own handleSaveMilestoneEdits). The .catch()
    // below is just a last-resort net for some unexpected failure in
    // onRetimeEvent itself, not a normal path.
    chart.$dragPreview = null;
    chart.canvas.style.cursor = chart.$hoverEventId ? "grab" : "";
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
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const hit = hitTestMarker(chart, events, x, y, tempUnit) || (!hideEventLabels && hitTestLabel(chart, events, x, y, tempUnit));
    if (!hit) return; // not on an editable marker -- let the browser's own context menu through
    e.preventDefault();
    setContextMenu({ eventId: hit.id, x: e.clientX, y: e.clientY });
  }

  // Hovering a milestone's dot makes it bigger + glows it (see
  // eventMarkersPlugin's own comment on why) and swaps the cursor to a
  // hand, so a target that has to stay visually tiny (it marks an exact
  // point on the BT curve) still reads as grabbable before you commit to
  // a click -- confirmed real feedback: without any of this, the plain
  // 3.5px dot read as too small to reliably click even though its actual
  // hit area (MARKER_HIT_RADIUS_PX) was already generous. Only wired up
  // at all when this chart can actually edit milestones (onDeleteEvent/
  // onRetimeEvent set) -- a read-only embed (e.g. a history thumbnail)
  // has nothing for hovering to offer.
  function handleCanvasMouseMove(e) {
    const chart = chartRef.current;
    if (!chart || dragRef.current || (!onDeleteEvent && !onRetimeEvent)) return;
    const rect = chart.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const hit = hitTestMarker(chart, events, x, y, tempUnit);
    const nextId = hit ? hit.id : null;
    if (chart.$hoverEventId !== nextId) {
      chart.$hoverEventId = nextId;
      chart.canvas.style.cursor = nextId ? "grab" : "";
      chart.update("none");
    }
  }

  function handleCanvasMouseLeave() {
    const chart = chartRef.current;
    if (!chart || chart.$hoverEventId == null) return;
    chart.$hoverEventId = null;
    chart.canvas.style.cursor = "";
    chart.update("none");
  }

  const options = useMemo(() => {
    // Default to just the Dry/Maillard/Dev span when one or more of those
    // phase bands are actually showing (computePhases -- each band needs
    // its own two boundary milestones, so this is always the visible
    // bands' own combined start/end, never a wider guess) -- a finished
    // roast is usually more interesting to review without the flat
    // pre-Charge lead-in and post-Drop cooling tail eating chart space by
    // default. Finished roasts only (interactive) -- a live roast's Dev
    // band can't exist until Drop actually happens, so this would
    // otherwise freeze the live view at Maillard's end for the whole
    // First Crack/Development stretch. Falls back to the full recording
    // when no phase is computable yet (e.g. Charge or Drop never marked).
    const phaseSpan = interactive && phases.length
      ? { min: Math.min(...phases.map((p) => p.start)), max: Math.max(...phases.map((p) => p.end)) }
      : null;
    const xRange = interactive
      ? stableAxisRange("x", phaseSpan ? phaseSpan.min : timeAxis.min, phaseSpan ? phaseSpan.max : timeAxis.max)
      : { min: timeAxis.min, max: timeAxis.max };
    const tempRange = interactive
      ? stableAxisRange("yTemp", tempAxisMin(), tempAxisMax(tempUnit, tempDataMax))
      : { min: tempAxisMin(), max: tempAxisMax(tempUnit, tempDataMax) };
    const rorDefault = rorAxisRange(tempUnit);
    const rorRange = interactive ? stableAxisRange("yRor", rorDefault.min, rorDefault.max) : rorDefault;
    return {
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
              const suffix =
                item.dataset.valueSuffix ??
                (item.dataset.yAxisID === "yTemp" ? unitSuffix(tempUnit) : item.dataset.yAxisID === "yRor" ? `${unitSuffix(tempUnit)}/min` : "");
              const value = item.parsed.y;
              return ` ${item.dataset.label}: ${value == null ? "—" : `${value.toFixed(1)}${suffix}`}`;
            },
          },
        },
        eventMarkers: { events: hideEventLabels ? [] : events, tempUnit, colors: channelColors },
        phaseBands: { phases: phases.map((p) => ({ ...p, color: phaseColor(channelColors, p.key) })) },
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
            // Free 2D pan -- not locked to horizontal. Was briefly
            // horizontal-only-by-default with vertical only via an Alt
            // modifier; dropped that after the user pointed out a plain
            // drag should just move whichever direction you actually
            // drag, not follow a fixed axis lock keyed to a modifier key.
            mode: "xy",
            onPanStart: ({ chart, event, point }) => {
              // Alt (Option) + drag on a milestone's label moves it -- far
              // easier to grab than the dot. A plain drag on a label still
              // pans the chart, as anywhere else. The dot itself drags
              // without Alt, as before.
              const altDrag = event?.srcEvent?.altKey && !hideEventLabels;
              // Checks the *live* hover state (handleCanvasMouseMove, set
              // on every mousemove) first, rather than only a fresh hit
              // test here -- ties "the dot is glowing" and "you can grab
              // it" to the exact same condition, so a genuinely glowing
              // dot never fails to drag (a real regression from an
              // earlier, tighter-than-hover drag-start radius: most real
              // clicks land well within the hover zone but outside a much
              // smaller one, so the drag silently never started even
              // though the dot was lit up). Falls back to a fresh hit
              // test only for a drag that starts before any mousemove
              // ever reported this point as hovered -- the very first
              // click on the chart, or a touchscreen tap with no hover
              // phase at all.
              const hoveredDot = chart.$hoverEventId ? events.find((e) => e.id === chart.$hoverEventId) : null;
              const dotHit = hoveredDot || hitTestMarker(chart, events, point.x, point.y, tempUnit);
              const hit = onRetimeEvent ? (altDrag && hitTestLabel(chart, events, point.x, point.y, tempUnit)) || dotHit : null;
              if (hit) {
                dragRef.current = { eventId: hit.id };
                chart.$dragPreview = { eventId: hit.id, timeS: hit.time_s };
                chart.canvas.style.cursor = "grabbing";
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
          min: xRange.min,
          ...(xRange.max != null ? { max: xRange.max } : { suggestedMax: timeAxis.suggestedMax }),
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
          min: tempRange.min,
          // 350 C / 527 F unless the roast runs hotter (then the next 50 up).
          max: tempRange.max,
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
          min: rorRange.min,
          max: rorRange.max,
        },
      },
    };
  }, [events, phases, showTemp, showRor, showControl, tempUnit, tempDataMax, timeAxis.min, timeAxis.max, hideEventLabels, interactive, onRetimeEvent, handleDragMove, handleDragEnd, t, channelColors]
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
            <span className="toggle-swatch" style={{ background: colorFor(s) }} />
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
        <Line
          ref={chartRef}
          data={data}
          options={options}
          onContextMenu={handleContextMenu}
          onMouseMove={handleCanvasMouseMove}
          onMouseLeave={handleCanvasMouseLeave}
        />
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
