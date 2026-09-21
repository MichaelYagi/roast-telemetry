import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client.js";

function fmt(seconds) {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

// Automatic control: repeat a saved roast's heater/fan/drum settings, or hold a
// target by adjusting the heater. Moving any slider by hand takes over again.
export default function AutoControlPanel({ roastId }) {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [past, setPast] = useState([]);
  const [pastId, setPastId] = useState("");
  const [variable, setVariable] = useState("ror_bt");
  const [target, setTarget] = useState("");
  const [maxHeater, setMaxHeater] = useState(100);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    if (!roastId) return;
    try {
      setStatus(await api.getControl(roastId));
    } catch {
      /* the roast may have just ended */
    }
  }, [roastId]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    api
      .listRoasts({ status: "complete", limit: 50 })
      .then((rows) => setPast(rows.filter((r) => r.id !== roastId)))
      .catch(() => {});
  }, [roastId]);

  async function run(action) {
    setBusy(true);
    setError(null);
    try {
      setStatus(await action());
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (!status || !status.can_write) return null;

  const { program, feedback, tripped_reason: tripped } = status;
  const unit = variable === "bt" ? "°C" : "°C/min";

  return (
    <div className="panel auto-control-panel">
      <h3>Automatic control</h3>
      <p className="hint">
        Starts at Charge. Moving any slider by hand takes over from it. Check the roaster is behaving before
        leaving it to run, and keep your hand near the Emergency stop.
      </p>

      {tripped && (
        <p className="error" role="alert">
          Stopped for safety: {tripped}. The heater is off. Move a slider or start control again to continue.
        </p>
      )}

      {program && (
        <p className="auto-status">
          Repeating <strong>{program.label}</strong> —{" "}
          {program.waiting_for_charge ? "waiting for Charge" : program.next_step_in_s != null ? `next change in ${fmt(program.next_step_in_s)}` : "finished its steps"}
        </p>
      )}
      {feedback && (
        <p className="auto-status">
          Holding {feedback.variable === "bt" ? "bean temperature" : "rate of rise"} at{" "}
          <strong>{feedback.setpoint ?? "—"}</strong> — heater {feedback.output_pct}%
          {feedback.waiting_for_charge ? " (waiting for Charge)" : ""}
        </p>
      )}
      {(program || feedback) && (
        <button type="button" onClick={() => run(() => api.stopAutomation(roastId))} disabled={busy}>
          Stop automatic control
        </button>
      )}

      <h4>Repeat a saved roast</h4>
      <div className="form-row">
        <select value={pastId} onChange={(e) => setPastId(e.target.value)}>
          <option value="">Choose a roast…</option>
          {past.map((r) => (
            <option key={r.id} value={r.id}>
              {r.title} ({new Date(r.created_at).toLocaleDateString()})
            </option>
          ))}
        </select>
        <button type="button" disabled={!pastId || busy} onClick={() => run(() => api.startProgramFromRoast(roastId, pastId))}>
          Repeat it
        </button>
      </div>

      <h4>Hold a target</h4>
      <div className="form-row">
        <select value={variable} onChange={(e) => setVariable(e.target.value)}>
          <option value="ror_bt">Rate of rise</option>
          <option value="bt">Bean temperature</option>
        </select>
        <label className="inline-field">
          Target ({unit})
          <input type="number" step="0.1" value={target} onChange={(e) => setTarget(e.target.value)} />
        </label>
        <label className="inline-field">
          Heater max (%)
          <input type="number" min="0" max="100" value={maxHeater} onChange={(e) => setMaxHeater(Number(e.target.value))} />
        </label>
        <button
          type="button"
          disabled={target === "" || busy}
          onClick={() => run(() => api.startFeedback(roastId, { variable, setpoint: Number(target), output_max_pct: maxHeater }))}
        >
          Hold it
        </button>
      </div>
      <p className="hint">The heater changes gradually, never faster than 1% per second, and stops at Drop.</p>

      {error && <p className="error">{error}</p>}
    </div>
  );
}
