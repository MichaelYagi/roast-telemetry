import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client.js";

const same = (a, b) => (a || "").trim().toLowerCase() === (b || "").trim().toLowerCase();

// Saved views, as one field: pick a saved view from the suggestions to load it,
// or type a name and Save to keep the page's current settings under it. Saving
// under a name that already exists updates that view.
// `getConfig()` returns what to save; `onLoad(config)` applies a saved one.
export default function SavedViews({ kind, getConfig, onLoad, loadOnly = false, placeholder }) {
  const { t } = useTranslation();
  const listId = useId();
  const [views, setViews] = useState([]);
  const [name, setName] = useState("");
  const [message, setMessage] = useState(null);
  const [error, setError] = useState(null);

  function refresh() {
    return api.listViews(kind).then(setViews).catch(() => {});
  }

  useEffect(() => {
    refresh();
  }, [kind]); // eslint-disable-line react-hooks/exhaustive-deps

  const match = views.find((v) => same(v.name, name));

  function change(e) {
    const next = e.target.value;
    setName(next);
    setError(null);
    // Picking a suggestion sets the exact saved name: that means "load it".
    const exact = views.find((v) => v.name === next);
    if (exact) {
      onLoad(exact.config);
      setMessage(t("common.savedViews.loaded"));
    } else {
      setMessage(null);
    }
  }

  async function save() {
    if (!name.trim()) return;
    setError(null);
    try {
      const saved = await api.saveView(kind, name.trim(), getConfig());
      await refresh();
      setName(saved.name);
      setMessage(match ? t("common.savedViews.updated") : t("common.savedViews.saved"));
    } catch (err) {
      setError(err.message);
    }
  }

  async function remove() {
    if (!match) return;
    try {
      await api.deleteView(match.id);
      setName("");
      setMessage(null);
      refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="saved-views no-print">
      <input
        list={listId}
        value={name}
        onChange={change}
        onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), save())}
        placeholder={
          placeholder ||
          (loadOnly
            ? t("common.savedViews.pickSavedView")
            : views.length
              ? t("common.savedViews.pickOrTypeName")
              : t("common.savedViews.typeNameToSave"))
        }
        aria-label={t("common.savedViews.ariaLabel")}
        maxLength={120}
      />
      <datalist id={listId}>
        {views.map((v) => (
          <option key={v.id} value={v.name} />
        ))}
      </datalist>
      {!loadOnly && (
        <button type="button" onClick={save} disabled={!name.trim()}>
          {match ? t("common.savedViews.update") : t("common.savedViews.save")}
        </button>
      )}
      {match && !loadOnly && (
        <button type="button" className="link-like danger" onClick={remove}>
          {t("common.savedViews.delete")}
        </button>
      )}
      {message && <span className="hint">{message}</span>}
      {error && <span className="error">{error}</span>}
    </div>
  );
}
