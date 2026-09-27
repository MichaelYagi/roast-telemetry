import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../AuthContext.jsx";
import { api } from "../api/client.js";
import { useConfirm } from "../components/DialogProvider.jsx";

// Admin-only -- App.jsx doesn't even route here for a non-admin (see its
// role check), but this loads its own list independently either way, so
// it's never relying on the router alone to keep a non-admin out.
export default function UsersView() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const confirm = useConfirm();
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
      <h2>{t("app.nav.manageAccess")}</h2>
      <p className="hint">{t("users.hint")}</p>
      {error && <p className="error">{error}</p>}
      <table className="users-table">
        <thead>
          <tr>
            <th>{t("users.table.username")}</th>
            <th>{t("users.table.role")}</th>
            <th>{t("users.table.status")}</th>
            <th>{t("users.table.registered")}</th>
            <th>{t("users.table.actions")}</th>
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
                        {t("users.allow")}
                      </button>
                      <button
                        type="button"
                        className="danger"
                        disabled={busy}
                        onClick={() => runAction(u.id, () => api.denyUser(u.id))}
                      >
                        {t("users.deny")}
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
                      {t("users.deny")}
                    </button>
                  )}
                  {u.status === "denied" && (
                    <button type="button" disabled={busy} onClick={() => runAction(u.id, () => api.resetUserToPending(u.id))}>
                      {t("users.resetToPending")}
                    </button>
                  )}
                  {!isSelf && (
                    <button
                      type="button"
                      className="danger"
                      disabled={busy}
                      onClick={async () => {
                        if (await confirm(t("users.deleteConfirm", { username: u.username }))) {
                          runAction(u.id, () => api.deleteUser(u.id));
                        }
                      }}
                    >
                      {t("users.delete")}
                    </button>
                  )}
                  {isSelf && <span className="hint">{t("users.you")}</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
