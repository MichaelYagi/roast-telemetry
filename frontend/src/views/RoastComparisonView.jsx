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

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend);

const PALETTE = ["#dc2626", "#2563eb", "#16a34a", "#ca8a04", "#9333ea", "#0891b2", "#334155"];

export default function RoastComparisonView() {
  const [roasts, setRoasts] = useState([]);
  const [selectedIds, setSelectedIds] = useState([]);
  const [details, setDetails] = useState({});
  const [curve, setCurve] = useState("bt");

  useEffect(() => {
    api.listRoasts({ limit: 200 }).then(setRoasts);
  }, []);

  function toggle(id) {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  useEffect(() => {
    selectedIds.forEach((id) => {
      if (!details[id]) {
        api.getRoast(id).then((roast) => setDetails((prev) => ({ ...prev, [id]: roast })));
      }
    });
  }, [selectedIds]); // eslint-disable-line react-hooks/exhaustive-deps

  const data = useMemo(() => {
    return {
      datasets: selectedIds
        .filter((id) => details[id])
        .map((id, i) => ({
          label: details[id].title,
          data: details[id].profile.map((p) => ({ x: p.time_s, y: p[curve] })),
          borderColor: PALETTE[i % PALETTE.length],
          backgroundColor: PALETTE[i % PALETTE.length],
          pointRadius: 0,
          borderWidth: 2,
          tension: 0.15,
        })),
    };
  }, [selectedIds, details, curve]);

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
        y: { type: "linear", title: { display: true, text: `${curve.toUpperCase()} (°C)` } },
      },
    }),
    [curve]
  );

  return (
    <div className="compare-view">
      <div className="panel">
        <h2>Roast Comparison</h2>
        <label>
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
                <label>
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(r.id)}
                    onChange={() => toggle(r.id)}
                  />
                  {r.title}
                </label>
              </li>
            ))}
          </ul>
        </div>
        <div className="panel" style={{ flex: 1, height: 420 }}>
          {selectedIds.length === 0 ? <p>Select roasts to overlay their curves.</p> : <Line data={data} options={options} />}
        </div>
      </div>
    </div>
  );
}
