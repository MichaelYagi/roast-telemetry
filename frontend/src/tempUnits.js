// Display-only Celsius/Fahrenheit conversion -- the
// Fahrenheit/Celsius display toggle. Every stored
// value and every config input field (thresholds, SV ranges, alarm rule
// temperatures) stays Celsius always; this only converts what's shown
// for *live readings* (readouts, chart, event history). See
// backend/app/models.py's AppSettings.temperature_unit for the full
// rationale on why input fields are deliberately excluded.

export function celsiusToUnit(celsius, unit) {
  if (celsius == null) return null;
  return unit === "f" ? (celsius * 9) / 5 + 32 : celsius;
}

// Inverse of celsiusToUnit -- needed wherever a live *control* (not just a
// readout) is displayed/dragged in the selected unit but must still write
// Celsius underneath (e.g. VerticalControlPanel's burner_sv_c slider).
export function unitToCelsius(value, unit) {
  if (value == null) return null;
  return unit === "f" ? ((value - 32) * 5) / 9 : value;
}

export function unitSuffix(unit) {
  return unit === "f" ? "°F" : "°C";
}

// Formats a Celsius value in the given display unit, e.g. "182.8°F".
export function formatTemp(celsius, unit, digits = 1) {
  if (celsius == null) return null;
  return `${celsiusToUnit(celsius, unit).toFixed(digits)}${unitSuffix(unit)}`;
}
