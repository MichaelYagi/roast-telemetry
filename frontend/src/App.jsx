import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { settingsStreamUrl } from "./api/client.js";
import { AuthProvider, useAuth } from "./AuthContext.jsx";
import AccountModal from "./components/AccountModal.jsx";
import { DialogProvider } from "./components/DialogProvider.jsx";
import ThemePicker from "./components/ThemePicker.jsx";
import i18n from "./i18n.js";
import useServerStatus from "./useServerStatus.js";
import HistoryDashboard from "./views/HistoryDashboard.jsx";
import LiveRoastView from "./views/LiveRoastView.jsx";
import LoginView from "./views/LoginView.jsx";
import AnalysisView from "./views/AnalysisView.jsx";
import ActivityView from "./views/ActivityView.jsx";
import BeansView from "./views/BeansView.jsx";
import RoastDetailView from "./views/RoastDetailView.jsx";
import SettingsView from "./views/SettingsView.jsx";
import UsersView from "./views/UsersView.jsx";

export default function App() {
  return (
    <AuthProvider>
      <DialogProvider>
        <AppShell />
      </DialogProvider>
    </AuthProvider>
  );
}

function AppShell() {
  const { t } = useTranslation();
  const { user, loading, logout } = useAuth();
  const [accountOpen, setAccountOpen] = useState(false);
  // The OS/version/LAN address the server is actually running on --
  // useful in the footer since this app runs self-hosted (Windows/WSL2/
  // macOS/Linux all behave differently for hardware access), and can't
  // be baked in at build time the way version/build already are, since
  // the same build runs anywhere. Rides along on useServerStatus's own
  // recurring poll rather than a separate one-shot fetch.
  const { status: serverStatus, activeRoast, platform: serverPlatform, osVersion: serverOsVersion, lanIp: serverLanIp } =
    useServerStatus();

  // Lets the browser tab itself say a roast is running -- visible even
  // with this tab in the background/unfocused, which no in-app element
  // (nav badge included) can cover. Deliberately no live elapsed-time
  // ticking here: this only needs to update on each ~5s health poll (see
  // useServerStatus.js), not every second, to answer "is something
  // running," not serve as a clock.
  useEffect(() => {
    document.title = activeRoast ? `\u{1F525} ${activeRoast.title} — Roast Telemetry` : "Roast Telemetry";
    return () => {
      document.title = "Roast Telemetry";
    };
  }, [activeRoast]);

  // Settings > Language (AppSettings.language) applies here, not just on
  // the Settings page itself -- the nav/footer/etc. this file renders are
  // outside any one route. Same settings-push stream LiveRoastView.jsx
  // uses for its own hot-apply (pushes the current settings immediately
  // on connect, then again on every save from any client), so a language
  // change in one tab updates every other open tab too.
  useEffect(() => {
    // Settings is an authenticated endpoint -- nothing to connect yet
    // while still on the login screen (avoids a stream of 401s there);
    // `user` in the dependency array means this actually opens the
    // instant login completes, not just on some later unrelated re-render.
    if (!user) return undefined;
    const source = new EventSource(settingsStreamUrl());
    source.addEventListener("settings", (e) => {
      try {
        const s = JSON.parse(e.data);
        if (s.language) i18n.changeLanguage(s.language);
      } catch {
        // malformed/partial event -- ignore, next push will self-correct
      }
    });
    return () => source.close();
  }, [user]);

  // Nothing rendered yet during the one-time /auth/me check on mount --
  // faster than a spinner for what's normally a same-machine round trip,
  // and avoids a flash of the login form for someone who's already
  // logged in.
  if (loading) return null;
  if (!user) return <LoginView />;

  return (
    <div className="app-shell">
      <header className="app-header">
        {/* Split into a wrapper so the *inner* row's width (not the header
            itself) is what gets constrained to match the breakout split --
            see the body.breakout-split-active rules in styles.css. Default
            (non-split) layout is unaffected either way. */}
        <div className="app-header-inner">
          <div className="app-title-group">
            <Link to="/" className="app-title-link">
              <img className="app-logo" src="/icon-48x48.png" alt="" width="28" height="28" />
              {/* Wraps the title and status dot together so they stay one
                  grid item at phone width (see the app-title-text rules in
                  styles.css) -- previously the dot had no explicit mobile
                  grid placement and landed on its own row below the nav. */}
              <span className="app-title-text">
                <h1>Roast Telemetry</h1>
                <span
                  className={`server-status-dot server-status-dot-${serverStatus}`}
                  title={
                    serverStatus === "green"
                      ? t("app.statusDot.connected")
                      : serverStatus === "yellow"
                        ? t("app.statusDot.notConnected")
                        : t("app.statusDot.unreachable")
                  }
                />
              </span>
            </Link>
            <ThemePicker />
          </div>
          <nav>
            <NavLink to="/" end>
              {t("app.nav.liveRoast")}
              {activeRoast && (
                <span className="nav-roast-badge" title={t("app.roastBadge", { title: activeRoast.title })} />
              )}
            </NavLink>
            <NavLink to="/history">{t("app.nav.history")}</NavLink>
            <NavLink to="/analysis">{t("app.nav.analysis")}</NavLink>
            <NavLink to="/beans">{t("app.nav.beans")}</NavLink>
            <NavLink to="/activity">{t("app.nav.activity")}</NavLink>
            <NavLink to="/settings">{t("app.nav.settings")}</NavLink>
            {user.role === "admin" && <NavLink to="/users">{t("app.nav.manageAccess")}</NavLink>}
          </nav>
          <div className="app-user-group">
            {/* Opens the Account modal (API key management) -- see
                AccountModal.jsx. A plain button, not a link, since this
                never navigates anywhere. */}
            <button type="button" className="app-username" onClick={() => setAccountOpen(true)}>
              {user.username}
            </button>
            <button type="button" className="logout-button" onClick={logout}>
              {t("app.logout")}
            </button>
          </div>
        </div>
      </header>
      <AccountModal open={accountOpen} onClose={() => setAccountOpen(false)} user={user} />
      <main className="app-main">
        <Routes>
          <Route path="/" element={<LiveRoastView />} />
          <Route path="/history" element={<HistoryDashboard />} />
          <Route path="/roasts/:id" element={<RoastDetailView />} />
          {/* Comparing is now done from History: select roasts there and press Compare. */}
          <Route path="/compare" element={<Navigate to="/history" replace />} />
          <Route path="/analysis" element={<AnalysisView />} />
          <Route path="/beans" element={<BeansView />} />
          <Route path="/activity" element={<ActivityView />} />
          <Route path="/settings" element={<SettingsView />} />
          <Route path="/users" element={user.role === "admin" ? <UsersView /> : <Navigate to="/" replace />} />
        </Routes>
      </main>
      <footer className="app-footer no-print">
        v{__APP_VERSION__} · build {__APP_BUILD__}
        {serverPlatform && (
          <>
            {" "}
            · {serverPlatform}
            {serverOsVersion && ` ${serverOsVersion}`}
          </>
        )}
        {serverLanIp && <> · {serverLanIp}</>} · AGPL-3.0-or-later ·{" "}
        {/* AGPL section 13: a network-served program has to offer its users
            its source -- the license's own guidance is a "Source" link in the
            interface. */}
        <a href="https://github.com/MichaelYagi/roast-telemetry" target="_blank" rel="noreferrer">
          {t("app.footer.sourceCode")}
        </a>
      </footer>
    </div>
  );
}
