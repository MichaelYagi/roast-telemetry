// Default look of the roast chart's axes and curves -- chosen to read like the
// roasting charts people already know: a temperature axis from 0 to 275 °C, a
// rate-of-rise axis from 0 to 25 °/min, and a time axis that starts a little
// before Charge and shows the first ten minutes.

// Rates convert by scale only (no +32), temperatures by the full formula.
export const RATE_SCALE_F = 1.8;

// Never lower than this, so the curve sits where people expect; a hotter roast
// simply grows the axis (suggestedMax) instead of running off the top.
export function tempAxisSuggestedMax(unit) {
  return unit === "f" ? 530 : 275;
}

export function tempAxisMin(unit) {
  return unit === "f" ? 32 : 0;
}

// Fixed, like the other roasting charts' default. A brief spike (RoR is huge
// for a moment right after Charge) runs off the top instead of stretching the
// axis and flattening the rest of the roast; the fall right after Charge
// (below zero) isn't drawn either.
export function rorAxisRange(unit) {
  return unit === "f" ? { min: 0, max: 45 } : { min: 0, max: 25 };
}

// Seconds. Starts 30 s before Charge; ten minutes by default, growing to fit a longer roast.
export const TIME_AXIS = { min: -30, suggestedMax: 600 };

// m:ss, with a leading minus before Charge (-0:30).
export function formatTime(seconds) {
  const sign = seconds < 0 ? "-" : "";
  const total = Math.round(Math.abs(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${sign}${m}:${s.toString().padStart(2, "0")}`;
}

// Line widths: BT a little heavier than ET, rates thin.
export function seriesLineWidth(key, axis) {
  if (axis === "yRor") return 1;
  if (key === "ET") return 2;
  if (axis === "yTemp") return 2.5;
  return 1.5;
}
