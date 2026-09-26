// Shared between VerticalControlPanel (live render) and
// VerticalControlSettingsEditor (Settings page) -- mirrors backend/app/
// models.py's VERTICAL_CONTROL_KEYS exactly, same key strings on both
// sides. drum_speed_pct/fan_pct are always shown regardless of settings
// (see VerticalControlPanel's own comment); heater_pct/burner_sv_c are the
// two configurable ones.
// drum_speed_pct/fan_pct are real RPM values on the FZ-94 (confirmed
// against a live unit), not percentages, despite the "_pct" field names
// -- their 0-100/0-70 range is just this machine's actual RPM range.
export const VERTICAL_CONTROL_ITEMS = [
  { key: "drum_speed_pct", label: "Drum", unit: " RPM", color: "#16a34a", mandatory: true },
  { key: "fan_pct", label: "Air", unit: " RPM", color: "#0891b2", mandatory: true },
  { key: "heater_pct", label: "Burner", unit: "%", color: "#f59e0b", mandatory: false },
  { key: "burner_sv_c", label: "SV", unit: "°C", color: "#92400e", mandatory: false },
];

export const VERTICAL_CONTROL_KEYS = VERTICAL_CONTROL_ITEMS.map((i) => i.key);
export const MANDATORY_VERTICAL_CONTROL_KEYS = VERTICAL_CONTROL_ITEMS.filter((i) => i.mandatory).map((i) => i.key);
// Configurable channels -- at least one of these must always stay visible
// (enforced by the settings editor UI; the renderer falls back to
// heater_pct if it's ever handed a layout where neither is present).
export const OPTIONAL_VERTICAL_CONTROL_KEYS = VERTICAL_CONTROL_ITEMS.filter((i) => !i.mandatory).map((i) => i.key);

export function verticalControlItem(key) {
  return VERTICAL_CONTROL_ITEMS.find((i) => i.key === key);
}

// Flattens the settings' ordered-groups shape (string[][], a group with 1
// key = siloed/full-height, 2+ = a shared lane split into stacked top/
// bottom (and so on) segments -- see AppSettings.vertical_control_layout's
// own docstring) into a single ordered list with
// a per-item `joinsPrevious` flag, and back again -- the editor works
// entirely in this flat shape (one linear reorderable list + a "stack with
// the one above" toggle per row) since a full 2D drag-and-drop grouping UI
// is a lot more machinery for the same expressiveness.
export function groupsToFlat(groups) {
  const flat = [];
  for (const group of groups) {
    group.forEach((key, i) => flat.push({ key, joinsPrevious: i > 0 }));
  }
  return flat;
}

export function flatToGroups(flat) {
  const groups = [];
  for (const item of flat) {
    if (item.joinsPrevious && groups.length > 0) {
      groups[groups.length - 1].push(item.key);
    } else {
      groups.push([item.key]);
    }
  }
  return groups;
}

// Guarantees the two safety invariants regardless of what's actually saved
// (a stale/hand-edited settings row, or simply the SV channel not being
// supported by the currently-connected mode) -- appends whatever's
// missing as its own new siloed group, at the end, rather than ever
// rendering with no way to touch Drum/Fan/the burner at all. Both
// VerticalControlPanel (render time, also given `svAvailable`) and the
// settings editor (edit time, always both optional keys "available") use
// this, so the two never disagree about what a given layout actually means.
//
// Deliberately falls back to heater_pct -- reappearing even if Settings
// configured only burner_sv_c -- whenever the connected mode/device has
// no SV range: simulator never has one at all, so a "Burner SV only"
// layout used to leave a simulator roast with *no* burner control
// whatsoever (confirmed live: Drum/Air only, no way to touch the heat
// mid-roast). A working control that isn't exactly what Settings asked
// for beats no control at all -- this only matters on a connection that
// genuinely can't offer SV; a mode/device that does (e.g. the FZ-94
// profile) still shows SV exactly as configured, Burner % staying hidden.
export function normalizeLayout(rawGroups, { svAvailable = true } = {}) {
  const seen = new Set();
  const groups = [];
  for (const group of rawGroups || []) {
    const filtered = group.filter((k) => {
      if (!VERTICAL_CONTROL_KEYS.includes(k)) return false;
      if (k === "burner_sv_c" && !svAvailable) return false;
      if (seen.has(k)) return false;
      return true;
    });
    filtered.forEach((k) => seen.add(k));
    if (filtered.length > 0) groups.push(filtered);
  }
  for (const key of MANDATORY_VERTICAL_CONTROL_KEYS) {
    if (!seen.has(key)) {
      groups.push([key]);
      seen.add(key);
    }
  }
  const hasOptional = OPTIONAL_VERTICAL_CONTROL_KEYS.some((k) => seen.has(k));
  if (!hasOptional) {
    groups.push(["heater_pct"]);
  }
  return groups;
}
