import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";

// What's known about the roast's beans, one line each (from the saved beans
// record: its notes, which hold the description an uploaded log came with, or
// else its filled-in details). Nothing shows when there's nothing beyond a name.
export default function BeansCard({ beanId, refreshKey = 0 }) {
  const { t } = useTranslation();
  const [bean, setBean] = useState(null);

  useEffect(() => {
    if (!beanId) {
      setBean(null);
      return;
    }
    let cancelled = false;
    api
      .listBeans()
      .then((list) => !cancelled && setBean(list.find((b) => b.id === beanId) || null))
      .catch(() => !cancelled && setBean(null));
    return () => {
      cancelled = true;
    };
  }, [beanId, refreshKey]);

  if (!bean) return null;

  const details = [
    [t("common.beansCard.origin"), bean.origin],
    [t("common.beansCard.process"), bean.process],
    [t("common.beansCard.variety"), bean.variety],
    [t("common.beansCard.altitude"), bean.altitude_m != null ? `${bean.altitude_m} m` : null],
    [t("common.beansCard.density"), bean.density_g_l != null ? `${bean.density_g_l} g/L` : null],
    [t("common.beansCard.moisture"), bean.moisture_pct != null ? `${bean.moisture_pct}%` : null],
    [t("common.beansCard.supplier"), bean.supplier],
  ].filter(([, value]) => value);
  const text = bean.notes || (details.length ? [bean.name, ...details.map(([k, v]) => `${k}: ${v}`)].join("\n") : "");
  if (!text) return null;

  return (
    <div className="panel">
      <h3>{t("app.nav.beans")}</h3>
      <div className="beans-card-text">{text}</div>
      <p className="hint no-print">
        <Link to="/beans">{t("common.beansCard.editOnBeansPage")}</Link>
      </p>
    </div>
  );
}
