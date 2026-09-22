// Shared row definitions + formatting for a RoastStats object (see
// backend/app/models.py's RoastStats / GET /roasts/{id}/stats) -- one
// canonical list of {key, label} rows, and a formatter that looks up
// each row's value from a given roast's stats. Used by both
// RoastStatsPanel.jsx (one roast, one column of values) and
// ComparisonPanel.jsx's numeric table (one roast per column) --
// a fixed key list rather than just iterating `stats.phases` directly
// is what lets the table's rows line up correctly across roasts that
// have different phases present (e.g. one roast never got Dry End
// marked, so it only has a Development phase).
export const STAT_ROW_DEFS = [
  { key: "dry", label: "Dry %" },
  { key: "maillard", label: "Maillard %" },
  { key: "development", label: "Development % (DTR)" },
  { key: "weight_loss", label: "Weight loss" },
  { key: "duration", label: "Duration" },
  { key: "ror_flags", label: "RoR flags" },
];

export function formatDuration(seconds) {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function phasePct(stats, phaseName) {
  const phase = stats.phases.find((p) => p.phase === phaseName);
  return phase?.pct_of_roast != null ? `${phase.pct_of_roast}%` : "—";
}

function rorFlagsSummary(stats) {
  const { crashes, flatlines, flicks } = stats.ror_flags;
  const parts = [];
  if (crashes.length) parts.push(`${crashes.length} crash${crashes.length === 1 ? "" : "es"}`);
  if (flatlines.length) parts.push(`${flatlines.length} flatline${flatlines.length === 1 ? "" : "s"}`);
  if (flicks.length) parts.push(`${flicks.length} flick${flicks.length === 1 ? "" : "s"}`);
  return parts.length ? parts.join(", ") : "None";
}

export function formatRoastStatRow(key, stats) {
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
      return rorFlagsSummary(stats);
    default:
      return "—";
  }
}
