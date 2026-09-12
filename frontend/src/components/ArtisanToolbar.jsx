// Mirrors Artisan desktop's top toolbar during a roast: milestone
// readouts (DRY%, time-to-dry, time-to-first-crack), the ON/OFF device
// toggle + START recording button, and a big digital elapsed-time clock.
export default function ArtisanToolbar({
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
            className={`power-btn ${connected ? "power-btn-off" : "power-btn-on"}`}
            onClick={onToggleConnect}
          >
            {connected ? "OFF" : "ON"}
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
