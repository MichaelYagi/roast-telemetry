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
import { Line } from "react-chartjs-2";
import { api } from "../api/client.js";
import { STAT_ROW_DEFS, formatRoastStatRow } from "../roastStats.js";
import { celsiusToUnit, unitSuffix } from "../tempUnits.js";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend);

const PALETTE = ["#dc2626", "#2563eb", "#16a34a", "#ca8a04", "#9333ea", "#0891b2", "#334155"];

export default function RoastComparisonView() {
  const [roasts, setRoasts] = useState([]);
  const [selectedIds, setSelectedIds] = useState([]);
  const [details, setDetails] = useState({});
  const [stats, setStats] = useState({});
  const [curve, setCurve] = useState("bt");
  const [tempUnit, setTempUnit] = useState("c"); // display only, see Settings > Temperature Unit

  useEffect(() => {
    api.listRoasts({ limit: 200 }).then(setRoasts);
  }, []);

  // One-time fetch, not a live subscription -- same rationale as
  // RoastDetailView.jsx.
  useEffect(() => {
    api.getSettings().then((s) => setTempUnit(s.temperature_unit || "c"));
  }, []);

  function toggle(id) {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  useEffect(() => {
    selectedIds.forEach((id) => {
      if (!details[id]) {
        api.getRoast(id).then((roast) => setDetails((prev) => ({ ...prev, [id]: roast })));
      }
      if (!stats[id]) {
        api.getRoastStats(id).then((s) => setStats((prev) => ({ ...prev, [id]: s })));
      }
    });
  }, [selectedIds]); // eslint-disable-line react-hooks/exhaustive-deps

  const isRate = curve.startsWith("ror_");

  // Filtered-then-indexed once here, reused for both the chart's own
  // per-dataset color below AND the stats table's column headers, so a
  // roast's color stays consistent between the two -- same list, same
  // index, not two independently-filtered copies that could drift if a
  // detail fetch resolves out of order.
  const readyIds = selectedIds.filter((id) => details[id]);

  const data = useMemo(() => {
    // Rate curves (°C/min) convert by scale only, no +32 offset -- see
    // RoastChart.jsx's identical convertRor for the same reasoning.
    const convert = (v) => (v == null ? null : isRate ? (tempUnit === "f" ? v * 1.8 : v) : celsiusToUnit(v, tempUnit));
    return {
      datasets: readyIds
        .map((id, i) => ({
          label: details[id].title,
          data: details[id].profile.map((p) => ({ x: p.time_s, y: convert(p[curve]) })),
          borderColor: PALETTE[i % PALETTE.length],
          backgroundColor: PALETTE[i % PALETTE.length],
          pointRadius: 0,
          borderWidth: 2,
          tension: 0.15,
        })),
    };
  }, [selectedIds, details, curve, tempUnit, isRate]);

  const options = useMemo(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      parsing: false,
      normalized: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "top" } },
      scales: {
        x: { type: "linear", title: { display: true, text: "Time (s)" } },
        y: {
          type: "linear",
          title: { display: true, text: `${curve.toUpperCase()} (${unitSuffix(tempUnit)}${isRate ? "/min" : ""})` },
        },
      },
    }),
    [curve, tempUnit, isRate]
  );

  return (
    <div className="compare-view">
      <div className="panel">
        <h2>Roast Comparison</h2>
        <label className="standalone-field-label">
          Curve
          <select value={curve} onChange={(e) => setCurve(e.target.value)}>
            <option value="bt">Bean Temp (BT)</option>
            <option value="et">Environment Temp (ET)</option>
            <option value="ror_bt">Rate of Rise (BT)</option>
          </select>
        </label>
      </div>

      <div className="compare-body">
        <div className="panel roast-picker">
          <h3>Select roasts</h3>
          <ul>
            {roasts.map((r) => (
              <li key={r.id}>
                <label className="roast-picker-item">
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(r.id)}
                    onChange={() => toggle(r.id)}
                  />
                  <span>
                    <span className="roast-picker-title">{r.title}</span>
                    {/* Title alone isn't enough to tell roasts apart, especially
                        with generic/repeated titles -- date + beans give the
                        context needed to know what's actually being compared. */}
                    <span className="roast-picker-meta">
                      {new Date(r.created_at).toLocaleDateString()}
                      {r.beans ? ` · ${r.beans}` : ""}
                    </span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </div>
        <div className="panel" style={{ flex: 1, height: 420 }}>
          {selectedIds.length === 0 ? <p>Select roasts to overlay their curves.</p> : <Line data={data} options={options} />}
        </div>
      </div>

      {readyIds.length > 0 && (
        <div className="panel compare-stats-table">
          <h3>Roast Stats</h3>
          <div className="table-scroll">
            <table className="roast-table">
              <thead>
                <tr>
                  <th></th>
                  {readyIds.map((id, i) => (
                    <th key={id} style={{ color: PALETTE[i % PALETTE.length] }}>
                      <div>{details[id].title}</div>
                      <div className="compare-th-meta">
                        {new Date(details[id].created_at).toLocaleDateString()}
                        {details[id].beans ? ` · ${details[id].beans}` : ""}
                      </div>
                      {details[id].tags && details[id].tags.length > 0 && (
                        <div className="compare-th-meta">
                          {details[id].tags.map((t) => (
                            <span key={t} className="tag-chip">
                              {t}
                            </span>
                          ))}
                        </div>
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {STAT_ROW_DEFS.map(({ key, label }) => (
                  <tr key={key}>
                    <td>{label}</td>
                    {readyIds.map((id) => (
                      <td key={id}>{formatRoastStatRow(key, stats[id])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
