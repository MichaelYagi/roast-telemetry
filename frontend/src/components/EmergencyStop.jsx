import { useState } from "react";
import { api } from "../api/client.js";

// Always-visible stop for anything that drives the roaster: heater off, fan to
// the safe level, all automatic control stopped. The roast keeps recording.
export default function EmergencyStop({ roastId, disabled }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);

  async function stop() {
    if (!roastId || busy) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.emergencyStop(roastId);
      setMessage(result.ok ? "Heater off, fan up. The roast is still recording." : "Couldn't reach the roaster! Switch it off at the machine.");
    } catch (err) {
      setMessage(`Couldn't send the stop: ${err.message}. Switch the roaster off at the machine.`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="emergency-stop-row no-print">
      <button type="button" className="emergency-stop" onClick={stop} disabled={disabled || busy}>
        {busy ? "Stopping…" : "Emergency stop"}
      </button>
      {message && <span className="emergency-stop-message" role="status">{message}</span>}
    </div>
  );
}
