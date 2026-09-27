import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client.js";
import { useConfirm } from "./DialogProvider.jsx";
import Modal from "./Modal.jsx";

// Opened by clicking the username in the header (App.jsx). API key
// management lives here: Generate (first time), Regenerate (swap for a
// new one, old stops working immediately), Revoke (turn off entirely,
// no replacement -- distinct from Regenerate for the "I don't want a
// live key right now at all" case, e.g. a suspected leak with no new
// integration ready). The plaintext key is only ever shown once, right
// after Generate/Regenerate -- neither this component nor the backend
// can recover it afterward (only its hash is stored).
export default function AccountModal({ open, onClose, user, onUserChange }) {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const [revealedKey, setRevealedKey] = useState(null); // cleared on close
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

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

  async function handleGenerate() {
    setBusy(true);
    setError(null);
    setCopied(false);
    try {
      const { api_key } = await api.generateApiKey();
      setRevealedKey(api_key);
      onUserChange?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleRevoke() {
    if (!(await confirm(t("common.accountModal.apiKey.revokeConfirm"), { confirmLabel: t("common.accountModal.apiKey.revoke") }))) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.revokeApiKey();
      setRevealedKey(null);
      onUserChange?.();
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

      {revealedKey ? (
        <div className="account-key-reveal">
          <p className="hint account-key-warning">{t("common.accountModal.apiKey.copyNowWarning")}</p>
          <div className="account-key-row">
            <code className="account-key-value">{revealedKey}</code>
            <button type="button" onClick={handleCopy}>
              {copied ? t("common.accountModal.apiKey.copied") : t("common.accountModal.apiKey.copy")}
            </button>
          </div>
        </div>
      ) : (
        <p className="hint">{user?.has_api_key ? t("common.accountModal.apiKey.active") : t("common.accountModal.apiKey.none")}</p>
      )}

      {error && <p className="error">{error}</p>}

      <div className="dialog-actions account-key-actions">
        {user?.has_api_key && !revealedKey && (
          <button type="button" className="danger" onClick={handleRevoke} disabled={busy}>
            {t("common.accountModal.apiKey.revoke")}
          </button>
        )}
        <button type="button" onClick={handleGenerate} disabled={busy}>
          {user?.has_api_key ? t("common.accountModal.apiKey.regenerate") : t("common.accountModal.apiKey.generate")}
        </button>
      </div>
    </Modal>
  );
}
