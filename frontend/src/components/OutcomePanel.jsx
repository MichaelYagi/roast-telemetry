import { useEffect, useState } from "react";
import { api } from "../api/client.js";

// What the roast turned out like, filled in afterwards: color, cupping score,
// a 1-5 rating and tasting notes. These become columns for comparing and
// analysing roasts.
export default function OutcomePanel({ roast, onSaved }) {
  const fromRoast = (r) => ({
    color_agtron: r.color_agtron ?? "",
    cupping_score: r.cupping_score ?? "",
    rating: r.rating ?? "",
    tasting_notes: r.tasting_notes ?? "",
  });
  const [form, setForm] = useState(() => fromRoast(roast));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setForm(fromRoast(roast));
  }, [roast.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const num = (v) => (v === "" ? null : Number(v));

  async function save() {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const saved = await api.setOutcome(roast.id, {
        color_agtron: num(form.color_agtron),
        cupping_score: num(form.cupping_score),
        rating: num(form.rating),
        tasting_notes: form.tasting_notes.trim() || null,
      });
      onSaved?.(saved);
      setMessage("Saved.");
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  return (
    <div className="panel outcome-panel no-print">
      <h3>How it turned out</h3>
      <div className="outcome-grid">
        <label>
          Color (Agtron)
          <input type="number" min="0" max="250" step="0.1" value={form.color_agtron} onChange={set("color_agtron")} />
        </label>
        <label>
          Cupping score
          <input type="number" min="0" max="100" step="0.25" value={form.cupping_score} onChange={set("cupping_score")} />
        </label>
        <label>
          Rating
          <select value={form.rating} onChange={set("rating")}>
            <option value="">—</option>
            {[1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>
                {"★".repeat(n)} ({n})
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="outcome-notes">
        Tasting notes
        <textarea rows={3} value={form.tasting_notes} onChange={set("tasting_notes")} maxLength={2000} />
      </label>
      <div className="outcome-actions">
        <button type="button" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        {message && <span className="hint">{message}</span>}
        {error && <span className="error">{error}</span>}
      </div>
    </div>
  );
}
