import { Chart as ChartJS, Legend, LineElement, LinearScale, PointElement, Tooltip } from "chart.js";
import { useEffect, useMemo, useState } from "react";
import { Scatter } from "react-chartjs-2";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import AnalysisInsights from "../components/AnalysisInsights.jsx";
import BulkZipDownload from "../components/BulkZipDownload.jsx";
import SavedViews from "../components/SavedViews.jsx";
import { formatMetric, formatSeconds, groupMetrics, meanAndSd, metricUnitLabel, metricValue } from "../lib/metricFormat.js";

ChartJS.register(LinearScale, PointElement, LineElement, Tooltip, Legend);

const PALETTE = ["#dc2626", "#2563eb", "#16a34a", "#ca8a04", "#9333ea", "#0891b2", "#db2777", "#334155"];

const DATE_KEY = "__date";

const GROUPS = [
  { key: "none", label: "Nothing (one colour)" },
  { key: "beans", label: "Beans" },
  { key: "origin", label: "Origin" },
  { key: "process", label: "Process" },
  { key: "tag", label: "Tag" },
  { key: "month", label: "Month" },
  { key: "roaster", label: "Roasted by" },
  { key: "source", label: "Where the data came from" },
];

const SOURCE_LABELS = { recorded: "Recorded from a device", uploaded: "Uploaded log file", replay: "Replayed log" };

// The numbers the consistency table shows for every group, besides the chart's own Y.
const KEY_METRICS = ["duration_s", "drop_temp_c", "development_time_s", "dtr_pct", "weight_loss_pct"];

const DEFAULTS = {
  filters: { q: "", tag: "", bean_id: "", source: "", created_from: "", created_to: "", include_simulated: false },
  x: "charge_temp_c",
  y: "drop_temp_c",
  groupBy: "beans",
};

// The group(s) a roast belongs to, the same way the server groups them.
function groupKeys(row, groupBy) {
  if (groupBy === "none") return ["All roasts"];
  if (groupBy === "tag") {
    const tags = row.tags.filter((t) => t !== "simulated");
    return tags.length ? tags : ["(no tag)"];
  }
  if (groupBy === "month") return [row.created_at.slice(0, 7)];
  const value = row[groupBy];
  if (groupBy === "source") return [SOURCE_LABELS[value] || value];
  return [value || "(none)"];
}

export default function AnalysisView() {
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
  const xMetric = x === DATE_KEY ? { key: DATE_KEY, label: "Date", unit: "" } : byKey[x];
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
      for (const key of groupKeys(row, groupBy)) {
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
          const color = PALETTE[i % PALETTE.length];
          return {
            label: `${name} (${points.length})`,
            data: points,
            borderColor: color,
            backgroundColor: color,
            pointRadius: 5,
            pointHoverRadius: 7,
            showLine: isDate,
            borderWidth: 1.5,
          };
        }),
      },
    };
  }, [table, x, y, groupBy, tempUnit, xMetric, yMetric]); // eslint-disable-line react-hooks/exhaustive-deps

  const axisTicks = (metric) => (value) => {
    if (metric.key === DATE_KEY) return new Date(value).toLocaleDateString(undefined, { month: "short", year: "2-digit" });
    return metric.unit === "s" ? formatSeconds(value) : value;
  };

  const options = useMemo(() => {
    if (!xMetric || !yMetric) return {};
    const axisTitle = (m) => (m.key === DATE_KEY ? "Date" : `${m.label} (${metricUnitLabel(m, tempUnit)})`);
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
        legend: { position: "top", labels: { usePointStyle: true, boxWidth: 8 } },
        tooltip: {
          callbacks: {
            title: (items) => items[0]?.raw.row.title,
            label: (item) => {
              const { row } = item.raw;
              const xText = x === DATE_KEY ? new Date(row.created_at).toLocaleDateString() : formatMetric(xMetric, row.metrics[x], tempUnit);
              return [`${xMetric.label}: ${xText}`, `${yMetric.label}: ${formatMetric(yMetric, row.metrics[y], tempUnit)}`, "Click to open in a new tab"];
            },
          },
        },
      },
      scales: {
        x: { type: "linear", title: { display: true, text: axisTitle(xMetric) }, ticks: { callback: axisTicks(xMetric) } },
        y: { type: "linear", title: { display: true, text: axisTitle(yMetric) }, ticks: { callback: axisTicks(yMetric) } },
      },
    };
  }, [xMetric, yMetric, tempUnit, x]); // eslint-disable-line react-hooks/exhaustive-deps

  // -- consistency table ------------------------------------------------------------
  const shownMetrics = useMemo(() => {
    const keys = [...new Set([y, ...KEY_METRICS])].filter((k) => byKey[k]);
    return keys.map((k) => byKey[k]);
  }, [y, byKey]);

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
      for (const key of groupKeys(row, groupBy)) {
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
      {withDate && <option value={DATE_KEY}>Date</option>}
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
          <h2>Analysis</h2>
          <SavedViews kind="analysis" getConfig={getConfig} onLoad={loadConfig} />
        </div>
        <p className="hint">
          Every finished roast, recorded or uploaded, is one point. Pick what to plot and what to group by. Roasts made on
          simulated devices, and replays of a saved log (which just repeat its data), are left out unless you ask.
        </p>

        <form className="filters-grid analysis-filters" onSubmit={apply}>
          <label className="filter-search">
            Search
            <input placeholder="Title, beans, or tag…" value={filters.q} onChange={setFilter("q")} />
          </label>
          <label>
            Beans
            <select value={filters.bean_id} onChange={setFilter("bean_id")}>
              <option value="">All</option>
              {beans.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Tag
            <select value={filters.tag} onChange={setFilter("tag")}>
              <option value="">All</option>
              {tags.map((t) => (
                <option key={t.tag} value={t.tag}>
                  {t.tag} ({t.count})
                </option>
              ))}
            </select>
          </label>
          <label>
            Data from
            <select value={filters.source} onChange={setFilter("source")}>
              <option value="">Recorded and uploaded</option>
              <option value="recorded">Recorded from a device</option>
              <option value="uploaded">Uploaded log files</option>
              <option value="replay">Replayed logs</option>
            </select>
          </label>
          <label>
            From
            <input type="date" value={filters.created_from} onChange={setFilter("created_from")} />
          </label>
          <label>
            To
            <input type="date" value={filters.created_to} onChange={setFilter("created_to")} />
          </label>
          <div className="analysis-filter-extras">
            <label className="checkbox-label">
              <input type="checkbox" checked={filters.include_simulated} onChange={setFilter("include_simulated")} />
              Include simulated
            </label>
          </div>
          <div className="analysis-filter-extras">
            <button type="submit" className={dirty ? "" : "button-quiet"}>
              Apply filters
            </button>
            {dirty && <span className="hint">Filters changed. Press Apply to update.</span>}
            {(anyFilter || x !== DEFAULTS.x || y !== DEFAULTS.y || groupBy !== DEFAULTS.groupBy) && (
              <button type="button" className="link-like" onClick={reset}>
                Reset
              </button>
            )}
          </div>
        </form>
      </div>

      <div className="panel">
        <div className="analysis-pickers">
          <label>
            Across (X)
            <select value={x} onChange={(e) => setX(e.target.value)}>
              {metricOptions(true)}
            </select>
          </label>
          <label>
            Up (Y)
            <select value={y} onChange={(e) => setY(e.target.value)}>
              {metricOptions(false)}
            </select>
          </label>
          <label>
            Colour by
            <select value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
              {GROUPS.map((g) => (
                <option key={g.key} value={g.key}>
                  {g.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {error && <p className="error">{error}</p>}
        {loading && !table ? (
          <p>Loading…</p>
        ) : table && table.total === 0 ? (
          <p className="hint">
            No finished roasts match. Upload logs on the <Link to="/history">History</Link> page, or loosen the filters.
          </p>
        ) : (
          <>
            {chart && chart.shown > 0 && xMetric && yMetric && (
              <p className="analysis-caption">
                Each dot is one roast: <strong>{xMetric.label}</strong> across, <strong>{yMetric.label}</strong> up
                {groupBy === "none" ? "" : `, coloured by ${GROUPS.find((g) => g.key === groupBy)?.label.toLowerCase()}`}. Hover a dot
                to see which roast it is; click it to open it in a new tab.
                {chart.shown < 5 && " With so few roasts there's no pattern to see yet; add more roasts or loosen the filters."}
              </p>
            )}
            <div className="analysis-chart">{chart && chart.shown > 0 ? <Scatter data={chart.data} options={options} /> : null}</div>
            {chart && chart.shown === 0 && table && table.total > 0 && (
              <p className="hint">None of these roasts have both of those numbers (for example, no Drop marked).</p>
            )}
            <p className="hint analysis-count">
              {table ? `${table.total} roast${table.total === 1 ? "" : "s"}` : ""}
              {table?.truncated ? " (showing the newest 2000)" : ""}
              {table?.simulated_excluded ? `; ${table.simulated_excluded} simulated left out` : ""}
              {table?.missing_recording
                ? `; ${table.missing_recording} left out because their recording file is missing or unreadable`
                : ""}
              .{" "}
              <BulkZipDownload params={applied} tempUnit={tempUnit} />
            </p>
          </>
        )}
      </div>

      {summary && summary.groups.length > 0 && (
        <div className="panel">
          <h3>How consistent</h3>
          <p className="hint">
            Average ± spread (standard deviation) for each group. A smaller spread means more repeatable roasts.
          </p>
          <div className="table-scroll">
            <table className="roast-table analysis-table">
              <thead>
                <tr>
                  <th>{GROUPS.find((g) => g.key === groupBy)?.label.replace("Nothing (one colour)", "All roasts")}</th>
                  <th>Roasts</th>
                  {shownMetrics.map((m) => (
                    <th key={m.key}>{m.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {summary.groups.map((g) => (
                  <tr key={g.key}>
                    <td data-label="Group">
                      <strong>{groupBy === "source" ? SOURCE_LABELS[g.key] || g.key : g.key}</strong>
                    </td>
                    <td data-label="Roasts">{g.count}</td>
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
          <h3>Roasts that stand out</h3>
          <p className="hint">
            Roasts more than two standard deviations from the rest of their group on <strong>{yMetric.label}</strong>{" "}
            (only groups of five or more roasts).
          </p>
          {outliers.length === 0 ? (
            <p className="hint">None.</p>
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
