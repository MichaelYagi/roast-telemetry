import {
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
} from "chart.js";
import { useMemo, useState } from "react";
import { Line } from "react-chartjs-2";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend);

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
// channels real Artisan uses for manual burner/air/drum/damper events.
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
  { key: "ROR_BT", label: "RoR (BT)", color: "#8b5cf6", axis: "yRor", source: "profile", field: "ror_bt", defaultOn: true },
  { key: "ROR_ET", label: "RoR (ET)", color: "#c4b5fd", axis: "yRor", source: "profile", field: "ror_et", defaultOn: false },
  { key: "Burner", label: "Burner", color: "#f59e0b", axis: "yControl", source: "channel", defaultOn: false },
  { key: "Air", label: "Air", color: "#0891b2", axis: "yControl", source: "channel", defaultOn: false },
  { key: "Drum", label: "Drum", color: "#16a34a", axis: "yControl", source: "channel", defaultOn: false },
  { key: "Damper", label: "Damper", color: "#7c3aed", axis: "yControl", source: "channel", defaultOn: false },
];

// The three classic roast phases, each bounded by a pair of named
// milestone events. Colors follow the common green/yellow/red convention
// (drying / Maillard-browning / development).
const PHASE_DEFS = [
  { key: "dry", label: "Dry", color: "#6ee7b7", fromType: "CHARGE", toType: "DRY_END" },
  { key: "maillard", label: "Maillard", color: "#fde68a", fromType: "DRY_END", toType: "FC_START" },
  { key: "dev", label: "Dev", color: "#fca5a5", fromType: "FC_START", toType: "DROP" },
];

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

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

// Real Artisan files only log control-channel *adjustments* (discrete
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

// Artisan's "Roaster Scope" draws alternating light/white horizontal bands
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

// Mimics Artisan's compact "F" / "F/min" unit labels pinned to the top
// corners of the scope, instead of full rotated axis titles.
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

// Dark rounded-rectangle callouts (label / time / value) anchored to each
// named milestone's point on the BT curve, with a stem + dot down to the
// actual point. Manual control-channel (CUSTOM) events are excluded here
// -- with dozens of those per roast, boxing each one would bury the
// chart; they're already visualized via the Burner/Air/Drum/Damper step
// curves and listed in the Events panel instead.
const eventMarkersPlugin = {
  id: "eventMarkers",
  afterDatasetsDraw(chart, _args, opts) {
    const events = (opts?.events || []).filter((e) => e.type !== "CUSTOM");
    if (!events.length) return;
    const { ctx, chartArea, scales } = chart;
    const xScale = scales.x;
    const yScale = scales.yTemp;
    ctx.save();
    events.forEach((ev) => {
      const x = xScale.getPixelForValue(ev.time_s);
      if (x < chartArea.left || x > chartArea.right) return;
      const color = EVENT_COLORS[ev.type] || EVENT_COLORS.CUSTOM;
      const dotY = ev.value != null && yScale ? yScale.getPixelForValue(ev.value) : chartArea.bottom;

      const lines = [ev.label, formatTime(ev.time_s), ev.value != null ? `${ev.value.toFixed(1)}°C` : null].filter(Boolean);
      ctx.font = "9px system-ui, sans-serif";
      const boxWidth = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 14;
      const boxHeight = lines.length * 11 + 6;
      const boxY = Math.max(chartArea.top + 2, dotY - boxHeight - 14);
      const boxX = Math.min(Math.max(x - boxWidth / 2, chartArea.left), chartArea.right - boxWidth);

      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, dotY);
      ctx.lineTo(x, boxY + boxHeight);
      ctx.stroke();

      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, dotY, 3.5, 0, Math.PI * 2);
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

export default function RoastChart({ profile = [], events = [], height = 420, title = "Roaster Scope" }) {
  const [visible, setVisible] = useState(() =>
    Object.fromEntries(SERIES_DEFS.map((s) => [s.key, s.defaultOn]))
  );

  const endTime = profile.length ? profile[profile.length - 1].time_s : null;
  const phases = useMemo(() => computePhases(events), [events]);

  const availability = useMemo(() => {
    const out = {};
    for (const s of SERIES_DEFS) {
      if (s.source === "profile") {
        out[s.key] = profile.some((p) => p[s.field] != null);
      } else {
        const continuousField = CONTINUOUS_FIELD_BY_CHANNEL[s.key];
        const hasContinuous = continuousField && profile.some((p) => p[continuousField] != null);
        const hasEvents = events.some((e) => e.channel === s.key);
        out[s.key] = hasContinuous || hasEvents;
      }
    }
    return out;
  }, [profile, events]);

  function toggle(key) {
    setVisible((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  const data = useMemo(() => {
    const datasets = SERIES_DEFS.filter((s) => visible[s.key]).map((s) => {
      let points;
      let stepped = false;
      if (s.source === "profile") {
        points = profile.map((p) => ({ x: p.time_s, y: p[s.field] }));
      } else {
        const continuousField = CONTINUOUS_FIELD_BY_CHANNEL[s.key];
        const hasContinuous = continuousField && profile.some((p) => p[continuousField] != null);
        if (hasContinuous) {
          points = profile.map((p) => ({ x: p.time_s, y: p[continuousField] }));
        } else {
          points = stepCurveFromEvents(events.filter((e) => e.channel === s.key), endTime);
          stepped = "before";
        }
      }
      return {
        label: s.label,
        data: points,
        borderColor: s.color,
        backgroundColor: s.color,
        pointRadius: 0,
        borderWidth: s.axis === "yTemp" ? 2.5 : 1.5,
        borderDash: s.axis === "yRor" ? [2, 2] : undefined,
        stepped,
        yAxisID: s.axis,
        tension: stepped ? 0 : 0.15,
      };
    });
    return { datasets };
  }, [profile, events, visible, endTime]);

  const showTemp = visible.BT || visible.ET;
  const showRor = visible.ROR_BT || visible.ROR_ET;
  const showControl = visible.Burner || visible.Air || visible.Drum || visible.Damper;

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
              const suffix = item.dataset.yAxisID === "yTemp" ? "°C" : item.dataset.yAxisID === "yRor" ? "°C/min" : "";
              const value = item.parsed.y;
              return ` ${item.dataset.label}: ${value == null ? "—" : `${value.toFixed(1)}${suffix}`}`;
            },
          },
        },
        eventMarkers: { events },
        phaseBands: { phases },
        axisUnitLabels: { leftUnit: showTemp ? "°C" : null, rightUnit: showRor ? "°C/min" : null },
      },
      scales: {
        x: {
          type: "linear",
          grid: { color: "#e7e5e4" },
          title: { display: true, text: "mins", color: "#78716c" },
          ticks: { callback: (value) => formatTime(value), color: "#78716c", maxTicksLimit: 8 },
        },
        yTemp: {
          type: "linear",
          position: "left",
          grid: { color: "#e7e5e4" },
          ticks: { color: "#78716c" },
          display: showTemp,
          // Without an explicit floor, Chart.js auto-fits to the visible
          // data's own min (e.g. ~82C at Turning Point), starting the
          // axis mid-way up rather than at a real baseline.
          min: 0,
        },
        yRor: {
          type: "linear",
          position: "right",
          grid: { drawOnChartArea: false },
          ticks: { color: "#78716c" },
          display: showRor,
          // Fixed range (matches typical Artisan RoR scope bounds) so a
          // single transient spike -- e.g. the sharp BT dip right after
          // charge -- can't stretch the axis and flatten the rest of the
          // roast's curve into an unreadable line near zero.
          min: -50,
          max: 50,
        },
        yControl: {
          type: "linear",
          position: "right",
          grid: { drawOnChartArea: false },
          ticks: { color: "#78716c" },
          title: { display: true, text: "ctrl", color: "#78716c" },
          display: showControl,
          // Heater/Fan/Drum/Damper are always 0-100% -- without a fixed
          // range here too, Chart.js auto-fit whatever narrow slice of
          // values was actually visible, so a ~constant 50% Drum line
          // (say) landed at an arbitrary height instead of a real
          // percentage scale. Artisan itself plots these against its own
          // temperature axis rather than a dedicated one -- this app
          // deliberately doesn't match that (a real 0-100 scale is more
          // readable than a control value squashed near zero on a 350-
          // degree axis), so don't "fix" this to match Artisan's own
          // choice here.
          min: 0,
          max: 100,
        },
      },
    }),
    [events, phases, showTemp, showRor, showControl]
  );

  return (
    <div className="scope">
      {title && (
        <div className="scope-header">
          <h2 className="scope-title">{title}</h2>
        </div>
      )}
      <div className="scope-toggles">
        {SERIES_DEFS.map((s) => (
          <label
            key={s.key}
            className={`scope-toggle ${visible[s.key] ? "scope-toggle-on" : ""} ${!availability[s.key] ? "scope-toggle-empty" : ""}`}
            title={availability[s.key] ? undefined : "No data for this channel in this roast"}
          >
            <input type="checkbox" checked={!!visible[s.key]} onChange={() => toggle(s.key)} />
            <span className="toggle-swatch" style={{ background: s.color }} />
            {s.label}
          </label>
        ))}
      </div>
      <div style={{ height }}>
        <Line data={data} options={options} />
      </div>
    </div>
  );
}
