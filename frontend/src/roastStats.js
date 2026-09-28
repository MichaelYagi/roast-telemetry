import { formatSeconds } from "./lib/metricFormat.js";

// Shared row definitions + formatting for a RoastStats object (see
// backend/app/models.py's RoastStats / GET /roasts/{id}/stats) -- one
// canonical list of {key, label} rows, and a formatter that looks up
// each row's value from a given roast's stats. Used by
// RoastStatsPanel.jsx (one roast, one column of values) -- a fixed key
// list rather than just iterating `stats.phases` directly is what lets
// rows line up correctly across roasts that have different phases
// present (e.g. one roast never got Dry End marked, so it only has a
// Development phase).
//
// A function, not a static array/object -- both take `t` (from the
// caller's own useTranslation(), this file itself isn't a component and
// can't call hooks) and are called fresh on every render, so a language
// switch is reflected immediately instead of freezing whatever was
// active the first time this module loaded.
export function getStatRowDefs(t) {
  return [
    { key: "dry", label: t("common.roastStats.rows.dry") },
    { key: "maillard", label: t("common.roastStats.rows.maillard") },
    { key: "development", label: t("common.roastStats.rows.development") },
    { key: "weight_loss", label: t("common.roastStats.rows.weightLoss") },
    { key: "duration", label: t("common.roastStats.rows.duration") },
    { key: "ror_flags", label: t("common.roastStats.rows.rorFlags") },
  ];
}

// Re-exported under this file's own established name (widely imported as
// formatDuration) -- the actual m:ss logic is lib/metricFormat.js's
// formatSeconds, the one correct implementation; rounding minutes and
// seconds separately (this file's old inline version) breaks on negative
// and near-whole-minute values -- see that module for the details.
export const formatDuration = formatSeconds;

function phasePct(stats, phaseName) {
  const phase = stats.phases.find((p) => p.phase === phaseName);
  return phase?.pct_of_roast != null ? `${phase.pct_of_roast}%` : "—";
}

function rorFlagsSummary(stats, t) {
  const { crashes, flatlines, flicks } = stats.ror_flags;
  const parts = [];
  if (crashes.length) parts.push(t("common.roastStats.crashes", { count: crashes.length }));
  if (flatlines.length) parts.push(t("common.roastStats.flatlines", { count: flatlines.length }));
  if (flicks.length) parts.push(t("common.roastStats.flicks", { count: flicks.length }));
  return parts.length ? parts.join(", ") : t("common.roastStats.none");
}

export function formatRoastStatRow(key, stats, t) {
  if (!stats) return "—";
  switch (key) {
    case "dry":
      return phasePct(stats, "Dry");
    case "maillard":
      return phasePct(stats, "Maillard");
    case "development":
      return stats.dtr_pct != null ? `${stats.dtr_pct}%` : "—";
    case "weight_loss":
      return stats.weight_loss_pct != null ? `${stats.weight_loss_pct}%` : "—";
    case "duration":
      return formatDuration(stats.duration_s);
    case "ror_flags":
      return rorFlagsSummary(stats, t);
    default:
      return "—";
  }
}
