import { BREAKOUT_PANEL_ITEMS } from "../breakoutPanels.js";
import { formatTemp } from "../tempUnits.js";
import { TERM_TOOLTIPS } from "../termTooltips.js";

// Optional large-digit sidebar for the Live Roast screen -- purely
// additive (see Settings: "Big Readout Panel"), nothing here replaces
// the normal small displays elsewhere on the page. Renders nothing at
// all when no items are enabled, so it costs zero layout space by
// default -- see SettingsView.jsx / breakoutPanels.js for the toggles.
function computeValues(latest, milestones, elapsedLabel, roast, tempUnit) {
  const fmt = (v, digits = 1, suffix = "") => (v == null ? "—" : `${v.toFixed(digits)}${suffix}`);
  const temp = (v) => formatTemp(v, tempUnit) ?? "—";
  return {
    bt: temp(latest?.bt),
    et: temp(latest?.et),
    dt: temp(latest?.dt),
    ror_bt: fmt(latest?.ror_bt, 1),
    ror_et: fmt(latest?.ror_et, 1),
    time: elapsedLabel,
    dry_pct: milestones?.dryPercent ?? "—",
    maillard_pct: milestones?.maillardPercent ?? "—",
    dev_pct: milestones?.devPercent ?? "—",
    to_dry: milestones?.dryTime ?? "—",
    to_fcs: milestones?.fcsTime ?? "—",
    to_dev: milestones?.devTime ?? "—",
    heater: fmt(latest?.heater_pct, 0, "%"),
    burner_sv: temp(latest?.burner_sv_c),
    fan: fmt(latest?.fan_pct, 0, " RPM"),
    drum: fmt(latest?.drum_speed_pct, 0, " RPM"),
    playback_speed: roast?.playback_speed != null ? `${roast.playback_speed}x` : "—",
  };
}

// `enabledKeys` is an ordered array (from Settings' "Big Readout Panel" ▲▼
// reordering) -- render in that order, not BREAKOUT_PANEL_ITEMS' fixed
// declaration order.
export default function BreakoutPanel({ enabledKeys, latest, milestones, elapsedLabel, roast, colorOverrides, tempUnit = "c" }) {
  const items = enabledKeys.map((key) => BREAKOUT_PANEL_ITEMS.find((item) => item.key === key)).filter(Boolean);
  if (items.length === 0) return null;

  const values = computeValues(latest, milestones, elapsedLabel, roast, tempUnit);

  return (
    <div className="breakout-panel">
      {items.map((item) => {
        const color = colorOverrides?.[item.key] || item.color;
        return (
          // --item-color, not just borderColor: the Big Readout Panel's
          // own look (white box, colored border+text) only needs
          // borderColor/color inline -- the Small Readout column wants
          // the *same* color value presented differently (a solid filled
          // background instead), which a scoped stylesheet rule can only
          // pull off by reading a custom property, not an inline style
          // meant for something else.
          <div key={item.key} className="breakout-box" style={{ borderColor: color, "--item-color": color }}>
            <span className="breakout-label" title={TERM_TOOLTIPS[item.label]}>{item.label}</span>
            <span className="breakout-value" style={{ color, "--value-chars": String(values[item.key]).length }}>
              {values[item.key]}
            </span>
          </div>
        );
      })}
    </div>
  );
}
