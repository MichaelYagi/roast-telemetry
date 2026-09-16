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

  function handleClose() {
    setRevealedKey(null);
    setCopied(false);
    setError(null);
    onClose();
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

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(revealedKey);
      setCopied(true);
    } catch {
      // Clipboard API can fail (permissions, non-HTTPS context) -- the key
      // text is still selectable/visible either way, nothing more to do.
    }
  }

  return (
    <Modal open={open} onClose={handleClose} title="Account">
      <p className="account-username">
        Signed in as <strong>{user?.username}</strong>
        {user?.role === "admin" && <span className="account-role-badge">admin</span>}
      </p>

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
