import { useEffect, useId, useState } from "react";
import { api } from "../api/client.js";

const same = (a, b) => (a || "").trim().toLowerCase() === (b || "").trim().toLowerCase();

// Saved views, as one field: pick a saved view from the suggestions to load it,
// or type a name and Save to keep the page's current settings under it. Saving
// under a name that already exists updates that view.
// `getConfig()` returns what to save; `onLoad(config)` applies a saved one.
export default function SavedViews({ kind, getConfig, onLoad, loadOnly = false, placeholder }) {
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
      setMessage("Loaded.");
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
      setMessage(match ? "Updated." : "Saved.");
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
          placeholder || (loadOnly ? "Pick a saved view" : views.length ? "Saved views: pick one, or type a name to save" : "Type a name to save this view")
        }
        aria-label="Saved views"
        maxLength={120}
      />
      <datalist id={listId}>
        {views.map((v) => (
          <option key={v.id} value={v.name} />
        ))}
      </datalist>
      {!loadOnly && (
        <button type="button" onClick={save} disabled={!name.trim()}>
          {match ? "Update" : "Save"}
        </button>
      )}
      {match && !loadOnly && (
        <button type="button" className="link-like danger" onClick={remove}>
          delete
        </button>
      )}
      {message && <span className="hint">{message}</span>}
      {error && <span className="error">{error}</span>}
    </div>
  );
}
