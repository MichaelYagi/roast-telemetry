// Showing the numbers from /api/analysis (see backend/app/roast_metrics.py):
// times as m:ss, temperatures in the chosen display unit, the rest as plain
// numbers. A metric here is one entry of GET /analysis/metrics.
import { celsiusDeltaToUnit, celsiusToUnit, unitSuffix } from "../tempUnits.js";

export function isTemperature(metric) {
  return metric.unit === "°C";
}

export function isRate(metric) {
  return metric.unit === "°C/min";
}

// A number in the metric's own unit, converted for the chosen temperature unit
// (a rate scales by 1.8 only, no +32 offset).
export function metricValue(metric, value, tempUnit) {
  if (value == null) return null;
  if (isTemperature(metric)) return celsiusToUnit(value, tempUnit);
  if (isRate(metric)) return tempUnit === "f" ? value * 1.8 : value;
  return value;
}

// Same, but for a *spread* between two readings (a standard deviation, a
// delta) rather than a single absolute reading -- a temperature metric's
// +32 offset cancels out when subtracting two Fahrenheit values, so a
// spread only ever scales by 9/5, same as a rate already does either way.
// Using metricValue (and its +32) on a spread is the bug this exists to
// avoid: a 7.8°C spread would come out as +46°F instead of +14°F.
export function metricSpreadValue(metric, value, tempUnit) {
  if (value == null) return null;
  if (isTemperature(metric)) return celsiusDeltaToUnit(value, tempUnit);
  if (isRate(metric)) return tempUnit === "f" ? value * 1.8 : value;
  return value;
}

export function metricUnitLabel(metric, tempUnit) {
  if (isTemperature(metric)) return unitSuffix(tempUnit);
  if (isRate(metric)) return `${unitSuffix(tempUnit)}/min`;
  if (metric.unit === "s") return "m:ss";
  return metric.unit;
}

export function formatSeconds(seconds) {
  if (seconds == null) return "—";
  const sign = seconds < 0 && Math.round(seconds) !== 0 ? "-" : "";
  const total = Math.round(Math.abs(seconds));
  return `${sign}${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

// Shared tail for formatMetric/formatMetricSpread: `shown` is already
// converted to the display unit (by metricValue or metricSpreadValue) --
// this just handles decimals and the unit suffix, identical either way.
function _formatShown(metric, shown, tempUnit, withUnit) {
  // Temperatures, rates and percentages always show one decimal so a column lines up.
  const fixed = isTemperature(metric) || isRate(metric) || metric.unit === "%";
  const text = fixed ? shown.toFixed(1) : Number.isInteger(shown) ? String(shown) : shown.toFixed(1);
  if (!withUnit) return text;
  if (isTemperature(metric)) return `${text}${unitSuffix(tempUnit)}`;
  if (isRate(metric)) return `${text}${unitSuffix(tempUnit)}/min`;
  if (metric.unit === "%") return `${text}%`;
  if (metric.unit === "g") return `${text} g`;
  if (metric.unit === "/5") return `${text}/5`;
  return metric.unit ? `${text} ${metric.unit}` : text;
}

export function formatMetric(metric, value, tempUnit, { withUnit = true } = {}) {
  if (value == null) return "—";
  if (metric.unit === "s") return formatSeconds(value);
  return _formatShown(metric, metricValue(metric, value, tempUnit), tempUnit, withUnit);
}

// Same as formatMetric, but for a spread (standard deviation, a delta)
// rather than a single absolute reading -- see metricSpreadValue.
export function formatMetricSpread(metric, value, tempUnit, { withUnit = true } = {}) {
  if (value == null) return "—";
  if (metric.unit === "s") return formatSeconds(value);
  return _formatShown(metric, metricSpreadValue(metric, value, tempUnit), tempUnit, withUnit);
}

// Groups the metric list for a <select> with <optgroup>s.
export function groupMetrics(metrics) {
  const groups = new Map();
  for (const m of metrics) {
    if (!groups.has(m.group)) groups.set(m.group, []);
    groups.get(m.group).push(m);
  }
  return [...groups.entries()];
}

// Mean and standard deviation of the numbers in `values` (nulls skipped).
export function meanAndSd(values) {
  const v = values.filter((x) => x != null);
  if (v.length === 0) return { n: 0, mean: null, sd: null };
  const mean = v.reduce((a, b) => a + b, 0) / v.length;
  const sd = v.length > 1 ? Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / (v.length - 1)) : null;
  return { n: v.length, mean, sd };
}
