import { useState } from "react";
import { useAuth } from "../AuthContext.jsx";
import { api } from "../api/client.js";

// Shown instead of the whole app whenever AuthContext has no logged-in
// user (see App.jsx) -- covers first-ever setup (register -> becomes
// admin -> straight in), an ordinary login, a fresh registration that
// now has to wait on the admin, and a pending/denied account trying to
// log in again before that's resolved.
export default function LoginView() {
  const { refresh } = useAuth();
  const [mode, setMode] = useState("login"); // "login" | "register"
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [pendingMessage, setPendingMessage] = useState(null);
  const [submitting, setSubmitting] = useState(false);

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
          setPendingMessage("Account created. An admin needs to approve it before you can log in.");
          setPassword("");
        }
      } else {
        await api.login(username.trim(), password);
        await refresh();
      }
    } catch (err) {
      setError(err.message || "Something went wrong");
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
            Log in
          </button>
          <button
            type="button"
            className={mode === "register" ? "active" : ""}
            onClick={() => switchMode("register")}
          >
            Register
          </button>
        </div>

        <div className="form-row">
          <label>
            Username
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
            Password
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

        {mode === "register" && (
          <p className="hint">
            The very first account registered on this install becomes its admin, with immediate access. Every
            account after that needs the admin's approval before it can log in.
          </p>
        )}
        {error && <p className="error">{error}</p>}
        {pendingMessage && <p className="hint">{pendingMessage}</p>}

        <button type="submit" disabled={submitting}>
          {mode === "register" ? "Register" : "Log in"}
        </button>
      </form>
    </div>
  );
}
