// Shared between SettingsView (the toggle checkboxes) and BreakoutPanel
// (what actually renders) -- mirrors backend/app/models.py's
// BREAKOUT_PANEL_KEYS exactly, same key strings on both sides.
export const BREAKOUT_PANEL_ITEMS = [
  { key: "bt", label: "BT", color: "#1d4ed8" },
  { key: "et", label: "ET", color: "#be123c" },
  { key: "dt", label: "DT", color: "#c2410c" },
  { key: "ror_bt", label: "ΔBT (RoR)", color: "#8b5cf6" },
  { key: "ror_et", label: "RoR (ET)", color: "#c4b5fd" },
  { key: "time", label: "Elapsed time", color: "#334155" },
  { key: "dry_pct", label: "DRY%", color: "#0891b2" },
  { key: "maillard_pct", label: "Maillard%", color: "#d97706" },
  { key: "dev_pct", label: "DEV%", color: "#dc2626" },
  { key: "to_dry", label: "»DRY", color: "#0891b2" },
  { key: "to_fcs", label: "»FCs", color: "#0891b2" },
  { key: "to_dev", label: "DEV TIME", color: "#dc2626" },
  { key: "heater", label: "Burner %", color: "#f59e0b" },
  { key: "burner_sv", label: "SV", color: "#92400e" },
  { key: "fan", label: "Air %", color: "#0891b2" },
  { key: "drum", label: "Drum %", color: "#16a34a" },
  { key: "playback_speed", label: "Playback speed", color: "#334155" },
];

// "Elapsed time" is redundant right beside the chart, which already has
// its own elapsed-time x-axis -- excluded only from the Small Readout
// column (both its Settings editor and its live render), not the Big
// Readout Panel, which isn't tied to sitting next to the chart the same way.
export const SMALL_READOUT_EXCLUDED_KEYS = ["time"];
