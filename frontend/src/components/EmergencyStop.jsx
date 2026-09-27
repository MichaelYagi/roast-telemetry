import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client.js";

// Sits in the roast toolbar beside ON/OFF/START. Heater off, fan to the
// safe level, all automatic control stopped; the roast keeps recording.
//
// Purely a button -- RoastToolbar.jsx owns the /control poll (it already
// needs tripped_reason to show "Stopped for safety: ..." on its own status
// line) and passes down whether this should be disabled: not just while
// the roast isn't ON, but also once it's *already* tripped, so a click
// that actually worked can't be repeated indefinitely (there's nothing
// left for a second click to do until automation restarts or the roast
// ends -- see control.py's clear_trip, only called from start_program/
// start_feedback). onStopped reports the fresh tripped_reason back up
// immediately after a successful click, instead of waiting up to 2s for
// the next poll tick to disable the button.
export default function EmergencyStop({ roastId, disabled, onStopped }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);

  async function stop() {
    if (!roastId || busy || disabled) return;
    setBusy(true);
    try {
      const result = await api.emergencyStop(roastId);
      onStopped?.(result.tripped_reason);
    } catch {
      /* nothing to show here -- RoastToolbar's status line is the source of truth, via its own poll */
    } finally {
      setBusy(false);
    }
  }

  return (
    <button type="button" className="emergency-stop power-btn" onClick={stop} disabled={disabled || busy}>
      {busy ? t("common.emergencyStop.stopping") : t("common.emergencyStop.button")}
    </button>
  );
}
