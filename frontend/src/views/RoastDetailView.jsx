import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client.js";
import RoastChart from "../components/RoastChart.jsx";
import RoastReviewCard from "../components/RoastReviewCard.jsx";

// Mirrors the Configure Roast form's <option> labels (LiveRoastView.jsx)
// so history shows the same human-readable name, not the raw mode enum.
const MODE_LABELS = {
  simulator: "Artisan Simulator",
  alog_playback: ".alog Playback",
  artisan_live: "Artisan Live Bridge",
  modbus_live: "Direct Modbus (FZ-94, USB)",
  ms6514_live: "Direct USB (Mastech MS6514)",
};

// Mirrors backend/app/api/roasts.py's alog_filename() exactly -- same
// input (title + the raw created_at ISO string, sliced not reformatted),
// so this label always matches the filename the browser actually saves,
// without a round trip to ask the server what it named it.
function alogFilename(title, createdAt) {
  const safeTitle = title.replace(/[\\/:*?"<>|]/g, "_").trim() || "roast";
  const timestamp = createdAt.slice(0, 16).replace("T", "_").replace(":", "");
  return `${safeTitle}_${timestamp}.alog`;
}

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
          // No target="_blank" -- the response is Content-Disposition:
          // attachment, so it downloads without navigating away; adding
          // _blank just pops an empty new tab in some browsers while the
          // file downloads silently in the background, looking like a
          // no-op click. Real Artisan's own native format -- File > Open
          // in Artisan itself opens this directly, no conversion needed.
          <>
            Download <a href={api.alogDownloadUrl(roast.id)}>{alogFilename(roast.title, roast.created_at)}</a>
          </>
        )}
      </div>

      <div className="panel">
        <RoastChart profile={roast.profile} events={roast.events} />
      </div>

      <div className="detail-grid">
        <div className="panel">
          <h3>Data source</h3>
          <ul className="kv-list">
            <li>
              <span>Mode</span>
              <span>{MODE_LABELS[roast.mode] || roast.mode}</span>
            </li>
            {roast.mode === "alog_playback" && (
              <>
                <li>
                  <span>Source file</span>
                  <span>{roast.source_alog_path || "—"}</span>
                </li>
                <li>
                  <span>Speed</span>
                  <span>{roast.playback_speed != null ? `${roast.playback_speed}x` : "—"}</span>
                </li>
              </>
            )}
          </ul>

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

        <RoastReviewCard roastId={roast.id} roastActive={roast.status === "roasting" || roast.status === "cooling"} />
      </div>
    </div>
  );
}
