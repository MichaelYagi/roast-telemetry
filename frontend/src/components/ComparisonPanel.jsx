import {
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
} from "chart.js";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Line } from "react-chartjs-2";
import { api } from "../api/client.js";
import { formatMetric, formatSeconds, groupMetrics, isRate, isTemperature } from "../lib/metricFormat.js";
import { celsiusToUnit, unitSuffix } from "../tempUnits.js";
import SavedViews from "./SavedViews.jsx";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend);

const PALETTE = ["#dc2626", "#2563eb", "#16a34a", "#ca8a04", "#9333ea", "#0891b2", "#db2777", "#334155"];
// Past eight roasts the colours repeat, so the line style changes to keep them apart.
const DASHES = [[], [7, 4], [2, 3]];

// The number groups that start open; the rest start collapsed.
const OPEN_BY_DEFAULT = ["Timing", "Temperatures", "Phases"];

const colorFor = (i) => PALETTE[i % PALETTE.length];
const dashFor = (i) => DASHES[Math.floor(i / PALETTE.length) % DASHES.length];

// How far a value is from the baseline's, in the same units it is shown in.
function formatDelta(metric, delta, tempUnit) {
  if (delta == null || Number.isNaN(delta) || delta === 0) return null;
  const sign = delta > 0 ? "+" : "-";
  const size = Math.abs(delta);
  if (metric.unit === "s") return `${sign}${formatSeconds(size)}`;
  const scaled = isTemperature(metric) || isRate(metric) ? (tempUnit === "f" ? size * 1.8 : size) : size;
  return `${sign}${scaled.toFixed(1).replace(/\.0$/, "")}`;
}

// The roasts ticked on the History page, side by side: their curves overlaid and
// one row of numbers per roast, with an optional baseline to measure the others
// against. `ids` is the set being compared -- it follows the ticks in the table,
// so unticking a roast there removes it here. `onRemoveId` unticks one (used
// when a roast can't be loaded), `onLoadIds` replaces the whole selection (a
// saved view), and `onClose` goes back to the plain list.
export default function ComparisonPanel({ ids, onRemoveId, onLoadIds, onClose, titleFor, initialConfig }) {
  const { t } = useTranslation();
  const [details, setDetails] = useState({});
  const [numbers, setNumbers] = useState({});
  const [metrics, setMetrics] = useState([]);
  const [loadErrors, setLoadErrors] = useState({}); // roast id -> why it couldn't be loaded
  // `initialConfig` is a saved view opened from the History page.
  const [curve, setCurve] = useState(initialConfig?.curve || "bt");
  const [alignOnCharge, setAlignOnCharge] = useState(initialConfig ? initialConfig.alignOnCharge !== false : true);
  const [baselineId, setBaselineId] = useState(initialConfig?.baselineId || null);
  const [hiddenIds, setHiddenIds] = useState(Array.isArray(initialConfig?.hiddenIds) ? initialConfig.hiddenIds : []); // roasts hidden from the graph (still in the table)
  const [openGroups, setOpenGroups] = useState(() => new Set(OPEN_BY_DEFAULT));
  const [sort, setSort] = useState(null); // { key, dir: 1 | -1 } or null for the selection order
  const [tempUnit, setTempUnit] = useState("c"); // display only, see Settings > Temperature Unit

  useEffect(() => {
    api.getAnalysisMetrics().then(setMetrics).catch(() => {});
    api.getSettings().then((s) => setTempUnit(s.temperature_unit || "c"));
  }, []);

  // One-time fetch per roast, not a live subscription -- same rationale as RoastDetailView.jsx.
  useEffect(() => {
    ids.forEach((id) => {
      if (!details[id] && !loadErrors[id]) {
        api
          .getRoast(id)
          .then((roast) => setDetails((prev) => ({ ...prev, [id]: roast })))
          .catch((err) => {
            // A roast whose recording is gone can't be compared: say so and untick it.
            setLoadErrors((prev) => ({ ...prev, [id]: err.message }));
            onRemoveId(id);
          });
      }
      if (!numbers[id]) {
        api
          .getRoastNumbers(id)
          .then((row) => setNumbers((prev) => ({ ...prev, [id]: row })))
          .catch(() => setNumbers((prev) => ({ ...prev, [id]: { metrics: {} } })));
      }
    });
  }, [ids]); // eslint-disable-line react-hooks/exhaustive-deps

  const readyIds = ids.filter((id) => details[id]);
  const activeBaseline = readyIds.includes(baselineId) ? baselineId : null;
  const isRateCurve = curve.startsWith("ror_");
  const hidden = new Set(hiddenIds);

  const data = useMemo(() => {
    // Rate curves (°C/min) convert by scale only, no +32 offset -- see
    // RoastChart.jsx's identical convertRor for the same reasoning.
    const convert = (v) => (v == null ? null : isRateCurve ? (tempUnit === "f" ? v * 1.8 : v) : celsiusToUnit(v, tempUnit));
    return {
      datasets: readyIds.map((id) => {
        const roast = details[id];
        const charge = roast.events.find((e) => e.type === "CHARGE");
        const offset = alignOnCharge && charge ? charge.time_s : 0;
        const i = ids.indexOf(id); // stable, so a roast keeps its colour when the table is sorted
        return {
          label: roast.title,
          data: roast.profile.map((p) => ({ x: p.time_s - offset, y: convert(p[curve]) })),
          borderColor: colorFor(i),
          backgroundColor: colorFor(i),
          borderDash: dashFor(i),
          hidden: hiddenIds.includes(id),
          pointRadius: 0,
          borderWidth: 2,
          tension: 0.15,
        };
      }),
    };
  }, [ids, details, curve, tempUnit, isRateCurve, alignOnCharge, hiddenIds]); // eslint-disable-line react-hooks/exhaustive-deps

  const options = useMemo(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      parsing: false,
      normalized: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "top", labels: { usePointStyle: true, boxWidth: 8 } } },
      scales: {
        x: {
          type: "linear",
          title: { display: true, text: alignOnCharge ? t("common.comparisonPanel.axisTimeFromCharge") : t("common.comparisonPanel.axisTime") },
          ticks: { callback: (v) => formatSeconds(v) },
        },
        y: {
          type: "linear",
          title: { display: true, text: `${curve.toUpperCase()} (${unitSuffix(tempUnit)}${isRateCurve ? "/min" : ""})` },
        },
      },
    }),
    [curve, tempUnit, isRateCurve, alignOnCharge, t]
  );

  // Columns: every number at least one roast has, grouped.
  const groups = useMemo(
    () =>
      groupMetrics(metrics)
        .map(([name, list]) => ({
          name,
          list: list.filter((m) => readyIds.some((id) => numbers[id]?.metrics?.[m.key] != null)),
        }))
        .filter((g) => g.list.length > 0),
    [metrics, numbers, ids, details] // eslint-disable-line react-hooks/exhaustive-deps
  );

  // Rows: the selection order, or sorted by a number (roasts without it last); the baseline first.
  const rowIds = useMemo(() => {
    let list = [...readyIds];
    if (sort) {
      const value = (id) => numbers[id]?.metrics?.[sort.key];
      list.sort((a, b) => {
        const va = value(a);
        const vb = value(b);
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        return (va - vb) * sort.dir;
      });
    }
    if (activeBaseline) list = [activeBaseline, ...list.filter((id) => id !== activeBaseline)];
    return list;
  }, [readyIds.join(","), numbers, sort, activeBaseline]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleGroup = (name) =>
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  // Clicking a number's heading: smallest first, then largest first, then back to the selection order.
  const cycleSort = (key) =>
    setSort((prev) => (!prev || prev.key !== key ? { key, dir: 1 } : prev.dir === 1 ? { key, dir: -1 } : null));

  const toggleHidden = (id) => setHiddenIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const getConfig = () => ({ ids, curve, alignOnCharge, baselineId, hiddenIds });
  const loadConfig = (config) => {
    if (Array.isArray(config.ids)) onLoadIds(config.ids);
    if (config.curve) setCurve(config.curve);
    setAlignOnCharge(config.alignOnCharge !== false);
    setBaselineId(config.baselineId || null);
    setHiddenIds(Array.isArray(config.hiddenIds) ? config.hiddenIds : []);
  };

  const errorEntries = Object.entries(loadErrors);
  const hiddenCount = readyIds.filter((id) => hidden.has(id)).length;

  return (
    <div className="panel compare-panel">
      <div className="analysis-header">
        <h2>
          {t("common.comparisonPanel.comparing", { count: ids.length })}
          {hiddenCount > 0 && <span className="hint compare-hidden-note"> {t("common.comparisonPanel.hiddenNote", { count: hiddenCount })}</span>}
        </h2>
        <div className="compare-panel-actions">
          <SavedViews kind="compare" getConfig={getConfig} onLoad={loadConfig} />
          <button type="button" onClick={onClose}>
            {t("common.comparisonPanel.closeComparison")}
          </button>
        </div>
      </div>

      <div className="compare-controls">
        <label className="standalone-field-label">
          {t("common.comparisonPanel.curve")}
          <select value={curve} onChange={(e) => setCurve(e.target.value)}>
            <option value="bt">{t("common.comparisonPanel.curveBt")}</option>
            <option value="et">{t("common.comparisonPanel.curveEt")}</option>
            <option value="ror_bt">{t("common.comparisonPanel.curveRorBt")}</option>
          </select>
        </label>
        <label className="checkbox-label">
          <input type="checkbox" checked={alignOnCharge} onChange={(e) => setAlignOnCharge(e.target.checked)} />
          {t("common.comparisonPanel.lineUpOnCharge")}
        </label>
      </div>

      {errorEntries.length > 0 && (
        <div className="error compare-load-errors" role="alert">
          {errorEntries.map(([id, message]) => (
            <p key={id}>
              {titleFor?.(id) || !/not found/.test(message)
                ? titleFor?.(id)
                  ? t("common.comparisonPanel.couldntCompareNamed", { title: titleFor(id), message: message.replace(/\.$/, "") })
                  : t("common.comparisonPanel.couldntCompareGeneric", { message: message.replace(/\.$/, "") })
                : t("common.comparisonPanel.roastNoLongerExists")}
            </p>
          ))}
        </div>
      )}

      <div className="compare-chart">{readyIds.length === 0 ? <p>{t("common.comparisonPanel.loading")}</p> : <Line data={data} options={options} />}</div>

      {readyIds.length > 0 && (
        <>
          <h3>{t("common.comparisonPanel.numbersHeading")}</h3>
          <p className="hint">
            {t("common.comparisonPanel.numbersHint.prefix")} <em>+0:12</em> {t("common.comparisonPanel.numbersHint.or")} <em>-3.5</em>
            {t("common.comparisonPanel.numbersHint.exampleClose")}
            {t("common.comparisonPanel.numbersHint.suffix")}
          </p>
          <div className="table-scroll">
            <table className="roast-table compare-table">
              <thead>
                <tr>
                  <th className="compare-sticky" rowSpan={2}>
                    {t("common.comparisonPanel.roastColumn")}
                  </th>
                  {groups.map((g) => (
                    <th key={g.name} colSpan={openGroups.has(g.name) ? g.list.length : 1} className="compare-group-head">
                      <button type="button" className="link-like" onClick={() => toggleGroup(g.name)} aria-expanded={openGroups.has(g.name)}>
                        {openGroups.has(g.name) ? "▾" : "▸"} {g.name}
                      </button>
                    </th>
                  ))}
                </tr>
                <tr>
                  {groups.flatMap((g) =>
                    openGroups.has(g.name)
                      ? g.list.map((m) => (
                          <th key={m.key} className="compare-metric-head">
                            <button type="button" className="link-like" onClick={() => cycleSort(m.key)} title={t("common.comparisonPanel.sortByThisNumber")}>
                              {m.label}
                              {sort?.key === m.key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
                            </button>
                          </th>
                        ))
                      : [<th key={`${g.name}-closed`} className="compare-metric-head" />]
                  )}
                </tr>
              </thead>
              <tbody>
                {rowIds.map((id) => {
                  const isHidden = hidden.has(id);
                  const isBase = activeBaseline === id;
                  const roast = details[id];
                  return (
                    <tr key={id} className={`${isHidden ? "compare-row-hidden" : ""}${isBase ? " compare-row-base" : ""}`}>
                      <th className="compare-sticky compare-roast-cell" scope="row">
                        <div className="compare-roast-name">
                          <span className="compare-dot" style={{ background: colorFor(ids.indexOf(id)) }} aria-hidden="true" />
                          <span>{roast.title}</span>
                        </div>
                        <div className="compare-th-meta">
                          {new Date(roast.created_at).toLocaleDateString()}
                          {roast.beans ? ` · ${roast.beans}` : ""}
                        </div>
                        <div className="compare-row-actions">
                          <button type="button" className="link-like" onClick={() => toggleHidden(id)}>
                            {isHidden ? t("common.comparisonPanel.showOnGraph") : t("common.comparisonPanel.hideFromGraph")}
                          </button>
                          <label className="compare-baseline">
                            <input
                              type="radio"
                              name="baseline"
                              checked={isBase}
                              onChange={() => setBaselineId(id)}
                              onClick={() => isBase && setBaselineId(null)}
                            />{" "}
                            {t("common.comparisonPanel.baseline")}
                          </label>
                        </div>
                      </th>
                      {groups.flatMap((g) =>
                        openGroups.has(g.name)
                          ? g.list.map((m) => {
                              const value = numbers[id]?.metrics?.[m.key];
                              const base = activeBaseline ? numbers[activeBaseline]?.metrics?.[m.key] : null;
                              const delta = activeBaseline && !isBase && value != null && base != null ? formatDelta(m, value - base, tempUnit) : null;
                              return (
                                <td key={m.key}>
                                  {formatMetric(m, value, tempUnit)}
                                  {delta && <span className="compare-delta"> {delta}</span>}
                                </td>
                              );
                            })
                          : [<td key={`${g.name}-closed`} className="compare-closed-cell" />]
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {groups.length === 0 && <p className="hint">{t("common.comparisonPanel.noNumbersYet")}</p>}
        </>
      )}
    </div>
  );
}
