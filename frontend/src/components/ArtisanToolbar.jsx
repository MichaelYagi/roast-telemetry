// Mirrors Artisan desktop's top toolbar during a roast: the ON/OFF
// device toggle + START recording button, and a big digital
// elapsed-time clock. Used to also carry a fixed DRY%/»DRY/»FCs
// milestone-box row, but those are just as available (and freely
// choosable alongside DEV%/DEV TIME/etc.) via the Big/Small Readout
// Panel now, so hardcoding this one fixed trio here was redundant.
// connectionStatus: "checking" | "pass" | "fail" | null (null/undefined
// -- no live-hardware connection worth showing a dot for, e.g.
// simulator/alog_playback, or idle/finished). "checking" is yellow --
// shown right after ON, before enough readings have come in to call it
// either way -- turning green once they look normal, or red if they
// don't. See useConnectionHealth.js for how this gets computed.
export default function ArtisanToolbar({
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
}) {
  const connected = phase !== "idle";
  const recording = phase === "roasting" || phase === "cooling" || phase === "finished";
  // Beans/weight are both optional (Configure Roast never requires
  // either) -- only append what's actually there, and only the " · "
  // separator between them when both are, rather than a dangling one.
  const meta = [beans, weightGreenG != null ? `${weightGreenG} g` : null].filter(Boolean).join(" · ");

  return (
    <div className="artisan-toolbar-wrap">
      <div className="artisan-toolbar">
        {/* Title is a required field (see LiveRoastView.jsx's
            requireTitle()), so this is live -- what you're currently
            typing in Configure Roast's General tab, then whatever the
            roast was actually created with once connected. The status
            line sits directly under it, not as its own separate row --
            both are on the left, controls/clock on the right, same row. */}
        <div className="artisan-toolbar-title-group">
          <h2 className="artisan-toolbar-title">
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
            {meta && <span className="artisan-toolbar-meta"> · {meta}</span>}
          </h2>
          <div className="status-line">{statusText}</div>
        </div>

        <div className="artisan-toolbar-controls">
          <div className="artisan-toolbar-power">
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
          </div>

          <div className="digital-clock">{elapsedLabel}</div>
        </div>
      </div>
    </div>
  );
}
