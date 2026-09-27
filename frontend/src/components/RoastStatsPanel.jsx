import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client.js";
import { getStatRowDefs, formatRoastStatRow } from "../roastStats.js";

// Single-roast derived stats -- Dry/Maillard/Development %, weight
// loss%, duration, RoR crash/flatline/flick flags. Pure display, no
// editing (unlike WeightField/tags) -- everything here is computed
// server-side from the roast's own profile/events (GET /roasts/{id}/stats,
// see backend/app/roast_stats.py), nothing the user sets directly.
export default function RoastStatsPanel({ roastId }) {
  const { t } = useTranslation();
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setStats(null);
    setError(null);
    api
      .getRoastStats(roastId)
      .then(setStats)
      .catch((err) => setError(err.message));
  }, [roastId]);

  if (error) return <p className="error">{error}</p>;
  if (!stats) return <p>{t("common.roastStats.loading")}</p>;

  return (
    <ul className="kv-list">
      {getStatRowDefs(t).map(({ key, label }) => (
        <li key={key}>
          <span>{label}</span>
          <span>{formatRoastStatRow(key, stats, t)}</span>
        </li>
      ))}
    </ul>
  );
}
