import { useEffect, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./AuthContext.jsx";
import ThemePicker from "./components/ThemePicker.jsx";
import HistoryDashboard from "./views/HistoryDashboard.jsx";
import LiveRoastView from "./views/LiveRoastView.jsx";
import LoginView from "./views/LoginView.jsx";
import RoastComparisonView from "./views/RoastComparisonView.jsx";
import RoastDetailView from "./views/RoastDetailView.jsx";
import SettingsView from "./views/SettingsView.jsx";
import UsersView from "./views/UsersView.jsx";

export default function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  );
}

// Drawer links -- also doubles as the header bar's page-title lookup below
// (pageTitle), so a route's display name only needs to be written once.
const NAV_ITEMS = [
  { to: "/", label: "Live Roast", end: true },
  { to: "/history", label: "History" },
  { to: "/compare", label: "Compare" },
  { to: "/settings", label: "Settings" },
  { to: "/users", label: "Manage Access", adminOnly: true },
];

// The header bar shows the current *page's* own title now, not the site's
// (site branding -- logo + "Roast Telemetry" -- moved to the top of the
// drawer instead, see AppDrawer). /roasts/:id isn't a drawer link (you
// reach it from a History row, not the menu), so it needs its own case
// here rather than falling out of NAV_ITEMS.
function pageTitle(pathname) {
  if (pathname.startsWith("/roasts/")) return "Roast Detail";
  const item = NAV_ITEMS.find((i) => (i.end ? pathname === i.to : pathname.startsWith(i.to)));
  return item?.label || "Roast Telemetry";
}

function AppShell() {
  const { user, loading, logout } = useAuth();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const location = useLocation();

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
          <button
            type="button"
            className="hamburger-button"
            onClick={() => setDrawerOpen(true)}
            aria-label="Open menu"
            aria-expanded={drawerOpen}
          >
            <span />
            <span />
            <span />
          </button>
          <h1>{pageTitle(location.pathname)}</h1>
        </div>
      </header>
      <AppDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} user={user} logout={logout} />
      <main className="app-main">
        <Routes>
          <Route path="/" element={<LiveRoastView />} />
          <Route path="/history" element={<HistoryDashboard />} />
          <Route path="/roasts/:id" element={<RoastDetailView />} />
          <Route path="/compare" element={<RoastComparisonView />} />
          <Route path="/settings" element={<SettingsView />} />
          <Route path="/users" element={user.role === "admin" ? <UsersView /> : <Navigate to="/" replace />} />
        </Routes>
      </main>
      <footer className="app-footer no-print">
        v{__APP_VERSION__} · build {__APP_BUILD__}
      </footer>
    </div>
  );
}

// Always mounted (not conditionally rendered) so the open/close transition
// can actually animate -- .open toggles a transform in styles.css. Closes
// itself on Escape, on a backdrop click, and on clicking any link inside
// it (navigating away implies you're done with the menu).
function AppDrawer({ open, onClose, user, logout }) {
  useEffect(() => {
    if (!open) return undefined;
    function onKeyDown(e) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  return (
    <>
      <div className={`app-drawer-backdrop${open ? " open" : ""}`} onClick={onClose} aria-hidden="true" />
      <div className={`app-drawer${open ? " open" : ""}`} aria-hidden={!open}>
        <div className="app-drawer-header">
          <Link to="/" className="app-drawer-brand" onClick={onClose}>
            <img className="app-logo" src="/icon-48x48.png" alt="" width="28" height="28" />
            <span>Roast Telemetry</span>
          </Link>
          <button type="button" className="app-drawer-close" onClick={onClose} aria-label="Close menu">
            ×
          </button>
        </div>
        <nav className="app-drawer-links">
          {NAV_ITEMS.filter((i) => !i.adminOnly || user.role === "admin").map((i) => (
            <NavLink key={i.to} to={i.to} end={i.end} onClick={onClose}>
              {i.label}
            </NavLink>
          ))}
        </nav>
        <div className="app-drawer-theme">
          <ThemePicker />
        </div>
        <div className="app-drawer-user">
          <span className="app-username">{user.username}</span>
          <button type="button" className="logout-button" onClick={logout}>
            Log out
          </button>
        </div>
      </div>
    </>
  );
}
