import { Chart as ChartJS, Legend, LineElement, LinearScale, PointElement, Tooltip } from "chart.js";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Line, Scatter } from "react-chartjs-2";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import AnalysisInsights from "../components/AnalysisInsights.jsx";
import BulkZipDownload from "../components/BulkZipDownload.jsx";
import SavedViews from "../components/SavedViews.jsx";
import { formatMetric, formatSeconds, groupMetrics, isRate, isTemperature, meanAndSd, metricUnitLabel, metricValue } from "../lib/metricFormat.js";
import { unitSuffix } from "../tempUnits.js";

ChartJS.register(LinearScale, PointElement, LineElement, Tooltip, Legend);

// Draws a soft canvas glow around a hovered group's points -- paired with
// the legend's onHover/onLeave in the Explore chart's own options below
// (hovering a legend item flags its dataset with _glow: true, set in the
// `chart` useMemo). beforeDatasetDraw/afterDatasetDraw bracket exactly one
// dataset's own draw call, so this never bleeds onto any other dataset.
const pointGlowPlugin = {
  id: "pointGlow",
  beforeDatasetDraw(chartInstance, args) {
    const dataset = chartInstance.data.datasets[args.index];
    if (!dataset?._glow) return;
    chartInstance.ctx.save();
    chartInstance.ctx.shadowColor = dataset.borderColor;
    chartInstance.ctx.shadowBlur = 14;
  },
  afterDatasetDraw(chartInstance, args) {
    if (!chartInstance.data.datasets[args.index]?._glow) return;
    chartInstance.ctx.restore();
  },
};
ChartJS.register(pointGlowPlugin);

const DATE_KEY = "__date";

// Evenly spread hues around the color wheel -- guarantees `total` visually
// distinct colors for any number of groups, instead of a fixed palette that
// wraps around (and silently duplicates) once there are more groups than it
// has colors for.
function colorForIndex(i, total) {
  const hue = Math.round((360 * i) / Math.max(total, 1));
  return { solid: `hsl(${hue}, 65%, 45%)`, dim: `hsla(${hue}, 65%, 45%, 0.18)` };
}

// The numbers the consistency table shows for every group, besides the chart's
// own Y -- also what the Trends tab charts, one small chart each (see
// TrendChart below): the same handful of "how's my roasting going" numbers,
// just trended over time instead of averaged into one row.
const KEY_METRICS = ["duration_s", "drop_temp_c", "development_time_s", "dtr_pct", "weight_loss_pct"];

// How many recent roasts each point on a trend's bold line averages over --
// smooths day-to-day noise without lagging so far behind that a real,
// sustained drift takes forever to show up.
const TREND_ROLLING_WINDOW = 5;
const TREND_MIN_POINTS = 2; // below this a "trend" is just one or two dots -- not worth drawing

// points: [{x: timestamp, y: number}], already sorted by x ascending.
function rollingAverage(points, window) {
  return points.map((p, i) => {
    const slice = points.slice(Math.max(0, i - window + 1), i + 1);
    return { x: p.x, y: slice.reduce((sum, s) => sum + s.y, 0) / slice.length };
  });
}

// Same suffix/decimals rules as formatMetric (lib/metricFormat.js), but for
// a value that's already in display units (the rolling-average line's own
// points, built from already-converted raw points below) -- formatMetric
// itself always converts from Celsius/raw first, which would double-convert
// here.
function formatTrendValue(metric, displayValue, tempUnit) {
  if (displayValue == null) return "—";
  if (metric.unit === "s") return formatSeconds(displayValue);
  const fixed = isTemperature(metric) || isRate(metric) || metric.unit === "%";
  const text = fixed ? displayValue.toFixed(1) : Number.isInteger(displayValue) ? String(displayValue) : displayValue.toFixed(1);
  if (isTemperature(metric)) return `${text}${unitSuffix(tempUnit)}`;
  if (isRate(metric)) return `${text}${unitSuffix(tempUnit)}/min`;
  if (metric.unit === "%") return `${text}%`;
  if (metric.unit === "g") return `${text} g`;
  if (metric.unit === "/5") return `${text}/5`;
  return metric.unit ? `${text} ${metric.unit}` : text;
}

// One small chart per KEY_METRICS entry (see AnalysisView's Trends tab):
// faint dots for each roast plus a bold rolling-average line, both against
// Date. A separate component (not inlined in the tab body) purely so each
// metric's own useMemo/options only recompute when that metric's own
// inputs change, not on every render of the other four.
function TrendChart({ metric, table, tempUnit, t, dateRange }) {
  const points = useMemo(() => {
    return table.rows
      .map((row) => ({ x: Date.parse(row.created_at), y: metricValue(metric, row.metrics[metric.key], tempUnit), row }))
      .filter((p) => p.y != null && !Number.isNaN(p.y) && !Number.isNaN(p.x))
      .sort((a, b) => a.x - b.x);
  }, [table, metric, tempUnit]);

  const unit = metricUnitLabel(metric, tempUnit);

  if (points.length < TREND_MIN_POINTS) {
    return (
      <div className="trend-chart-box">
        <h4>
          {metric.label} {unit && <span className="hint">({unit})</span>}
        </h4>
        <p className="hint">{t("analysis.trends.notEnoughRoasts")}</p>
      </div>
    );
  }

  const avg = rollingAverage(points, TREND_ROLLING_WINDOW);
  const data = {
    datasets: [
      {
        label: metric.label,
        data: points,
        borderColor: "transparent",
        backgroundColor: "#94a3b8",
        pointRadius: 3,
        pointHoverRadius: 5,
        showLine: false,
      },
      {
        label: metric.label,
        data: avg,
        borderColor: "#2563eb",
        backgroundColor: "#2563eb",
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.25,
      },
    ],
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    parsing: false,
    animation: false,
    onClick: (_event, elements, chartInstance) => {
      // Only the raw dots (dataset 0) have a roast behind a given point --
      // a click landing on the average line itself does nothing.
      const hit = elements.find((el) => el.datasetIndex === 0);
      if (!hit) return;
      const point = chartInstance.data.datasets[0].data[hit.index];
      window.open(`/roasts/${point.row.id}`, "_blank", "noopener,noreferrer");
    },
    onHover: (event, elements) => {
      event.native.target.style.cursor = elements.some((el) => el.datasetIndex === 0) ? "pointer" : "default";
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          title: (items) => {
            const raw = items.find((i) => i.datasetIndex === 0);
            return raw ? raw.raw.row.title : new Date(items[0].parsed.x).toLocaleDateString();
          },
          label: (item) =>
            item.datasetIndex === 0
              ? `${new Date(item.raw.row.created_at).toLocaleDateString()}: ${formatMetric(metric, item.raw.row.metrics[metric.key], tempUnit)}`
              : formatTrendValue(metric, item.parsed.y, tempUnit),
        },
      },
    },
    scales: {
      x: {
        type: "linear",
        // The same [min, max] on every trend chart (all of table.rows'
        // created_at range, not just this metric's own points) -- so
        // scanning down the page, the same x position always means the
        // same date on every chart, even when one metric has fewer
        // recorded roasts than another. `bounds: "data"` on top of that
        // stops Chart.js's default tick-rounding from padding the axis
        // out past the real range: it treats these as plain large numbers
        // (millisecond timestamps), not dates, so its usual "round to a
        // nice number" padding can overshoot the last real point by
        // weeks -- confirmed live (DTR's last dot was 9/21, axis ran to
        // 11/18).
        min: dateRange?.[0],
        max: dateRange?.[1],
        bounds: "data",
        grid: { display: false },
        ticks: { callback: (v) => new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric" }), maxTicksLimit: 4 },
      },
      y: { ticks: { callback: (v) => (metric.unit === "s" ? formatSeconds(v) : v) } },
    },
  };

  return (
    <div className="trend-chart-box">
      <h4>
        {metric.label} {unit && <span className="hint">({unit})</span>}
      </h4>
      <div className="trend-chart-canvas">
        <Line data={data} options={options} />
      </div>
    </div>
  );
}

const DEFAULTS = {
  filters: { q: "", tag: "", bean_id: "", source: "", created_from: "", created_to: "", include_simulated: false },
  x: "charge_temp_c",
  y: "drop_temp_c",
  groupBy: "beans",
};

// The group(s) a roast belongs to, the same way the server groups them.
function groupKeys(row, groupBy, t, sourceLabels) {
  if (groupBy === "none") return [t("analysis.groups.allRoasts")];
  if (groupBy === "tag") {
    const tags = row.tags.filter((tagValue) => tagValue !== "simulated");
    return tags.length ? tags : [t("analysis.noTagFallback")];
  }
  if (groupBy === "month") return [row.created_at.slice(0, 7)];
  const value = row[groupBy];
  if (groupBy === "source") return [sourceLabels[value] || value];
  return [value || t("analysis.noneFallback")];
}

export default function AnalysisView() {
  const { t } = useTranslation();
  const GROUPS = [
    { key: "none", label: t("analysis.groups.nothing") },
    { key: "beans", label: t("analysis.groups.beans") },
    { key: "origin", label: t("analysis.groups.origin") },
    { key: "process", label: t("analysis.groups.process") },
    { key: "tag", label: t("analysis.groups.tag") },
    { key: "month", label: t("analysis.groups.month") },
    { key: "roaster", label: t("analysis.groups.roastedBy") },
    { key: "source", label: t("analysis.groups.source") },
  ];
  const SOURCE_LABELS = {
    recorded: t("analysis.sourceLabels.recorded"),
    uploaded: t("analysis.sourceLabels.uploaded"),
    replay: t("analysis.sourceLabels.replay"),
  };
  const [metrics, setMetrics] = useState([]);
  const [tags, setTags] = useState([]);
  const [beans, setBeans] = useState([]);
  const [tempUnit, setTempUnit] = useState("c");
  // `filters` is what the boxes show; `applied` is what the page is actually
  // showing. The Apply button (or Enter) copies one to the other.
  const [filters, setFilters] = useState(DEFAULTS.filters);
  const [applied, setApplied] = useState(DEFAULTS.filters);
  const [x, setX] = useState(DEFAULTS.x);
  const [y, setY] = useState(DEFAULTS.y);
  const [groupBy, setGroupBy] = useState(DEFAULTS.groupBy);
  // "explore" (the X/Y scatter picker, the page's long-standing default) or
  // "trends" (a fixed set of small rolling-average charts, see TrendChart).
  // Both read the same already-filtered `table` -- no separate fetch.
  const [mode, setMode] = useState("explore");
  // Explore chart only: which dataset's legend entry is currently hovered
  // (its index, not its name -- the legend label has a "(count)" suffix
  // appended, so the raw group name isn't directly available there without
  // re-parsing it). null when nothing's hovered.
  const [hoveredDatasetIndex, setHoveredDatasetIndex] = useState(null);
  const [table, setTable] = useState(null);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.getAnalysisMetrics().then(setMetrics);
    api.listTags().then(setTags).catch(() => {});
    api.listBeans().then(setBeans).catch(() => {});
    api.getSettings().then((s) => setTempUnit(s.temperature_unit || "c"));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([api.getAnalysisTable(applied), api.getAnalysisSummary({ ...applied, group_by: groupBy })])
      .then(([t, s]) => {
        if (cancelled) return;
        setTable(t);
        setSummary(s);
      })
      .catch((err) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [applied, groupBy]);

  const byKey = useMemo(() => Object.fromEntries(metrics.map((m) => [m.key, m])), [metrics]);
  const xMetric = x === DATE_KEY ? { key: DATE_KEY, label: t("analysis.date"), unit: "" } : byKey[x];
  const yMetric = byKey[y];

  const setFilter = (key) => (e) => {
    const value = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    setFilters((f) => ({ ...f, [key]: value }));
  };

  // -- chart --------------------------------------------------------------------
  const xValue = (row) => (x === DATE_KEY ? Date.parse(row.created_at) : metricValue(xMetric, row.metrics[x], tempUnit));
  const yValue = (row) => metricValue(yMetric, row.metrics[y], tempUnit);

  const chart = useMemo(() => {
    if (!table || !xMetric || !yMetric) return null;
    const groups = new Map();
    for (const row of table.rows) {
      const a = xValue(row);
      const b = yValue(row);
      if (a == null || b == null || Number.isNaN(a)) continue;
      for (const key of groupKeys(row, groupBy, t, SOURCE_LABELS)) {
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push({ x: a, y: b, row });
      }
    }
    const names = [...groups.keys()].sort();
    const isDate = x === DATE_KEY;
    return {
      shown: [...groups.values()].reduce((n, pts) => n + pts.length, 0),
      data: {
        datasets: names.map((name, i) => {
          const points = groups.get(name).sort((p, q) => p.x - q.x);
          const { solid, dim } = colorForIndex(i, names.length);
          const isHovered = hoveredDatasetIndex === i;
          // Only fade the *others* once something is actually hovered --
          // with nothing hovered, every group shows at full color as before.
          const dimmed = hoveredDatasetIndex != null && !isHovered;
          const color = dimmed ? dim : solid;
          return {
            label: `${name} (${points.length})`,
            data: points,
            borderColor: color,
            backgroundColor: color,
            pointRadius: isHovered ? 7 : 5,
            pointHoverRadius: isHovered ? 9 : 7,
            // The white ring + glow (via pointGlowPlugin, keyed off _glow)
            // are additive on top of the hovered group's own color -- left
            // unset for everyone else so their normal appearance is
            // unchanged from before this feature existed.
            ...(isHovered ? { pointBorderColor: "#ffffff", pointBorderWidth: 2, _glow: true } : null),
            showLine: isDate,
            borderWidth: 1.5,
          };
        }),
      },
    };
  }, [table, x, y, groupBy, tempUnit, xMetric, yMetric, hoveredDatasetIndex]); // eslint-disable-line react-hooks/exhaustive-deps

  const axisTicks = (metric) => (value) => {
    if (metric.key === DATE_KEY) return new Date(value).toLocaleDateString(undefined, { month: "short", year: "2-digit" });
    return metric.unit === "s" ? formatSeconds(value) : value;
  };

  const options = useMemo(() => {
    if (!xMetric || !yMetric) return {};
    const axisTitle = (m) => (m.key === DATE_KEY ? t("analysis.date") : `${m.label} (${metricUnitLabel(m, tempUnit)})`);
    return {
      responsive: true,
      maintainAspectRatio: false,
      parsing: false,
      animation: false,
      onClick: (_event, elements, chartInstance) => {
        if (!elements.length) return;
        const { datasetIndex, index } = elements[0];
        const point = chartInstance.data.datasets[datasetIndex].data[index];
        // A new tab, so the chart and its filters stay as they are.
        window.open(`/roasts/${point.row.id}`, "_blank", "noopener,noreferrer");
      },
      onHover: (event, elements) => {
        event.native.target.style.cursor = elements.length ? "pointer" : "default";
      },
      plugins: {
        legend: {
          position: "top",
          labels: { usePointStyle: true, boxWidth: 8 },
          // Hovering a legend entry glows/enlarges that group's own dots in
          // the chart below (see the `chart` useMemo and pointGlowPlugin
          // above) so a busy scatter with many groups is easy to pick out.
          onHover: (event, legendItem) => {
            event.native.target.style.cursor = "pointer";
            setHoveredDatasetIndex(legendItem.datasetIndex);
          },
          onLeave: (event) => {
            event.native.target.style.cursor = "default";
            setHoveredDatasetIndex(null);
          },
        },
        tooltip: {
          callbacks: {
            // No shared title callback -- a shared header only shows the
            // *first* matched point's title, but two dots can genuinely
            // overlap (same X/Y, different roasts), and each one then needs
            // its own title, not just its own beans/metric lines under
            // someone else's. Putting the title inside each item's own
            // label lines guarantees it's always paired with the right
            // roast, in order, no matter how many points are matched at once.
            label: (item) => {
              const { row } = item.raw;
              const xText = x === DATE_KEY ? new Date(row.created_at).toLocaleDateString() : formatMetric(xMetric, row.metrics[x], tempUnit);
              return [
                row.title,
                row.beans ? `${t("analysis.filters.beans")}: ${row.beans}` : null,
                `${xMetric.label}: ${xText}`,
                `${yMetric.label}: ${formatMetric(yMetric, row.metrics[y], tempUnit)}`,
                t("analysis.clickToOpenNewTab"),
              ].filter(Boolean);
            },
          },
        },
      },
      scales: {
        x: {
          type: "linear",
          title: { display: true, text: axisTitle(xMetric) },
          ticks: { callback: axisTicks(xMetric) },
          // Date only, same reasoning as TrendChart's own x-scale: without
          // this, Chart.js's default "round to a nice number" tick padding
          // treats a millisecond timestamp as a plain large number and can
          // pad the axis weeks past the last real point.
          ...(x === DATE_KEY ? { bounds: "data" } : null),
        },
        y: { type: "linear", title: { display: true, text: axisTitle(yMetric) }, ticks: { callback: axisTicks(yMetric) } },
      },
    };
  }, [xMetric, yMetric, tempUnit, x, t]); // eslint-disable-line react-hooks/exhaustive-deps

  // -- consistency table ------------------------------------------------------------
  const shownMetrics = useMemo(() => {
    const keys = [...new Set([y, ...KEY_METRICS])].filter((k) => byKey[k]);
    return keys.map((k) => byKey[k]);
  }, [y, byKey]);

  // -- trends tab -------------------------------------------------------------------
  // Fixed (not the user's own Y pick, unlike shownMetrics above) -- Trends
  // is meant to always answer the same "how's my roasting going" question,
  // not shift around with whatever's picked in Explore.
  const trendMetrics = useMemo(() => KEY_METRICS.map((k) => byKey[k]).filter(Boolean), [byKey]);
  // Every roast in the current filtered set, not just the ones with a value
  // for a given metric -- passed to every TrendChart below as its x-axis
  // min/max, so a metric with fewer recorded roasts than another still
  // lines up on the same calendar range instead of auto-scaling to its own
  // narrower span of points.
  const trendDateRange = useMemo(() => {
    if (!table) return null;
    const times = table.rows.map((r) => Date.parse(r.created_at)).filter((t2) => !Number.isNaN(t2));
    return times.length ? [Math.min(...times), Math.max(...times)] : null;
  }, [table]);

  const meanSd = (metric, stats) => {
    if (!stats || stats.mean == null) return "—";
    const mean = formatMetric(metric, stats.mean, tempUnit);
    if (stats.sd == null) return mean;
    const sd = metric.unit === "s" ? formatSeconds(stats.sd) : formatMetric(metric, stats.sd, tempUnit, { withUnit: false });
    return `${mean} ± ${sd}`;
  };

  // -- outliers: a roast far from the rest of its group on the chosen Y number ------------
  const outliers = useMemo(() => {
    if (!table || !yMetric) return [];
    const groups = new Map();
    for (const row of table.rows) {
      const value = row.metrics[y];
      if (value == null) continue;
      for (const key of groupKeys(row, groupBy, t, SOURCE_LABELS)) {
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push({ row, value });
      }
    }
    const found = [];
    for (const [key, items] of groups) {
      if (items.length < 5) continue;
      const { mean, sd } = meanAndSd(items.map((i) => i.value));
      if (!sd) continue;
      for (const { row, value } of items) {
        const z = (value - mean) / sd;
        if (Math.abs(z) >= 2) found.push({ row, group: key, value, z });
      }
    }
    return found.sort((a, b) => Math.abs(b.z) - Math.abs(a.z));
  }, [table, y, groupBy, yMetric]);

  // -- saved views ------------------------------------------------------------------------
  const getConfig = () => ({ filters, x, y, groupBy });
  const loadConfig = (config) => {
    const next = { ...DEFAULTS.filters, ...(config.filters || {}) };
    setFilters(next);
    setApplied(next); // loading a saved view shows it straight away
    if (config.x) setX(config.x);
    if (config.y) setY(config.y);
    if (config.groupBy) setGroupBy(config.groupBy);
  };

  const dirty = JSON.stringify(filters) !== JSON.stringify(applied);
  const apply = (e) => {
    e?.preventDefault();
    setApplied(filters);
  };

  const reset = () => {
    setFilters(DEFAULTS.filters);
    setApplied(DEFAULTS.filters);
    setX(DEFAULTS.x);
    setY(DEFAULTS.y);
    setGroupBy(DEFAULTS.groupBy);
  };

  const anyFilter = Object.entries(filters).some(([k, v]) => (k === "include_simulated" ? v : Boolean(v)));
  const metricOptions = (withDate) => (
    <>
      {withDate && <option value={DATE_KEY}>{t("analysis.date")}</option>}
      {groupMetrics(metrics).map(([group, list]) => (
        <optgroup key={group} label={group}>
          {list.map((m) => (
            <option key={m.key} value={m.key}>
              {m.label}
            </option>
          ))}
        </optgroup>
      ))}
    </>
  );

  return (
    <div className="analysis-view">
      <div className="panel">
        <div className="analysis-header">
          <h2>{t("app.nav.analysis")}</h2>
          <SavedViews kind="analysis" getConfig={getConfig} onLoad={loadConfig} />
        </div>
        <p className="hint">{t("analysis.hint")}</p>

        <form className="filters-grid analysis-filters" onSubmit={apply}>
          <label className="filter-search">
            {t("analysis.filters.search")}
            <input placeholder={t("analysis.filters.searchPlaceholder")} value={filters.q} onChange={setFilter("q")} />
          </label>
          <label>
            {t("analysis.filters.beans")}
            <select value={filters.bean_id} onChange={setFilter("bean_id")}>
              <option value="">{t("analysis.filters.all")}</option>
              {beans.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("analysis.filters.tag")}
            <select value={filters.tag} onChange={setFilter("tag")}>
              <option value="">{t("analysis.filters.all")}</option>
              {tags.map((tagItem) => (
                <option key={tagItem.tag} value={tagItem.tag}>
                  {tagItem.tag} ({tagItem.count})
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("analysis.filters.dataFrom")}
            <select value={filters.source} onChange={setFilter("source")}>
              <option value="">{t("analysis.filters.recordedAndUploaded")}</option>
              <option value="recorded">{t("analysis.sourceLabels.recorded")}</option>
              <option value="uploaded">{t("analysis.filters.uploadedLogFiles")}</option>
              <option value="replay">{t("analysis.filters.replayedLogs")}</option>
            </select>
          </label>
          <label>
            {t("analysis.filters.from")}
            <input type="date" value={filters.created_from} onChange={setFilter("created_from")} />
          </label>
          <label>
            {t("analysis.filters.to")}
            <input type="date" value={filters.created_to} onChange={setFilter("created_to")} />
          </label>
          <div className="analysis-filter-extras">
            <label className="checkbox-label">
              <input type="checkbox" checked={filters.include_simulated} onChange={setFilter("include_simulated")} />
              {t("analysis.filters.includeSimulated")}
            </label>
          </div>
          <div className="analysis-filter-extras">
            <button type="submit" className={dirty ? "" : "button-quiet"}>
              {t("analysis.filters.applyFilters")}
            </button>
            {dirty && <span className="hint">{t("analysis.filters.filtersChangedHint")}</span>}
            {(anyFilter || x !== DEFAULTS.x || y !== DEFAULTS.y || groupBy !== DEFAULTS.groupBy) && (
              <button type="button" className="link-like" onClick={reset}>
                {t("analysis.filters.reset")}
              </button>
            )}
          </div>
        </form>
      </div>

      <div className="panel">
        <div className="analysis-mode-toggle">
          <button type="button" className={mode === "explore" ? "active" : ""} onClick={() => setMode("explore")}>
            {t("analysis.mode.explore")}
          </button>
          <button type="button" className={mode === "trends" ? "active" : ""} onClick={() => setMode("trends")}>
            {t("analysis.mode.trends")}
          </button>
        </div>

        {mode === "explore" && (
          <div className="analysis-pickers">
            <label>
              {t("analysis.xAxisLabel")}
              <select value={x} onChange={(e) => setX(e.target.value)}>
                {metricOptions(true)}
              </select>
            </label>
            <label>
              {t("analysis.yAxisLabel")}
              <select value={y} onChange={(e) => setY(e.target.value)}>
                {metricOptions(false)}
              </select>
            </label>
            <label>
              {t("analysis.colourBy")}
              <select value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
                {GROUPS.map((g) => (
                  <option key={g.key} value={g.key}>
                    {g.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}

        {error && <p className="error">{error}</p>}
        {loading && !table ? (
          <p>{t("analysis.loading")}</p>
        ) : table && table.total === 0 ? (
          <p className="hint">
            {t("analysis.noMatch.prefix")} <Link to="/history">{t("app.nav.history")}</Link> {t("analysis.noMatch.suffix")}
          </p>
        ) : mode === "explore" ? (
          <>
            {chart && chart.shown > 0 && xMetric && yMetric && (
              <p className="analysis-caption">
                {t("analysis.caption.prefix")} <strong>{xMetric.label}</strong> {t("analysis.caption.acrossComma")} <strong>{yMetric.label}</strong>{" "}
                {t("analysis.caption.upDot")}
                {groupBy === "none" ? "" : t("analysis.caption.colouredBy", { group: GROUPS.find((g) => g.key === groupBy)?.label.toLowerCase() })}
                {t("analysis.caption.hoverHint")}
                {chart.shown < 5 && t("analysis.caption.fewRoasts")}
              </p>
            )}
            <div className="analysis-chart">{chart && chart.shown > 0 ? <Scatter data={chart.data} options={options} /> : null}</div>
            {chart && chart.shown === 0 && table && table.total > 0 && <p className="hint">{t("analysis.noneOfBoth")}</p>}
            <p className="hint analysis-count">
              {table ? t("analysis.count", { count: table.total }) : ""}
              {table?.truncated ? t("analysis.countTruncated") : ""}
              {table?.simulated_excluded ? t("analysis.countSimulatedExcluded", { count: table.simulated_excluded }) : ""}
              {table?.missing_recording ? t("analysis.countMissingRecording", { count: table.missing_recording }) : ""}
              .{" "}
              <BulkZipDownload params={applied} tempUnit={tempUnit} />
            </p>
          </>
        ) : (
          <>
            <p className="hint">{t("analysis.trends.hint", { window: TREND_ROLLING_WINDOW })}</p>
            <div className="trend-charts-grid">
              {table &&
                trendMetrics.map((m) => (
                  <TrendChart key={m.key} metric={m} table={table} tempUnit={tempUnit} t={t} dateRange={trendDateRange} />
                ))}
            </div>
            <p className="hint analysis-count">
              {table ? t("analysis.count", { count: table.total }) : ""}
              {table?.truncated ? t("analysis.countTruncated") : ""}
              {table?.simulated_excluded ? t("analysis.countSimulatedExcluded", { count: table.simulated_excluded }) : ""}
              {table?.missing_recording ? t("analysis.countMissingRecording", { count: table.missing_recording }) : ""}
              .{" "}
              <BulkZipDownload params={applied} tempUnit={tempUnit} />
            </p>
          </>
        )}
      </div>

      {summary && summary.groups.length > 0 && (
        <div className="panel">
          <h3>{t("analysis.consistencyHeading")}</h3>
          <p className="hint">{t("analysis.consistencyHint")}</p>
          <div className="table-scroll">
            <table className="roast-table analysis-table">
              <thead>
                <tr>
                  <th>{groupBy === "none" ? t("analysis.groups.allRoasts") : GROUPS.find((g) => g.key === groupBy)?.label}</th>
                  <th>{t("analysis.roastsColumn")}</th>
                  {shownMetrics.map((m) => (
                    <th key={m.key}>{m.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {summary.groups.map((g) => (
                  <tr key={g.key}>
                    <td data-label={t("analysis.groupDataLabel")}>
                      <strong>{groupBy === "source" ? SOURCE_LABELS[g.key] || g.key : g.key}</strong>
                    </td>
                    <td data-label={t("analysis.roastsColumn")}>{g.count}</td>
                    {shownMetrics.map((m) => (
                      <td key={m.key} data-label={m.label}>
                        {meanSd(m, g.metrics[m.key])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <AnalysisInsights filters={applied} groupBy={groupBy} />

      {yMetric && (
        <div className="panel">
          <h3>{t("analysis.standoutHeading")}</h3>
          <p className="hint">
            {t("analysis.standoutHintPrefix")} <strong>{yMetric.label}</strong> {t("analysis.standoutHintSuffix")}
          </p>
          {outliers.length === 0 ? (
            <p className="hint">{t("analysis.standoutNone")}</p>
          ) : (
            <ul className="outlier-list">
              {outliers.map(({ row, group, value, z }) => (
                <li key={`${group}-${row.id}`}>
                  <Link to={`/roasts/${row.id}`}>{row.title}</Link>{" "}
                  <span className="hint">
                    {new Date(row.created_at).toLocaleDateString()} · {group} · {formatMetric(yMetric, value, tempUnit)} (
                    {z > 0 ? "+" : ""}
                    {z.toFixed(1)} SD)
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
