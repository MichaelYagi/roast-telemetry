import { useState } from "react";
import { api } from "../api/client.js";
import { formatTime } from "../chartDefaults.js";

// "Sep 20, 2026, 9:41 PM" in the viewer's own time zone.
function formatWritten(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

// Notes for one roast: add, edit and delete, during the roast and after it.
// Shared by Live Roast and the roast's own page. After every change the
// panel re-reads the roast's notes from the server and hands the whole
// list to `onReplace` -- a finished roast's note ids are just their
// position in the saved file, so they shift when a note is removed and a
// locally patched copy would soon point at the wrong note.
export default function NotesPanel({ roastId, notes, onReplace }) {
  const [draft, setDraft] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [editText, setEditText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function run(action) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err.message);
      // The note was probably changed or removed elsewhere -- show what's there now.
      try {
        await refresh();
        setEditingId(null);
      } catch {
        /* keep the first error */
      }
    } finally {
      setBusy(false);
    }
  }

  async function refresh() {
    const latest = await api.getRoast(roastId);
    onReplace(latest.notes || []);
  }

  const add = () =>
    run(async () => {
      await api.addNote(roastId, { text: draft.trim() });
      setDraft("");
      await refresh();
    });

  const saveEdit = () =>
    run(async () => {
      await api.updateNote(roastId, editingId, editText.trim());
      setEditingId(null);
      await refresh();
    });

  const remove = (noteId) =>
    run(async () => {
      await api.deleteNote(roastId, noteId);
      // Ids can shift after a delete, so don't keep an edit open on one.
      setEditingId(null);
      await refresh();
    });

  return (
    <div className="panel notes-panel">
      <h3>Notes</h3>
      <div className="note-input">
        <textarea
          placeholder="Add a note…"
          rows={3}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={!roastId}
        />
        <button type="button" onClick={add} disabled={busy || !roastId || !draft.trim()}>
          Add
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      <ul className="note-feed">
        {notes.map((n) =>
          editingId === n.id ? (
            <li key={n.id} className="note-editing">
              <textarea rows={3} value={editText} onChange={(e) => setEditText(e.target.value)} autoFocus />
              <span className="note-actions">
                <button type="button" onClick={saveEdit} disabled={busy || !editText.trim()}>
                  Save
                </button>
                <button type="button" className="link-like" onClick={() => setEditingId(null)}>
                  Cancel
                </button>
              </span>
            </li>
          ) : (
            <li key={n.id} className="note-item">
              <span className="note-text">{n.text}</span>
              <span className="note-meta">
                {n.created_at ? `${formatWritten(n.created_at)} ` : ""}@ {formatTime(n.time_s)}
                {n.author ? ` · ${n.author}` : ""}
              </span>
              <span className="note-actions no-print">
                <button
                  type="button"
                  className="link-like"
                  onClick={() => {
                    setEditingId(n.id);
                    setEditText(n.text);
                    setError(null);
                  }}
                >
                  edit
                </button>
                <button type="button" className="danger link-like" onClick={() => remove(n.id)} disabled={busy}>
                  delete
                </button>
              </span>
            </li>
          )
        )}
        {notes.length === 0 && <li>No notes.</li>}
      </ul>
    </div>
  );
}
