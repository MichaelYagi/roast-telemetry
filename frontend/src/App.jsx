import { NavLink, Route, Routes } from "react-router-dom";
import HistoryDashboard from "./views/HistoryDashboard.jsx";
import LiveRoastView from "./views/LiveRoastView.jsx";
import MachineConfigView from "./views/MachineConfigView.jsx";
import RoastComparisonView from "./views/RoastComparisonView.jsx";
import RoastDetailView from "./views/RoastDetailView.jsx";

export default function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>Artisan Web Roasting Platform</h1>
        <nav>
          <NavLink to="/" end>
            Live Roast
          </NavLink>
          <NavLink to="/history">History</NavLink>
          <NavLink to="/compare">Compare</NavLink>
          <NavLink to="/machines">Machines</NavLink>
        </nav>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<LiveRoastView />} />
          <Route path="/history" element={<HistoryDashboard />} />
          <Route path="/roasts/:id" element={<RoastDetailView />} />
          <Route path="/compare" element={<RoastComparisonView />} />
          <Route path="/machines" element={<MachineConfigView />} />
        </Routes>
      </main>
    </div>
  );
}
