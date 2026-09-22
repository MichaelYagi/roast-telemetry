import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { formatMetric, groupMetrics } from "../lib/metricFormat.js";

// Every number worked out for one roast (GET /analysis/roasts/{id}): the
// temperatures and times at each milestone, phase times, rate of rise, and so on.
// Only the ones this roast actually has are listed.
export default function RoastNumbers({ roastId, tempUnit = "c", refreshKey = 0 }) {
  const [row, setRow] = useState(null);
  const [metrics, setMetrics] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.getAnalysisMetrics().then(setMetrics).catch(() => {});
  }, []);

  useEffect(() => {
    setRow(null);
    setError(null);
    api
      .getRoastNumbers(roastId)
      .then(setRow)
      .catch((err) => setError(err.message));
  }, [roastId, refreshKey]);

  if (error) return <p className="hint">{error}</p>;
  if (!row) return <p>Loading…</p>;

  const groups = groupMetrics(metrics)
    .map(([name, list]) => [name, list.filter((m) => row.metrics[m.key] != null)])
    .filter(([, list]) => list.length > 0);
  if (groups.length === 0) return <p className="hint">No numbers yet — mark Charge and Drop to get them.</p>;

  return (
    <div className="numbers-groups">
      {groups.map(([name, list]) => (
        <div key={name}>
          <h4>{name}</h4>
          <ul className="kv-list">
            {list.map((m) => (
              <li key={m.key}>
                <span>{m.label}</span>
                <span>{formatMetric(m, row.metrics[m.key], tempUnit)}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
