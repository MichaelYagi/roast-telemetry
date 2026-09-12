import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client.js";
import RoastChart from "../components/RoastChart.jsx";

export default function RoastDetailView() {
  const { id } = useParams();
  const [roast, setRoast] = useState(null);
  const [machine, setMachine] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .getRoast(id)
      .then(setRoast)
      .catch((err) => setError(err.message));
  }, [id]);

  useEffect(() => {
    if (roast?.machine_id) {
      api.getMachine(roast.machine_id).then(setMachine).catch(() => setMachine(null));
    }
  }, [roast?.machine_id]);

  if (error) return <p className="error panel">{error}</p>;
  if (!roast) return <p className="panel">Loading…</p>;

  return (
    <div className="detail-view">
      <div className="panel">
        <h2>{roast.title}</h2>
        <p className="sub">
          {roast.mode} · status: <strong>{roast.status}</strong> · duration:{" "}
          {roast.duration_s ? `${Math.floor(roast.duration_s / 60)}:${String(Math.round(roast.duration_s % 60)).padStart(2, "0")}` : "—"}
        </p>
        {roast.alog_path && (
          <a href={api.alogDownloadUrl(roast.id)} target="_blank" rel="noreferrer">
            Download .alog
          </a>
        )}
      </div>

      <div className="panel">
        <RoastChart profile={roast.profile} events={roast.events} />
      </div>

      <div className="detail-grid">
        <div className="panel">
          <h3>Machine</h3>
          {machine ? (
            <ul className="kv-list">
              <li>
                <span>Brand</span>
                <span>{machine.brand}</span>
              </li>
              <li>
                <span>Model</span>
                <span>{machine.model}</span>
              </li>
              <li>
                <span>Connection</span>
                <span>{machine.connection_type}</span>
              </li>
              <li>
                <span>Control capable</span>
                <span>{machine.control_capable ? "Yes" : "No"}</span>
              </li>
            </ul>
          ) : roast.machine_label ? (
            <ul className="kv-list">
              <li>
                <span>Roaster</span>
                <span>{roast.machine_label}</span>
              </li>
            </ul>
          ) : (
            <p>No machine associated with this roast.</p>
          )}
          <h3>Batch</h3>
          <ul className="kv-list">
            <li>
              <span>Beans</span>
              <span>{roast.beans || "—"}</span>
            </li>
            <li>
              <span>Green weight</span>
              <span>{roast.weight_green_g ? `${roast.weight_green_g} g` : "—"}</span>
            </li>
            <li>
              <span>Roasted weight</span>
              <span>{roast.weight_roasted_g ? `${roast.weight_roasted_g} g` : "—"}</span>
            </li>
          </ul>
        </div>

        <div className="panel">
          <h3>Events</h3>
          <ul className="event-feed">
            {roast.events.map((ev) => (
              <li key={ev.id}>
                <strong>{ev.label}</strong> @ {Math.floor(ev.time_s / 60)}:
                {String(Math.round(ev.time_s % 60)).padStart(2, "0")}
                {ev.value != null && ev.channel ? ` (${ev.channel}: ${ev.value})` : ev.value != null ? ` (${ev.value}°C)` : ""}
              </li>
            ))}
          </ul>
        </div>

        <div className="panel">
          <h3>Notes</h3>
          <ul className="note-feed">
            {roast.notes.map((n) => (
              <li key={n.id}>{n.text}</li>
            ))}
            {roast.notes.length === 0 && <li>No notes.</li>}
          </ul>
        </div>
      </div>
    </div>
  );
}
