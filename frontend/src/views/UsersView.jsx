import { useEffect, useState } from "react";
import { useAuth } from "../AuthContext.jsx";
import { api } from "../api/client.js";

// Admin-only -- App.jsx doesn't even route here for a non-admin (see its
// role check), but this loads its own list independently either way, so
// it's never relying on the router alone to keep a non-admin out.
export default function UsersView() {
  const { user } = useAuth();
  const [users, setUsers] = useState(null);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  function load() {
    api
      .listUsers()
      .then(setUsers)
      .catch((err) => setError(err.message));
  }

  useEffect(load, []);

  async function runAction(id, action) {
    setBusyId(id);
    setError(null);
    try {
      await action();
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  }

  if (error && !users) {
    return (
      <div className="panel">
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!users) return null;

  return (
    <div className="panel">
      <h2>Manage access</h2>
      <p className="hint">
        Every registered account below has full access to everything else in the app once Allowed -- there's no
        per-feature permission, just this one gate.
      </p>
      {error && <p className="error">{error}</p>}
      <table className="users-table">
        <thead>
          <tr>
            <th>Username</th>
            <th>Role</th>
            <th>Status</th>
            <th>Registered</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => {
            const isSelf = u.id === user?.id;
            const busy = busyId === u.id;
            return (
              <tr key={u.id}>
                <td>{u.username}</td>
                <td>{u.role}</td>
                <td>
                  <span className={`user-status-badge user-status-${u.status}`}>{u.status}</span>
                </td>
                <td>{new Date(u.created_at).toLocaleString()}</td>
                <td className="users-table-actions">
                  {u.status === "pending" && (
                    <>
                      <button type="button" disabled={busy} onClick={() => runAction(u.id, () => api.allowUser(u.id))}>
                        Allow
                      </button>
                      <button
                        type="button"
                        className="danger"
                        disabled={busy}
                        onClick={() => runAction(u.id, () => api.denyUser(u.id))}
                      >
                        Deny
                      </button>
                    </>
                  )}
                  {u.status === "allowed" && !isSelf && (
                    <button
                      type="button"
                      className="danger"
                      disabled={busy}
                      onClick={() => runAction(u.id, () => api.denyUser(u.id))}
                    >
                      Deny
                    </button>
                  )}
                  {u.status === "denied" && (
                    <button type="button" disabled={busy} onClick={() => runAction(u.id, () => api.resetUserToPending(u.id))}>
                      Reset to pending
                    </button>
                  )}
                  {!isSelf && (
                    <button
                      type="button"
                      className="danger"
                      disabled={busy}
                      onClick={() => {
                        if (window.confirm(`Delete ${u.username}? This can't be undone.`)) {
                          runAction(u.id, () => api.deleteUser(u.id));
                        }
                      }}
                    >
                      Delete
                    </button>
                  )}
                  {isSelf && <span className="hint">(you)</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
