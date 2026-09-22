import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client.js";
import EmergencyStop from "./EmergencyStop.jsx";

// The top toolbar during a roast: the ON/OFF
// device toggle + START recording button, an Emergency stop (only for the
// modes that can actually drive the roaster -- see showEmergencyStop --
// greyed out via `roasterIsOn` below until the roast is actually ON; not
// LiveRoastView's own `isActive`, which only turns true once the roast
// object itself is populated -- lagging behind "ON" by as much as the rest
// of the armed phase, since connecting alone only sets phase/roastId, see
// handleToggleConnect), and a big digital elapsed-time clock. Used to also
// carry a fixed DRY%/»DRY/»FCs milestone-box row, but those are just as
// available (and freely choosable alongside DEV%/DEV TIME/etc.) via the
// Big/Small Readout Panel now, so hardcoding this one fixed trio here was
// redundant.
//
// Owns two polls. GET /control (same endpoint AutoControlPanel.jsx
// separately polls for its own purposes) drives two things: showing
// "Stopped for safety: ..." on the status line the moment a trip happens
// (not just tucked away in AutoControlPanel, which needs scrolling past
// the chart to see), and disabling Emergency Stop once it's already
// tripped, so a click that worked can't be mashed repeatedly. That one
// only exists once a roast/session does, though (there's no /control to
// poll before ON) -- so whether to *hide* the button for safety_disabled
// comes from a separate GET /settings poll instead, the one source for
// that flag that's meaningful with or without an active roast (a checkbox
// change in Settings needs to hide the button on the Configure Roast
// screen too, before any roastId exists yet).
//
// connectionStatus: "checking" | "pass" | "fail" | null (null/undefined
// -- no live-hardware connection worth showing a dot for, e.g.
// simulator/alog_playback, or idle/finished). "checking" is yellow --
// shown right after ON, before enough readings have come in to call it
// either way -- turning green once they look normal, or red if they
// don't. See useConnectionHealth.js for how this gets computed.
export default function RoastToolbar({
  title,
  beans,
  weightGreenG,
  phase,
  elapsedLabel,
  statusText,
  onToggleConnect,
  onStart,
  connectionStatus,
  connectionFailedLabels = [],
  simulated = false,
  roastId,
  showEmergencyStop = false,
}) {
  const connected = phase !== "idle";
  const recording = phase === "roasting" || phase === "cooling" || phase === "finished";
  // Distinct from `connected` above: that one also counts "finished" (so
  // the power button still reads as its own state, not "ON"/"OFF" again),
  // but a finished roast has nothing left to emergency-stop -- the device
  // is already disconnected. Emergency stop should grey out again the
  // moment OFF/RESET is pressed, not stay lit through "finished" too.
  const roasterIsOn = phase === "armed" || phase === "roasting" || phase === "cooling";
  // Beans/weight are both optional (Configure Roast never requires
  // either) -- only append what's actually there, and only the " · "
  // separator between them when both are, rather than a dangling one.
  const meta = [beans, weightGreenG != null ? `${weightGreenG} g` : null].filter(Boolean).join(" · ");

  const [controlStatus, setControlStatus] = useState(null);
  const [safetyDisabled, setSafetyDisabled] = useState(false);

  const refreshControl = useCallback(async () => {
    if (!showEmergencyStop || !roastId) return;
    try {
      setControlStatus(await api.getControl(roastId));
    } catch {
      /* the roast may have just ended */
    }
  }, [roastId, showEmergencyStop]);

  useEffect(() => {
    setControlStatus(null); // a new/no roastId means the previous roast's status no longer applies
    refreshControl();
    const timer = setInterval(refreshControl, 2000);
    return () => clearInterval(timer);
  }, [refreshControl]);

  const refreshSafetyDisabled = useCallback(async () => {
    if (!showEmergencyStop) return;
    try {
      const settings = await api.getSettings();
      setSafetyDisabled(Boolean(settings.control?.safety_disabled));
    } catch {
      /* leave the last known value -- a transient fetch failure shouldn't flip the button back on */
    }
  }, [showEmergencyStop]);

  useEffect(() => {
    refreshSafetyDisabled();
    const timer = setInterval(refreshSafetyDisabled, 2000);
    return () => clearInterval(timer);
  }, [refreshSafetyDisabled]);

  const trippedReason = controlStatus?.tripped_reason || null;

  return (
    <div className="roast-toolbar-wrap">
      <div className="roast-toolbar">
        {/* Title is a required field (see LiveRoastView.jsx's
            requireTitle()), so this is live -- what you're currently
            typing in Configure Roast's General tab, then whatever the
            roast was actually created with once connected. The status
            line sits directly under it, not as its own separate row --
            both are on the left, controls/clock on the right, same row. */}
        <div className="roast-toolbar-title-group">
          <h2 className="roast-toolbar-title">
            {connectionStatus && (
              <span
                className={`connection-status-dot connection-status-dot-${connectionStatus}`}
                title={
                  connectionStatus === "pass"
                    ? "Readings look normal"
                    : connectionStatus === "fail"
                      ? `${connectionFailedLabels.join(", ")} out of range -- see Test Connection`
                      : "Checking connection…"
                }
              />
            )}
            {title || "Untitled roast"}
            {simulated && (
              <span className="simulated-badge" title="A built-in simulated device -- not a real machine">
                Simulated
              </span>
            )}
            {meta && <span className="roast-toolbar-meta"> · {meta}</span>}
          </h2>
          <div className="status-line">
            {statusText}
            {trippedReason && (
              <span className="status-line-danger" role="alert">
                {" "}
                · Stopped for safety: {trippedReason}.
              </span>
            )}
          </div>
        </div>

        <div className="roast-toolbar-controls">
          <div className="roast-toolbar-power">
            <button
              type="button"
              className={`power-btn ${
                phase === "finished" ? "power-btn-reset" : connected ? "power-btn-off" : "power-btn-on"
              }`}
              onClick={onToggleConnect}
            >
              {/* "finished" shares onToggleConnect's OFF branch (same click
                  handler, calls handleReset) but needs its own label --
                  otherwise this button says "OFF" through four different
                  phases (armed/roasting/cooling/finished) that each do
                  something different on click, with zero indication that
                  a second "OFF" click, after a roast is already stopped,
                  does something else entirely (discards the finished view
                  and clears the form) rather than repeating the first. */}
              {phase === "finished" ? "RESET" : connected ? "OFF" : "ON"}
            </button>
            <button type="button" className="power-btn power-btn-start" onClick={onStart} disabled={!connected || recording}>
              START
            </button>
            {showEmergencyStop && !safetyDisabled && (
              <EmergencyStop
                roastId={roastId}
                disabled={!roasterIsOn || Boolean(trippedReason)}
                onStopped={(reason) => setControlStatus((s) => (s ? { ...s, tripped_reason: reason } : s))}
              />
            )}
          </div>

          <div className="digital-clock">{elapsedLabel}</div>
        </div>
      </div>
    </div>
  );
}
