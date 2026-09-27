import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../AuthContext.jsx";
import { api } from "../api/client.js";

// Shown instead of the whole app whenever AuthContext has no logged-in
// user (see App.jsx) -- covers first-ever setup (register -> becomes
// admin -> straight in), an ordinary login, a fresh registration that
// now has to wait on the admin, and a pending/denied account trying to
// log in again before that's resolved.
export default function LoginView() {
  const { t } = useTranslation();
  const { refresh } = useAuth();
  const [mode, setMode] = useState("login"); // "login" | "register"
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [rememberMe, setRememberMe] = useState(false);
  const [error, setError] = useState(null);
  const [pendingMessage, setPendingMessage] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  // null while loading -- the register-mode hint below waits for this
  // rather than assuming "no admin yet" and flashing the wrong copy for
  // every registration after the first one.
  const [hasAdmin, setHasAdmin] = useState(null);

  useEffect(() => {
    api
      .authStatus()
      .then((s) => setHasAdmin(s.has_admin))
      .catch(() => setHasAdmin(true)); // can't tell -- assume the safer, more common case
  }, []);

  // First-ever visit to a fresh install (no admin yet) -- land straight on
  // Register instead of Login, since there's no account to log into yet.
  // Only fires once, right when the fetch above resolves, so it never
  // fights a manual tab switch afterward.
  useEffect(() => {
    if (hasAdmin === false) setMode("register");
  }, [hasAdmin]);

  function switchMode(next) {
    setMode(next);
    setError(null);
    setPendingMessage(null);
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setPendingMessage(null);
    setSubmitting(true);
    try {
      if (mode === "register") {
        const created = await api.register(username.trim(), password);
        if (created.status === "allowed") {
          await refresh(); // first-ever account -- admin, auto-logged-in
        } else {
          setPendingMessage(t("login.pendingApproval"));
          setPassword("");
        }
      } else {
        await api.login(username.trim(), password, rememberMe);
        await refresh();
      }
    } catch (err) {
      setError(err.message || t("login.genericError"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-shell">
      <form className="panel login-panel" onSubmit={handleSubmit}>
        <h1>Roast Telemetry</h1>
        <div className="login-mode-toggle">
          <button type="button" className={mode === "login" ? "active" : ""} onClick={() => switchMode("login")}>
            {t("login.logIn")}
          </button>
          <button
            type="button"
            className={mode === "register" ? "active" : ""}
            onClick={() => switchMode("register")}
          >
            {t("login.register")}
          </button>
        </div>

        <div className="form-row">
          <label>
            {t("login.username")}
            <input
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </label>
        </div>
        <div className="form-row">
          <label>
            {t("login.password")}
            <input
              type="password"
              autoComplete={mode === "register" ? "new-password" : "current-password"}
              minLength={mode === "register" ? 8 : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
        </div>

        {mode === "login" && (
          <div className="form-row">
            <label className="checkbox-label">
              <input type="checkbox" checked={rememberMe} onChange={(e) => setRememberMe(e.target.checked)} />
              {t("login.rememberMe")}
            </label>
          </div>
        )}
        {mode === "login" && (
          <p className="hint">{rememberMe ? t("login.rememberMeOnHint") : t("login.rememberMeOffHint")}</p>
        )}
        {mode === "register" && hasAdmin === false && <p className="hint">{t("login.firstAdminHint")}</p>}
        {error && <p className="error">{error}</p>}
        {pendingMessage && <p className="hint">{pendingMessage}</p>}

        <button type="submit" disabled={submitting}>
          {mode === "register" ? t("login.register") : t("login.logIn")}
        </button>
      </form>
    </div>
  );
}
