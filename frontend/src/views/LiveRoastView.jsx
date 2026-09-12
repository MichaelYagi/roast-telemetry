import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { useRoastStream } from "../api/ws.js";
import ArtisanToolbar from "../components/ArtisanToolbar.jsx";
import ConnectionBadge from "../components/ConnectionBadge.jsx";
import ControlPanel from "../components/ControlPanel.jsx";
import EventButtonRow from "../components/EventButtonRow.jsx";
import LiveReadouts from "../components/LiveReadouts.jsx";
import RoastChart from "../components/RoastChart.jsx";

const SAMPLE_ALOG_PATH = "backend/data/sample_roasts/demo_roast.alog";

function formatElapsed(seconds) {
  if (seconds == null) return "00:00";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

const LIVE_MODES = ["artisan_live", "modbus_live", "ms6514_live"];

const STATUS_TEXT = {
  idle: "Configure a roast, then press ON to connect the device.",
  armed: "Device connected. Press START to begin recording.",
  roasting: "Scope recording…",
  cooling: "Cooling…",
  finished: "Roast finished. Press OFF to reset.",
};

export default function LiveRoastView() {
  const [machines, setMachines] = useState([]);
  const [form, setForm] = useState({
    title: "",
    mode: "simulator",
    machine_id: "",
    beans: "",
    weight_green_g: "",
    alog_path: SAMPLE_ALOG_PATH,
    playback_speed: 4,
    artisan_host: "",
    artisan_port: 8080,
    modbus_port: "",
    modbus_baudrate: 57600,
    ms6514_port: "",
    dry_end_c: 160,
    fc_start_c: 196,
  });
  const [phase, setPhase] = useState("idle"); // idle | armed | roasting | cooling | finished
  const [roastId, setRoastId] = useState(null);
  const [noteText, setNoteText] = useState("");
  const [error, setError] = useState(null);

  const { roast, connectionStatus } = useRoastStream(roastId);

  useEffect(() => {
    api.listMachines().then(setMachines).catch(() => setMachines([]));
  }, []);

  useEffect(() => {
    if (roast?.status === "complete" || roast?.status === "aborted") {
      setPhase("finished");
    } else if (roast?.status === "cooling") {
      setPhase("cooling");
    }
  }, [roast?.status]);

  function handleReset() {
    setRoastId(null);
    setPhase("idle");
    setError(null);
    setForm((f) => ({ ...f, title: "" }));
  }

  async function handleToggleConnect() {
    setError(null);
    if (phase === "idle") {
      setPhase("armed");
    } else if (phase === "armed") {
      setPhase("idle");
    } else if (phase === "roasting" || phase === "cooling") {
      await api.stopRoast(roastId);
    } else if (phase === "finished") {
      handleReset();
    }
  }

  async function handleStart() {
    if (phase !== "armed") return;
    setError(null);
    try {
      const payload = {
        title: form.title || `Roast ${new Date().toLocaleString()}`,
        mode: form.mode,
        machine_id: form.machine_id || null,
        beans: form.beans || null,
        weight_green_g: form.weight_green_g ? Number(form.weight_green_g) : null,
        sample_interval_s: 1.0,
      };
      if (form.mode === "alog_playback") {
        payload.alog_path = form.alog_path;
        payload.playback_speed = Number(form.playback_speed) || 1;
      }
      if (form.mode === "artisan_live") {
        payload.artisan_host = form.artisan_host;
        payload.artisan_port = Number(form.artisan_port) || 8080;
      }
      if (form.mode === "modbus_live") {
        payload.modbus_port = form.modbus_port;
        payload.modbus_baudrate = Number(form.modbus_baudrate) || 57600;
      }
      if (form.mode === "ms6514_live") {
        payload.ms6514_port = form.ms6514_port;
      }
      if (LIVE_MODES.includes(form.mode)) {
        payload.dry_end_c = form.dry_end_c === "" ? null : Number(form.dry_end_c);
        payload.fc_start_c = form.fc_start_c === "" ? null : Number(form.fc_start_c);
      }
      const summary = await api.createRoast(payload);
      setRoastId(summary.id);
      setPhase("roasting");
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleCommand(partial) {
    if (!roastId) return;
    api.sendCommand(roastId, partial).catch((err) => setError(err.message));
  }

  async function handleFireEvent(type, label) {
    if (!roastId) return;
    const latest = roast?.profile?.[roast.profile.length - 1];
    api.addEvent(roastId, { type, label, value: latest?.bt ?? null }).catch((err) => setError(err.message));
  }

  async function handleAddNote() {
    if (!roastId || !noteText.trim()) return;
    await api.addNote(roastId, { text: noteText.trim() });
    setNoteText("");
  }

  const isActive = roast && (roast.status === "roasting" || roast.status === "cooling");
  const latest = roast?.profile?.[roast.profile.length - 1];
  const elapsedLabel = formatElapsed(latest?.time_s);

  const dryEndEvent = roast?.events?.find((e) => e.type === "DRY_END");
  const fcStartEvent = roast?.events?.find((e) => e.type === "FC_START");
  const milestones = {
    dryPercent:
      dryEndEvent && latest?.time_s ? `${((dryEndEvent.time_s / latest.time_s) * 100).toFixed(1)}%` : "---",
    dryTime: dryEndEvent ? formatElapsed(dryEndEvent.time_s) : "--:--",
    fcsTime: fcStartEvent ? formatElapsed(fcStartEvent.time_s) : "--:--",
  };

  return (
    <div className="live-view">
      <ArtisanToolbar
        phase={phase}
        elapsedLabel={elapsedLabel}
        statusText={STATUS_TEXT[phase]}
        milestones={milestones}
        onToggleConnect={handleToggleConnect}
        onStart={handleStart}
      />

      {error && <p className="error panel">{error}</p>}

      {phase === "idle" && (
        <form
          className="panel roast-form"
          onSubmit={(e) => {
            e.preventDefault();
            handleToggleConnect();
          }}
        >
          <h2>Configure Roast</h2>
          <div className="form-row">
            <label>
              Title
              <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </label>
            <label>
              Data source
              <select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })}>
                <option value="simulator">Artisan Simulator</option>
                <option value="alog_playback">.alog Playback</option>
                <option value="artisan_live">Artisan Live Bridge</option>
                <option value="modbus_live">Direct Modbus (FZ94 EVO, USB)</option>
                <option value="ms6514_live">Direct USB (Mastech MS6514)</option>
              </select>
            </label>
          </div>
          <div className="form-row">
            <label>
              Machine
              <select value={form.machine_id} onChange={(e) => setForm({ ...form, machine_id: e.target.value })}>
                <option value="">(unspecified)</option>
                {machines.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.brand} {m.model} {m.control_capable ? "· controllable" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Beans
              <input value={form.beans} onChange={(e) => setForm({ ...form, beans: e.target.value })} />
            </label>
            <label>
              Green weight (g)
              <input
                type="number"
                value={form.weight_green_g}
                onChange={(e) => setForm({ ...form, weight_green_g: e.target.value })}
              />
            </label>
          </div>
          {form.mode === "alog_playback" && (
            <div className="form-row">
              <label>
                .alog file path (server-side)
                <input value={form.alog_path} onChange={(e) => setForm({ ...form, alog_path: e.target.value })} />
              </label>
              <label>
                Playback speed
                <input
                  type="number"
                  step="0.5"
                  min="0.5"
                  value={form.playback_speed}
                  onChange={(e) => setForm({ ...form, playback_speed: e.target.value })}
                />
              </label>
            </div>
          )}
          {form.mode === "artisan_live" && (
            <div className="form-row">
              <label>
                Artisan host / IP
                <input
                  placeholder="192.168.1.50"
                  value={form.artisan_host}
                  onChange={(e) => setForm({ ...form, artisan_host: e.target.value })}
                />
              </label>
              <label>
                WebLCDs port
                <input
                  type="number"
                  value={form.artisan_port}
                  onChange={(e) => setForm({ ...form, artisan_port: e.target.value })}
                />
              </label>
              <p className="hint">
                On the machine running Artisan and connected to the real roaster: Config → Curves → UI tab →
                enable WebLCDs on this port. This mirrors BT/ET/RoR live and auto-detects Charge/Turning Point
                from the BT curve. Drop and Cool End are judgment calls, not thresholds — mark those yourself
                with the buttons below, like a real roaster would. This can't control the roaster.
              </p>
            </div>
          )}
          {form.mode === "modbus_live" && (
            <div className="form-row">
              <label>
                Serial port
                <input
                  placeholder="COM3"
                  value={form.modbus_port}
                  onChange={(e) => setForm({ ...form, modbus_port: e.target.value })}
                />
              </label>
              <label>
                Baud rate
                <input
                  type="number"
                  value={form.modbus_baudrate}
                  onChange={(e) => setForm({ ...form, modbus_baudrate: e.target.value })}
                />
              </label>
              <p className="hint">
                Direct Modbus RTU to the FZ94 EVO over USB — bypasses Artisan entirely. Register addresses are
                Coffee-Tech's own, from Artisan's FZ94 EVO preset. This can control the roaster (Burner/Air/Drum
                below) — mutually exclusive with Artisan also connected to this same port. Not tested against
                real FZ94 EVO hardware; confirm which slider moves which physical control before roasting with it.
              </p>
            </div>
          )}
          {form.mode === "ms6514_live" && (
            <div className="form-row">
              <label>
                Serial port
                <input
                  placeholder="COM5"
                  value={form.ms6514_port}
                  onChange={(e) => setForm({ ...form, ms6514_port: e.target.value })}
                />
              </label>
              <p className="hint">
                Direct USB read of the Mastech MS6514 — bypasses Artisan entirely (no Artisan needed at all for
                this mode). Read-only. T1 → BT, T2 → ET. Keep the meter's display set to "T1" or "T2" (not
                "T1-T2") for reliable dual-channel reading. Charge/Turning Point auto-detect from BT; Dry
                End/FC Start trigger at your set thresholds. Mark Drop and Cool End yourself when you make the call.
              </p>
            </div>
          )}
          {LIVE_MODES.includes(form.mode) && (
            <div className="form-row">
              <label>
                Dry End BT threshold (°C, blank to disable)
                <input
                  type="number"
                  value={form.dry_end_c}
                  onChange={(e) => setForm({ ...form, dry_end_c: e.target.value })}
                />
              </label>
              <label>
                FC Start BT threshold (°C, blank to disable)
                <input
                  type="number"
                  value={form.fc_start_c}
                  onChange={(e) => setForm({ ...form, fc_start_c: e.target.value })}
                />
              </label>
            </div>
          )}
          <button type="submit">ON — Connect Device</button>
        </form>
      )}

      {phase !== "idle" && (
        <div className="live-roast">
          <div className="panel scope-panel">
            <div className="scope-body">
              <div className="scope-chart">
                <RoastChart profile={roast?.profile || []} events={roast?.events || []} title={null} />
              </div>
              <LiveReadouts latest={latest} />
            </div>
            <EventButtonRow disabled={!isActive} onFire={handleFireEvent} />
          </div>

          {roast && (
            <div className="live-header panel">
              <div>
                <h2>{roast.title}</h2>
                <p className="sub">
                  {roast.mode} · status: <strong>{roast.status}</strong>
                </p>
              </div>
              <div className="live-header-actions">
                <ConnectionBadge status={connectionStatus} />
                {phase === "finished" && <Link to={`/roasts/${roastId}`}>View detail</Link>}
              </div>
            </div>
          )}

          <div className="live-grid">
            {(form.mode === "simulator" || form.mode === "modbus_live") && (
              <ControlPanel disabled={!isActive} onSend={handleCommand} />
            )}
            {form.mode === "alog_playback" && (
              <div className="panel control-panel">
                <h3>Playback Speed</h3>
                <input
                  type="range"
                  min="0.5"
                  max="20"
                  step="0.5"
                  defaultValue={form.playback_speed}
                  disabled={!isActive}
                  onChange={(e) => handleCommand({ speed: Number(e.target.value) })}
                />
              </div>
            )}
            {form.mode === "artisan_live" && (
              <div className="panel control-panel">
                <h3>Artisan Live Bridge</h3>
                <p className="hint">
                  Mirroring {form.artisan_host}:{form.artisan_port}. Read-only — no control commands go back to
                  the real roaster. Charge and Turning Point auto-detect from the BT curve; Dry End/FC Start
                  trigger at the thresholds you set. Mark Drop and Cool End yourself below when you make the call.
                </p>
              </div>
            )}
            {form.mode === "modbus_live" && (
              <p className="hint" style={{ gridColumn: "1 / -1" }}>
                Controls above write directly to the FZ94 EVO over {form.modbus_port || "the serial port"}:
                Heater → Burner (register 35, 30–100), Fan → Air (register 20, 30–70), Drum Speed → Drum
                (register 16, 30–70) — out-of-range values are clamped to the machine's valid range. Charge and
                Turning Point auto-detect from BT; Dry End/FC Start trigger at your set thresholds. Mark Drop
                and Cool End yourself when you make the call.
              </p>
            )}
            {form.mode === "ms6514_live" && (
              <div className="panel control-panel">
                <h3>Mastech MS6514</h3>
                <p className="hint">
                  Reading {form.ms6514_port || "the serial port"} directly — no Artisan needed. Read-only,
                  this meter has no command to control anything. Charge and Turning Point auto-detect from BT;
                  Dry End/FC Start trigger at your set thresholds. Mark Drop and Cool End yourself below.
                </p>
              </div>
            )}
            <div className="panel">
              <h3>Events</h3>
              <ul className="event-feed">
                {(roast?.events || [])
                  .slice()
                  .reverse()
                  .map((ev) => (
                    <li key={ev.id}>
                      <strong>{ev.label}</strong> @ {formatElapsed(ev.time_s)} ({ev.value != null ? ev.value.toFixed(1) : "--"}°C)
                    </li>
                  ))}
              </ul>
            </div>

            <div className="panel">
              <h3>Notes</h3>
              <div className="note-input">
                <input
                  placeholder="Add a note…"
                  value={noteText}
                  onChange={(e) => setNoteText(e.target.value)}
                  disabled={!isActive}
                />
                <button onClick={handleAddNote} disabled={!isActive}>
                  Add
                </button>
              </div>
              <ul className="note-feed">
                {(roast?.notes || []).map((n) => (
                  <li key={n.id}>{n.text}</li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
