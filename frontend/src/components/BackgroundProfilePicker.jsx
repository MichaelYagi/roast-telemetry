import { useEffect, useState } from "react";
import { api } from "../api/client.js";

// Only roasts with a real, finished profile are worth pacing against --
// "roasting"/"cooling" (still in progress somewhere else, or this is
// that very roast) and "idle"/"aborted" (no meaningful curve) are
// excluded. The backend's own status filter only takes one value at a
// time, so this filters client-side rather than making two requests.
const FINISHED_STATUSES = new Set(["stopped", "complete"]);

// Lets the operator load a previously recorded roast's BT/ET curve onto
// the live chart as a reference to pace against (a "Background Profile"). Purely a display overlay (see RoastChart.jsx):
// it never reads from or influences the live roast's own recording or
// its automation, and the choice isn't saved anywhere -- picking one is
// a per-session decision, cleared on refresh, same as it not being part
// of a roast's own saved config.
export default function BackgroundProfilePicker({ excludeId, selectedId, selectedTitle, onSelect }) {
  const [roasts, setRoasts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .listRoasts({ limit: 200 })
      .then((rows) => setRoasts(rows.filter((r) => FINISHED_STATUSES.has(r.status) && r.id !== excludeId)))
      .catch(() => {});
    // excludeId intentionally omitted -- re-filtering on every id change
    // during an active roast would refetch for no reason; the exclusion
    // only matters at the moment the list is loaded.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleChange(e) {
    const id = e.target.value;
    if (!id) {
      onSelect(null, null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const roast = await api.getRoast(id);
      onSelect(id, roast);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="background-profile-picker">
      <label>
        Background profile
        <select value={selectedId || ""} onChange={handleChange} disabled={loading}>
          <option value="">None</option>
          {roasts.map((r) => (
            <option key={r.id} value={r.id}>
              {r.title} — {new Date(r.created_at).toLocaleDateString()}
            </option>
          ))}
        </select>
      </label>
      {selectedId && (
        <button type="button" className="link-like" onClick={() => onSelect(null, null)}>
          Clear
        </button>
      )}
      {error && <span className="error">{error}</span>}
      {selectedTitle && <span className="hint">Pacing against "{selectedTitle}" — BT/ET shown as dashed reference lines.</span>}
    </div>
  );
}
