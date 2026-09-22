import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";

// What's known about the roast's beans, one line each (from the saved beans
// record: its notes, which hold the description an uploaded log came with, or
// else its filled-in details). Nothing shows when there's nothing beyond a name.
export default function BeansCard({ beanId, refreshKey = 0 }) {
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
    ["Origin", bean.origin],
    ["Process", bean.process],
    ["Variety", bean.variety],
    ["Altitude", bean.altitude_m != null ? `${bean.altitude_m} m` : null],
    ["Density", bean.density_g_l != null ? `${bean.density_g_l} g/L` : null],
    ["Moisture", bean.moisture_pct != null ? `${bean.moisture_pct}%` : null],
    ["Supplier", bean.supplier],
  ].filter(([, value]) => value);
  const text = bean.notes || (details.length ? [bean.name, ...details.map(([k, v]) => `${k}: ${v}`)].join("\n") : "");
  if (!text) return null;

  return (
    <div className="panel">
      <h3>Beans</h3>
      <div className="beans-card-text">{text}</div>
      <p className="hint no-print">
        <Link to="/beans">Edit on the Beans page</Link>
      </p>
    </div>
  );
}
