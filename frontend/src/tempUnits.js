// Display-only Celsius/Fahrenheit conversion -- mirrors Artisan's own
// Config > Temperature > Fahrenheit/Celsius Mode toggle. Every stored
// value and every config input field (thresholds, SV ranges, alarm rule
// temperatures) stays Celsius always; this only converts what's shown
// for *live readings* (readouts, chart, event history). See
// backend/app/models.py's AppSettings.temperature_unit for the full
// rationale on why input fields are deliberately excluded.

export function celsiusToUnit(celsius, unit) {
  if (celsius == null) return null;
  return unit === "f" ? (celsius * 9) / 5 + 32 : celsius;
}

export function unitSuffix(unit) {
  return unit === "f" ? "°F" : "°C";
}

// Formats a Celsius value in the given display unit, e.g. "182.8°F".
export function formatTemp(celsius, unit, digits = 1) {
  if (celsius == null) return null;
  return `${celsiusToUnit(celsius, unit).toFixed(digits)}${unitSuffix(unit)}`;
}
