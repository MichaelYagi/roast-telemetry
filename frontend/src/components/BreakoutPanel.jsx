import { BREAKOUT_PANEL_ITEMS } from "../breakoutPanels.js";

// Optional large-digit sidebar for the Live Roast screen -- purely
// additive (see Settings: "Big Readout Panel"), nothing here replaces
// the normal small displays elsewhere on the page. Renders nothing at
// all when no items are enabled, so it costs zero layout space by
// default -- see SettingsView.jsx / breakoutPanels.js for the toggles.
function computeValues(latest, milestones, elapsedLabel, roast) {
  const fmt = (v, digits = 1, suffix = "") => (v == null ? "—" : `${v.toFixed(digits)}${suffix}`);
  return {
    bt: fmt(latest?.bt, 1, "°"),
    et: fmt(latest?.et, 1, "°"),
    dt: fmt(latest?.dt, 1, "°"),
    ror_bt: fmt(latest?.ror_bt, 1),
    ror_et: fmt(latest?.ror_et, 1),
    time: elapsedLabel,
    dry_pct: milestones?.dryPercent ?? "—",
    maillard_pct: milestones?.maillardPercent ?? "—",
    dev_pct: milestones?.devPercent ?? "—",
    to_dry: milestones?.dryTime ?? "—",
    to_fcs: milestones?.fcsTime ?? "—",
    heater: fmt(latest?.heater_pct, 0, "%"),
    fan: fmt(latest?.fan_pct, 0, "%"),
    drum: fmt(latest?.drum_speed_pct, 0, "%"),
    playback_speed: roast?.playback_speed != null ? `${roast.playback_speed}x` : "—",
  };
}

// `enabledKeys` is an ordered array (from Settings' "Big Readout Panel" ▲▼
// reordering) -- render in that order, not BREAKOUT_PANEL_ITEMS' fixed
// declaration order.
export default function BreakoutPanel({ enabledKeys, latest, milestones, elapsedLabel, roast }) {
  const items = enabledKeys.map((key) => BREAKOUT_PANEL_ITEMS.find((item) => item.key === key)).filter(Boolean);
  if (items.length === 0) return null;

  const values = computeValues(latest, milestones, elapsedLabel, roast);

  return (
    <div className="breakout-panel">
      {items.map((item) => (
        <div key={item.key} className="breakout-box" style={{ borderColor: item.color }}>
          <span className="breakout-label">{item.label}</span>
          <span
            className="breakout-value"
            style={{ color: item.color, "--value-chars": String(values[item.key]).length }}
          >
            {values[item.key]}
          </span>
        </div>
      ))}
    </div>
  );
}
