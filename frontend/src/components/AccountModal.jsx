import { useState } from "react";
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
      setPwError("New password must be at least 8 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setPwError("New passwords don't match.");
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
    if (!(await confirm("Turn off API key access? This can't be undone -- you'd need to generate a new key to use the API directly again.", { confirmLabel: "Revoke" }))) {
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
    <Modal open={open} onClose={handleClose} title="Account">
      <p className="account-username">
        Signed in as <strong>{user?.username}</strong>
        {user?.role === "admin" && <span className="account-role-badge">admin</span>}
      </p>

      <h4>Change password</h4>
      <form className="account-password-form" onSubmit={handleChangePassword}>
        <div className="form-row">
          <label>
            Current password
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
            New password
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
            Confirm new password
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
        {pwSuccess && <p className="hint account-password-success">Password changed. Any other signed-in browser has been logged out.</p>}

        <div className="dialog-actions">
          <button type="submit" disabled={pwBusy}>
            Change password
          </button>
        </div>
      </form>

      <h4>API key</h4>
      <p className="hint">
        Lets a script, a Home Assistant integration, or curl call the API directly with an{" "}
        <code>X-API-Key</code> header, instead of logging in through the browser. Not required for using this app
        normally.
      </p>

      {revealedKey ? (
        <div className="account-key-reveal">
          <p className="hint account-key-warning">Copy this now -- it won't be shown again.</p>
          <div className="account-key-row">
            <code className="account-key-value">{revealedKey}</code>
            <button type="button" onClick={handleCopy}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </div>
      ) : (
        <p className="hint">{user?.has_api_key ? "A key is active (hidden -- regenerate to see a new one)." : "No key yet."}</p>
      )}

      {error && <p className="error">{error}</p>}

      <div className="dialog-actions account-key-actions">
        {user?.has_api_key && !revealedKey && (
          <button type="button" className="danger" onClick={handleRevoke} disabled={busy}>
            Revoke
          </button>
        )}
        <button type="button" onClick={handleGenerate} disabled={busy}>
          {user?.has_api_key ? "Regenerate" : "Generate API key"}
        </button>
      </div>
    </Modal>
  );
}
