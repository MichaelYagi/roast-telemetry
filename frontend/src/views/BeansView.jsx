import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client.js";
import { useConfirm } from "../components/DialogProvider.jsx";

const EMPTY = { name: "", origin: "", process: "", variety: "", altitude_m: "", density_g_l: "", moisture_pct: "", supplier: "", notes: "" };

const NUMBER_FIELDS = ["altitude_m", "density_g_l", "moisture_pct"];

function toForm(bean) {
  return Object.fromEntries(Object.keys(EMPTY).map((k) => [k, bean[k] ?? ""]));
}

function toPayload(form) {
  const out = {};
  for (const [k, v] of Object.entries(form)) {
    if (NUMBER_FIELDS.includes(k)) out[k] = v === "" ? null : Number(v);
    else out[k] = typeof v === "string" && v.trim() === "" ? null : typeof v === "string" ? v.trim() : v;
  }
  return out;
}

// Green-bean records. A roast can point at one (on the roast's own page or
// when configuring a roast), so roasts can be grouped and analysed by beans,
// origin, process and density.
export default function BeansView() {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const [beans, setBeans] = useState([]);
  const [editingId, setEditingId] = useState(null); // an id, "new", or null
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  function refresh() {
    return api
      .listBeans()
      .then(setBeans)
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refresh();
  }, []);

  function startNew() {
    setForm(EMPTY);
    setEditingId("new");
    setError(null);
  }

  function startEdit(bean) {
    setForm(toForm(bean));
    setEditingId(bean.id);
    setError(null);
  }

  async function save(e) {
    e.preventDefault();
    setError(null);
    try {
      if (editingId === "new") await api.createBean(toPayload(form));
      else await api.updateBean(editingId, toPayload(form));
      setEditingId(null);
      refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  async function remove(bean) {
    const ok = await confirm(t("beans.deleteConfirm", { name: bean.name, count: bean.roast_count }));
    if (!ok) return;
    await api.deleteBean(bean.id);
    if (editingId === bean.id) setEditingId(null);
    refresh();
  }

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  return (
    <div className="beans-view">
      <div className="panel">
        <div className="beans-header">
          <h2>{t("app.nav.beans")}</h2>
          <button type="button" onClick={startNew}>
            {t("beans.addBeans")}
          </button>
        </div>
        <p className="hint">{t("beans.hint")}</p>

        {editingId && (
          <form className="bean-form" onSubmit={save}>
            <label>
              {t("beans.form.name")}
              <input value={form.name} onChange={set("name")} required maxLength={200} autoFocus />
            </label>
            <label>
              {t("beans.form.origin")}
              <input value={form.origin} onChange={set("origin")} placeholder={t("beans.form.originPlaceholder")} />
            </label>
            <label>
              {t("beans.form.process")}
              <input value={form.process} onChange={set("process")} placeholder={t("beans.form.processPlaceholder")} />
            </label>
            <label>
              {t("beans.form.variety")}
              <input value={form.variety} onChange={set("variety")} />
            </label>
            <label>
              {t("beans.form.altitudeM")}
              <input type="number" min="0" value={form.altitude_m} onChange={set("altitude_m")} />
            </label>
            <label>
              {t("beans.form.densityGL")}
              <input type="number" min="0" step="0.1" value={form.density_g_l} onChange={set("density_g_l")} />
            </label>
            <label>
              {t("beans.form.moisturePct")}
              <input type="number" min="0" max="100" step="0.1" value={form.moisture_pct} onChange={set("moisture_pct")} />
            </label>
            <label>
              {t("beans.form.supplier")}
              <input value={form.supplier} onChange={set("supplier")} />
            </label>
            <label className="bean-notes">
              {t("beans.form.notes")}
              <textarea rows={2} value={form.notes} onChange={set("notes")} maxLength={2000} />
            </label>
            <div className="bean-form-actions">
              <button type="submit">{editingId === "new" ? t("beans.form.add") : t("beans.form.save")}</button>
              <button type="button" className="link-like" onClick={() => setEditingId(null)}>
                {t("beans.form.cancel")}
              </button>
              {error && <span className="error">{error}</span>}
            </div>
          </form>
        )}

        {loading ? (
          <p>{t("beans.loading")}</p>
        ) : beans.length === 0 ? (
          <p className="hint">{t("beans.noneYet")}</p>
        ) : (
          <table className="roast-table beans-table">
            <thead>
              <tr>
                <th>{t("beans.table.name")}</th>
                <th>{t("beans.table.origin")}</th>
                <th>{t("beans.table.process")}</th>
                <th>{t("beans.table.density")}</th>
                <th>{t("beans.table.moisture")}</th>
                <th>{t("beans.table.roasts")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {beans.map((b) => (
                <tr key={b.id}>
                  <td data-label={t("beans.table.name")}>
                    <strong>{b.name}</strong>
                    {b.variety && <span className="cell-sub">{b.variety}</span>}
                  </td>
                  <td data-label={t("beans.table.origin")}>{b.origin || "—"}</td>
                  <td data-label={t("beans.table.process")}>{b.process || "—"}</td>
                  <td data-label={t("beans.table.density")}>{b.density_g_l != null ? `${b.density_g_l} g/L` : "—"}</td>
                  <td data-label={t("beans.table.moisture")}>{b.moisture_pct != null ? `${b.moisture_pct}%` : "—"}</td>
                  <td data-label={t("beans.table.roasts")}>{b.roast_count}</td>
                  <td className="row-actions">
                    <button type="button" className="btn-sm" onClick={() => startEdit(b)}>
                      {t("beans.table.edit")}
                    </button>{" "}
                    <button type="button" className="btn-sm btn-danger-soft" onClick={() => remove(b)}>
                      {t("beans.table.delete")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
