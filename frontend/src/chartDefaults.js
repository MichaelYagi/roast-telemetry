// Default look of the roast chart's axes and curves -- chosen to read like the
// roasting charts people already know: a temperature axis from 0 to 275 °C, a
// rate-of-rise axis from 0 to 25 °/min, and a time axis that starts a little
// before Charge and shows the first ten minutes.

// Rates convert by scale only (no +32), temperatures by the full formula.
export const RATE_SCALE_F = 1.8;

// The top of the temperature axis: 350 C or 527 F (the usual roasting-chart
// defaults) unless the data gets close to it, then the next 50 up. An exact
// number rather than a "nice" tick (Chart.js rounds a suggested max up to the
// next tick, which turned 527 F into 600).
export function tempAxisMax(unit, dataMax) {
  const floor = unit === "f" ? 527 : 350;
  if (dataMax == null || !Number.isFinite(dataMax) || dataMax <= floor - 15) return floor;
  return Math.max(floor, Math.ceil((dataMax + 10) / 50) * 50);
}

// 0 in both units: the control lines (Burner/Air/Drum/Damper, 0-100) share this
// axis and sit in its low band, so it has to start at 0 -- not at 32 F.
export function tempAxisMin() {
  return 0;
}

// Fixed, like the other roasting charts' default. A brief spike (RoR is huge
// for a moment right after Charge) runs off the top instead of stretching the
// axis and flattening the rest of the roast; the fall right after Charge
// (below zero) isn't drawn either.
export function rorAxisRange(unit) {
  return unit === "f" ? { min: 0, max: 45 } : { min: 0, max: 25 };
}

// Seconds. While a roast is being recorded the axis starts 30 s before Charge and
// shows ten minutes, growing to fit a longer roast. A finished or loaded roast
// instead fills the chart: from just before Charge to the end of the recording.
export const TIME_AXIS = { min: -30, suggestedMax: 600 };

export function timeAxisFor(finished, durationS) {
  if (finished && durationS != null && durationS > 0) {
    const lead = Math.max(3, durationS * 0.05);
    return { min: -lead, max: durationS * 1.01 };
  }
  return TIME_AXIS;
}

// m:ss, with a leading minus before Charge (-0:30).
export function formatTime(seconds) {
  const total = Math.round(Math.abs(seconds));
  const sign = seconds < 0 && total > 0 ? "-" : "";
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
