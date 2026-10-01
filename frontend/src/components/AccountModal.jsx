import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client.js";
import { useConfirm } from "./DialogProvider.jsx";
import Modal from "./Modal.jsx";

// Opened by clicking the username in the header (App.jsx). API key
// management lives here: each key is independently named, created, and
// revoked -- revoking one never affects any other integration's key
// (see storage.py's api_keys table). The plaintext key is only ever
// shown once, right after it's created -- neither this component nor
// the backend can recover it afterward (only its hash is stored).
export default function AccountModal({ open, onClose, user }) {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const [keys, setKeys] = useState([]);
  const [newKeyName, setNewKeyName] = useState("");
  const [revealedKey, setRevealedKey] = useState(null); // cleared on close; the just-created key only
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open) return;
    api.listApiKeys().then(setKeys).catch((err) => setError(err.message));
  }, [open]);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [pwBusy, setPwBusy] = useState(false);
  const [pwError, setPwError] = useState(null);
  const [pwSuccess, setPwSuccess] = useState(false);

  function handleClose() {
    setRevealedKey(null);
    setCopied(false);
    setError(null);
    setNewKeyName("");
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setPwError(null);
    setPwSuccess(false);
    onClose();
  }

  async function handleChangePassword(e) {
    e.preventDefault();
    setPwError(null);
    setPwSuccess(false);
    // Mirrors the backend's own Field(min_length=8) (models.py's
    // ChangePasswordRequest) -- catching this here instead of just
    // letting the request 422 avoids surfacing FastAPI's raw validation
    // error shape (a list of objects, not a plain string) as the error
    // message.
    if (newPassword.length < 8) {
      setPwError(t("common.accountModal.changePassword.tooShort"));
      return;
    }
    if (newPassword !== confirmPassword) {
      setPwError(t("common.accountModal.changePassword.mismatch"));
      return;
    }
    setPwBusy(true);
    try {
      await api.changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setPwSuccess(true);
    } catch (err) {
      setPwError(err.message);
    } finally {
      setPwBusy(false);
    }
  }

  async function handleCreate(e) {
    e.preventDefault();
    const name = newKeyName.trim();
    if (!name) return;
    setBusy(true);
    setError(null);
    setCopied(false);
    try {
      const created = await api.createApiKey(name);
      setRevealedKey(created.api_key);
      setNewKeyName("");
      setKeys(await api.listApiKeys());
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleRevoke(key) {
    const confirmed = await confirm(t("common.accountModal.apiKey.revokeConfirm", { name: key.name }), {
      confirmLabel: t("common.accountModal.apiKey.revoke"),
    });
    if (!confirmed) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteApiKey(key.id);
      setKeys(await api.listApiKeys());
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  // navigator.clipboard only exists in a secure context (HTTPS or
  // localhost) -- this app deliberately runs over plain HTTP on a LAN
  // (see auth.py's start_session comment), so on a real LAN deployment
  // navigator.clipboard is simply undefined, not just permission-denied.
  // document.execCommand("copy") is older/deprecated but still broadly
  // supported and, unlike the Clipboard API, isn't restricted to secure
  // contexts -- the actual fallback that makes this work at all there.
  function legacyCopy(text) {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    document.body.removeChild(textarea);
    return ok;
  }

  async function handleCopy() {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(revealedKey);
        setCopied(true);
        return;
      } catch {
        // Falls through to the legacy path below -- e.g. permission denied
        // even though the API exists.
      }
    }
    if (legacyCopy(revealedKey)) setCopied(true);
    // Either way, the key text itself is still visible and selectable
    // (user-select: all on .account-key-value) as a manual last resort.
  }

  return (
    <Modal open={open} onClose={handleClose} title={t("common.accountModal.title")}>
      <p className="account-username">
        {t("common.accountModal.signedInAs")} <strong>{user?.username}</strong>
        {user?.role === "admin" && <span className="account-role-badge">{t("common.accountModal.admin")}</span>}
      </p>

      <h4>{t("common.accountModal.changePassword.heading")}</h4>
      <form className="account-password-form" onSubmit={handleChangePassword}>
        <div className="form-row">
          <label>
            {t("common.accountModal.changePassword.currentPassword")}
            <input
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              required
            />
          </label>
        </div>
        <div className="form-row">
          <label>
            {t("common.accountModal.changePassword.newPassword")}
            <input
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
            />
          </label>
        </div>
        <div className="form-row">
          <label>
            {t("common.accountModal.changePassword.confirmNewPassword")}
            <input
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
            />
          </label>
        </div>

        {pwError && <p className="error">{pwError}</p>}
        {pwSuccess && <p className="hint account-password-success">{t("common.accountModal.changePassword.success")}</p>}

        <div className="dialog-actions">
          <button type="submit" disabled={pwBusy}>
            {t("common.accountModal.changePassword.submit")}
          </button>
        </div>
      </form>

      <h4>{t("common.accountModal.apiKey.heading")}</h4>
      <p className="hint">
        {t("common.accountModal.apiKey.hintPrefix")} <code>X-API-Key</code> {t("common.accountModal.apiKey.hintSuffix")}
      </p>

      {revealedKey && (
        <div className="account-key-reveal">
          <p className="hint account-key-warning">{t("common.accountModal.apiKey.copyNowWarning")}</p>
          <div className="account-key-row">
            <code className="account-key-value">{revealedKey}</code>
            <button type="button" onClick={handleCopy}>
              {copied ? t("common.accountModal.apiKey.copied") : t("common.accountModal.apiKey.copy")}
            </button>
          </div>
        </div>
      )}

      {keys.length === 0 && !revealedKey && <p className="hint">{t("common.accountModal.apiKey.empty")}</p>}

      {keys.length > 0 && (
        <ul className="account-key-list">
          {keys.map((key) => (
            <li key={key.id} className="account-key-list-row">
              <div>
                <strong>{key.name}</strong>
                <p className="hint">
                  {t("common.accountModal.apiKey.created", { date: new Date(key.created_at).toLocaleDateString() })}
                  {" · "}
                  {key.last_used_at
                    ? t("common.accountModal.apiKey.lastUsed", { date: new Date(key.last_used_at).toLocaleString() })
                    : t("common.accountModal.apiKey.neverUsed")}
                </p>
              </div>
              <button type="button" className="danger" onClick={() => handleRevoke(key)} disabled={busy}>
                {t("common.accountModal.apiKey.revoke")}
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && <p className="error">{error}</p>}

      <form className="account-key-add" onSubmit={handleCreate}>
        <input
          type="text"
          value={newKeyName}
          onChange={(e) => setNewKeyName(e.target.value)}
          placeholder={t("common.accountModal.apiKey.namePlaceholder")}
        />
        <button type="submit" disabled={busy || !newKeyName.trim()}>
          {t("common.accountModal.apiKey.add")}
        </button>
      </form>
    </Modal>
  );
}
