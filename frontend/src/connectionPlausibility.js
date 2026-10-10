// Used by ConnectionTestPanel.jsx's manual, on-demand "Run read-only test".

// Every channel worth checking on a full modbus_live connection -- ms6514
// only ever has bt/et, tc4_live only has bt/et/dt (see channelsForMode
// below), everything else (Burner SV, Fan/Drum) is FZ-94/Modbus-specific
// and doesn't exist on those other devices at all. Ranges are deliberately
// generous (this is a sanity check against "reading garbage/nothing," not
// a real calibration check) -- a real BT/ET/DT for a coffee roaster, hot
// or cold, comfortably fits 0-300C; Burner SV is a configurable setpoint
// range but 0-400C safely bounds any sane configuration; Fan/Drum are
// genuinely a 0-100.00% share of the VFD drives' max frequency (register
// 8193, 0-10000 raw -- see docs/modbus/fz-94-usb.html), matching the
// fan_pct/drum_speed_pct field names -- Drum's real *operating* range is
// capped at 0-70 for safety, still the same 0-100% scale, not a
// different unit.
export const READ_CHANNELS = [
  { key: "bt", label: "BT", unit: "°", min: -10, max: 300 },
  { key: "et", label: "ET", unit: "°", min: -10, max: 300 },
  { key: "dt", label: "DT", unit: "°", min: -10, max: 300 },
  { key: "burner_sv_c", label: "Burner SV", unit: "°", min: 0, max: 400 },
  { key: "fan_pct", label: "Fan", unit: "%", min: 0, max: 100 },
  { key: "drum_speed_pct", label: "Drum", unit: "%", min: 0, max: 100 },
];

export function channelsForMode(mode) {
  if (mode === "ms6514_live") return READ_CHANNELS.filter((c) => ["bt", "et"].includes(c.key));
  // tc4_live: no Burner SV register, and OT1/DCFAN are write-only -- there's
  // never anything to read back for fan_pct/drum_speed_pct (TC4 has no drum
  // channel at all), so including them here would always show a misleading
  // "no data" for reasons that have nothing to do with the connection
  // actually working.
  if (mode === "tc4_live") return READ_CHANNELS.filter((c) => ["bt", "et", "dt"].includes(c.key));
  // aillio_live: no Burner SV concept at all (always null -- see
  // aillio_bridge/engine.py's own comment). Fan/Drum are also a 0-100%
  // scale here (a small device-native range, not the FZ-94's VFD
  // frequency register, but the same unit either way), so those two
  // reuse READ_CHANNELS' own entries unchanged. Genuinely polled device
  // feedback (see AillioEngine.tick()'s _poll()), not write-only like
  // TC4 -- these get real "pass"/"fail" plausibility checks.
  if (mode === "aillio_live") return READ_CHANNELS.filter((c) => ["bt", "et", "dt", "fan_pct", "drum_speed_pct"].includes(c.key));
  // plugin_live: an arbitrary third-party plugin (see device_plugins/README.md)
  // -- bt/et are the only channels device_plugins.DeviceEngine's contract
  // guarantees every plugin reports something for (even if just null), so
  // these are the only ones checked here, same conservative reasoning as
  // ms6514_live above. A plugin that also has dt/controls has no way to say
  // so yet -- this only ever under-includes, never shows a misleading failure.
  if (mode === "plugin_live") return READ_CHANNELS.filter((c) => ["bt", "et"].includes(c.key));
  return READ_CHANNELS;
}

// unitSuffix/celsiusToUnit come from ../tempUnits.js -- passed in rather
// than imported here so this stays a pure function of its inputs, easy to
// call from either component without worrying about import cycles.
export function analyzeReadSamples(samples, channels, tempUnit, { celsiusToUnit, unitSuffix }) {
  return channels.map((ch) => {
    const isTemp = ch.unit === "°";
    // Plausibility bounds (ch.min/ch.max) always stay Celsius -- they're
    // sanity-check thresholds, not something a display preference should
    // touch. Only what gets shown to the user converts.
    const display = (v) => (isTemp ? celsiusToUnit(v, tempUnit) : v);
    const unit = isTemp ? unitSuffix(tempUnit) : ch.unit;
    const values = samples.map((s) => s?.[ch.key]).filter((v) => v != null);
    if (values.length === 0) {
      // Not necessarily a failure -- e.g. DT is off by default on most
      // FZ-94 units, so "never populated" is the *expected* shape there,
      // not a fault. Surfaced as a neutral warning either way, not a
      // red fail, since this function has no way to know which case it is.
      return { ...ch, status: "warn", detail: "no data (not configured, or not reading)" };
    }
    const min = Math.min(...values);
    // No artificial cap here -- this feeds the "out of range" detail text
    // below, which exists specifically to show *how far* out of range a
    // bad reading actually is. A hardcoded ceiling (this used to be
    // `Math.min(300, ...)`, understating anything above 300 even for
    // Burner SV's real 400 ceiling) defeats that purpose by hiding the
    // true severity of the out-of-range value.
    const max = Math.max(...values);
    const inRange = values.every((v) => v >= ch.min && v <= ch.max);
    if (!inRange) {
      return { ...ch, status: "fail", detail: `out of range (${display(min).toFixed(1)}-${display(max).toFixed(1)})` };
    }
    const varies = max - min > 0.001;
    const last = values[values.length - 1];
    return {
      ...ch,
      status: "pass",
      detail: `${display(last).toFixed(1)}${unit}${varies ? "" : " (steady -- fine if the roaster is idle)"}`,
    };
  });
}
