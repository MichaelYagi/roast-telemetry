import { NavLink, Route, Routes } from "react-router-dom";
import ThemePicker from "./components/ThemePicker.jsx";
import HistoryDashboard from "./views/HistoryDashboard.jsx";
import LiveRoastView from "./views/LiveRoastView.jsx";
import RoastComparisonView from "./views/RoastComparisonView.jsx";
import RoastDetailView from "./views/RoastDetailView.jsx";
import SettingsView from "./views/SettingsView.jsx";

export default function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        {/* Split into a wrapper so the *inner* row's width (not the header
            itself) is what gets constrained to match the breakout split --
            see the body.breakout-split-active rules in styles.css. Default
            (non-split) layout is unaffected either way. */}
        <div className="app-header-inner">
          <div className="app-title-group">
            <h1>Roast Telemetry</h1>
            <ThemePicker />
          </div>
          <nav>
            <NavLink to="/" end>
              Live Roast
            </NavLink>
            <NavLink to="/history">History</NavLink>
            <NavLink to="/compare">Compare</NavLink>
            <NavLink to="/settings">Settings</NavLink>
          </nav>
        </div>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<LiveRoastView />} />
          <Route path="/history" element={<HistoryDashboard />} />
          <Route path="/roasts/:id" element={<RoastDetailView />} />
          <Route path="/compare" element={<RoastComparisonView />} />
          <Route path="/settings" element={<SettingsView />} />
        </Routes>
      </main>
    </div>
  );
}
