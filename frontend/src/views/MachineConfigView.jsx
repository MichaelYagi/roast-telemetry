import { useEffect, useState } from "react";
import { api } from "../api/client.js";

export default function MachineConfigView() {
  const [machines, setMachines] = useState([]);
  const [devices, setDevices] = useState([]);
  const [search, setSearch] = useState("");
  const [onlyControlCapable, setOnlyControlCapable] = useState(false);

  useEffect(() => {
    api.listMachines().then(setMachines);
    const load = () => api.listDevices().then(setDevices).catch(() => {});
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const filtered = machines.filter((m) => {
    if (onlyControlCapable && !m.control_capable) return false;
    if (search && !`${m.brand} ${m.model}`.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  return (
    <div className="machines-view">
      <div className="panel">
        <h2>Active Devices</h2>
        {devices.length === 0 ? (
          <p>No devices currently connected. Start a roast to bind one.</p>
        ) : (
          <table className="roast-table">
            <thead>
              <tr>
                <th>Device</th>
                <th>Mode</th>
                <th>Status</th>
                <th>Last error</th>
              </tr>
            </thead>
            <tbody>
              {devices.map((d) => (
                <tr key={d.id}>
                  <td>{d.name}</td>
                  <td>{d.mode}</td>
                  <td>
                    <span className={`status-pill status-${d.status}`}>{d.status}</span>
                  </td>
                  <td>{d.last_error || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <h2>Supported Machine Catalog</h2>
        <p className="hint">
          All machines run through the simulator/mock-device layer -- no physical connection is made.
          Connection type reflects the interface Artisan would normally use with real hardware.
        </p>
        <div className="filters-row">
          <input placeholder="Search brand or model…" value={search} onChange={(e) => setSearch(e.target.value)} />
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={onlyControlCapable}
              onChange={(e) => setOnlyControlCapable(e.target.checked)}
            />
            Control-capable only
          </label>
          <span className="hint" style={{ marginLeft: "auto" }}>
            {filtered.length} machines
          </span>
        </div>
        <table className="roast-table">
          <thead>
            <tr>
              <th>Brand</th>
              <th>Model</th>
              <th>Capabilities</th>
              <th>Connection</th>
              <th>Control</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((m) => (
              <tr key={m.id}>
                <td>{m.brand}</td>
                <td>{m.model}</td>
                <td>{m.capabilities.join(", ")}</td>
                <td>{m.connection_type}</td>
                <td>{m.control_capable ? "✓" : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
