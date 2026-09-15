// Mirrors Artisan desktop's top toolbar during a roast: milestone
// readouts (DRY%, time-to-dry, time-to-first-crack), the ON/OFF device
// toggle + START recording button, and a big digital elapsed-time clock.
export default function ArtisanToolbar({
  title,
  phase,
  elapsedLabel,
  statusText,
  milestones,
  onToggleConnect,
  onStart,
}) {
  const connected = phase !== "idle";
  const recording = phase === "roasting" || phase === "cooling" || phase === "finished";

  return (
    <div className="artisan-toolbar-wrap">
      {/* Title is a required field (see LiveRoastView.jsx's
          requireTitle()), so this is live -- what you're currently
          typing in Configure Roast's General tab, then whatever the
          roast was actually created with once connected. Sits above
          everything else in the toolbar, itself already the first thing
          on the page (see LiveRoastView's own !showSplitLayout &&
          toolbarElement / split-pane placement). */}
      <h2 className="artisan-toolbar-title">{title || "Untitled roast"}</h2>
      <div className="artisan-toolbar">
        <div className="artisan-toolbar-milestones">
          <div className="milestone-box">
            <span className="milestone-label">DRY%</span>
            <span className="milestone-value">{milestones.dryPercent}</span>
          </div>
          <div className="milestone-box">
            <span className="milestone-label">&raquo;DRY</span>
            <span className="milestone-value">{milestones.dryTime}</span>
          </div>
          <div className="milestone-box">
            <span className="milestone-label">&raquo;FCs</span>
            <span className="milestone-value">{milestones.fcsTime}</span>
          </div>
        </div>

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
      <div className="status-line">{statusText}</div>
    </div>
  );
}
