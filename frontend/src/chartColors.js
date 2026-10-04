import { BREAKOUT_PANEL_ITEMS } from "./breakoutPanels.js";

// Every color on the roast chart that isn't one of the readout items in
// breakoutPanels.js, with its default. All of them are saved in the same
// settings map (breakout_panel_colors) under the keys below -- mirrors
// backend/app/models.py's CHART_COLOR_KEYS/is_color_key -- and edited in
// Settings > Colors (ColorsSettingsEditor.jsx).

// Lines that have no readout item of their own.
export const OTHER_LINE_ITEMS = [
  { key: "damper", label: "Damper", color: "#7c3aed" },
  { key: "bg_bt", label: "Background BT", color: "#93c5fd" },
  { key: "bg_et", label: "Background ET", color: "#fda4af" },
];

// Milestone markers ("event:<TYPE>").
export const EVENT_COLORS = {
  CHARGE: "#2563eb",
  TURNING_POINT: "#0891b2",
  DRY_END: "#ca8a04",
  FC_START: "#dc2626",
  FC_END: "#b91c1c",
  SC_START: "#9333ea",
  SC_END: "#7e22ce",
  DROP: "#16a34a",
  COOL_END: "#334155",
  CUSTOM: "#64748b",
};
const EVENT_LABELS = {
  CHARGE: "Charge",
  TURNING_POINT: "Turning Point",
  DRY_END: "Dry End",
  FC_START: "FC Start",
  FC_END: "FC End",
  SC_START: "SC Start",
  SC_END: "SC End",
  DROP: "Drop",
  COOL_END: "Cool End",
  CUSTOM: "Other events",
};
export const EVENT_ITEMS = Object.keys(EVENT_COLORS).map((type) => ({
  key: `event:${type}`,
  label: EVENT_LABELS[type],
  color: EVENT_COLORS[type],
}));

// Phase bands ("phase:<key>").
export const PHASE_COLORS = { dry: "#6ee7b7", maillard: "#fde68a", dev: "#fca5a5" };
export const PHASE_ITEMS = [
  { key: "phase:dry", label: "Dry", color: PHASE_COLORS.dry },
  { key: "phase:maillard", label: "Maillard", color: PHASE_COLORS.maillard },
  { key: "phase:dev", label: "Dev", color: PHASE_COLORS.dev },
];

// Extra channels ("extra:<label>") -- a roast's own "Drum Heat", "Fan
// Speed", ... With no color saved, one that has the same name as a readout
// item (an imported log's "SV" is the same thing as the SV readout) follows
// that item's color, so the two always match; any other gets the next color
// from this list.
export const EXTRA_CHANNEL_COLORS = ["#0d9488", "#b45309", "#7c3aed", "#be185d"];
const READOUT_KEY_BY_EXTRA_LABEL = { sv: "burner_sv", dt: "dt", bt: "bt", et: "et" };
const READOUT_DEFAULTS = Object.fromEntries(BREAKOUT_PANEL_ITEMS.map((i) => [i.key, i.color]));

export function extraChannelDefaultColor(colors, label, index) {
  const readoutKey = READOUT_KEY_BY_EXTRA_LABEL[String(label).trim().toLowerCase()];
  if (readoutKey) return colors?.[readoutKey] || READOUT_DEFAULTS[readoutKey];
  return EXTRA_CHANNEL_COLORS[index % EXTRA_CHANNEL_COLORS.length];
}

export function extraChannelColor(colors, label, index) {
  return colors?.[`extra:${label}`] || extraChannelDefaultColor(colors, label, index);
}

export function eventColor(colors, type) {
  return colors?.[`event:${type}`] || EVENT_COLORS[type] || colors?.["event:CUSTOM"] || EVENT_COLORS.CUSTOM;
}

export function phaseColor(colors, key) {
  return colors?.[`phase:${key}`] || PHASE_COLORS[key];
}
