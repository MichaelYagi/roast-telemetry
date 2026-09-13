import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { useRoastStream } from "../api/ws.js";
import ArtisanToolbar from "../components/ArtisanToolbar.jsx";
import BreakoutPanel from "../components/BreakoutPanel.jsx";
import ConnectionBadge from "../components/ConnectionBadge.jsx";
import ControlPanel from "../components/ControlPanel.jsx";
import EventButtonRow from "../components/EventButtonRow.jsx";
import LiveReadouts from "../components/LiveReadouts.jsx";
import RoastChart from "../components/RoastChart.jsx";

const SETTINGS_POLL_MS = 5000;

const SAMPLE_ALOG_PATH = "backend/data/sample_roasts/demo_roast.alog";

function formatElapsed(seconds) {
  if (seconds == null) return "00:00";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

const LIVE_MODES = ["artisan_live", "modbus_live", "ms6514_live"];
const CONTROLLABLE_MODES = ["simulator", "modbus_live"];

// simulator/engine.py's SimulatorConfig defaults -- that engine has no
// per-roast override for these (RoastSession always constructs a plain
// SimulatorEngine()), so unlike the live-hardware modes, simulator's
// thresholds aren't something the form even offers to configure.
const SIMULATOR_DRY_END_C = 160.0;
const SIMULATOR_FC_START_C = 196.0;

// Purely informational estimate -- linear extrapolation from the current
// BT and its trailing-window RoR, nothing more. Not shown once the real
// event has already fired (that's an actual timestamp, not a guess), and
// intentionally conservative: no estimate at all (rather than a wildly
// wrong one) when RoR is flat/negative or already past the threshold.
function estimateEtaSeconds(currentBt, currentRorPerMin, thresholdC) {
  if (currentBt == null || currentRorPerMin == null || thresholdC == null) return null;
  if (currentBt >= thresholdC || currentRorPerMin <= 0) return null;
  return ((thresholdC - currentBt) / currentRorPerMin) * 60;
}

// Below this viewport width, the split-pane layout doesn't have enough
// room to be worthwhile -- falls back to the plain in-flow sidebar
// (.live-roast-layout) instead, same as before any of this existed.
const BREAKOUT_SPLIT_MIN_VIEWPORT = 1400;
const SPLIT_MIN_MAIN_WIDTH = 560; // floor for the chart/controls pane
const SPLIT_MIN_PANEL_WIDTH = 200; // floor for the breakout panel pane
const SPLIT_DIVIDER_WIDTH = 8 + 2 * 0.4 * 16; // .breakout-split-divider's own width + its 0.4rem margins
const SPLIT_WIDTH_STORAGE_KEY = "roast-telemetry:breakoutSplitWidth";

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
    modbus_baudrate: 19200,
    modbus_control_port: "",
    modbus_control_baudrate: 19200,
    ms6514_port: "",
    dry_end_c: 160,
    fc_start_c: 196,
    heater_pct: 70,
    fan_pct: 20,
    drum_speed_pct: 50,
  });
  const [phase, setPhase] = useState("idle"); // idle | armed | roasting | cooling | finished
  const [roastId, setRoastId] = useState(null);
  const [noteText, setNoteText] = useState("");
  const [error, setError] = useState(null);
  const [presets, setPresets] = useState([]);
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [presetName, setPresetName] = useState("");
  const [presetFeedback, setPresetFeedback] = useState(null);
  const [brokenOutPanels, setBrokenOutPanels] = useState([]); // ordered array, matches Settings' display order
  const [viewportWide, setViewportWide] = useState(
    () => typeof window !== "undefined" && window.innerWidth >= BREAKOUT_SPLIT_MIN_VIEWPORT
  );
  const [splitWidth, setSplitWidth] = useState(() => {
    const saved = typeof window !== "undefined" && Number(localStorage.getItem(SPLIT_WIDTH_STORAGE_KEY));
    return saved > 0 ? saved : 780;
  });
  const dragStateRef = useRef(null);

  // Must be computed here, before the effects below that depend on them --
  // they used to live much further down near the render return, which put
  // their `const` declarations after the useEffect calls that reference
  // them in dependency arrays, and a dependency array is evaluated
  // immediately (not deferred like the effect body), so that ordering threw
  // "Cannot access before initialization" (TDZ) on every render.
  const showBreakoutPanel = phase !== "idle" && brokenOutPanels.length > 0;
  const showSplitLayout = showBreakoutPanel && viewportWide;

  const { roast, connectionStatus } = useRoastStream(roastId);

  useEffect(() => {
    function onResize() {
      setViewportWide(window.innerWidth >= BREAKOUT_SPLIT_MIN_VIEWPORT);
      // Re-clamp in case a split chosen on a wider screen would now
      // overflow/squeeze the panel pane at this narrower (but still
      // above-threshold) width -- 40px is .breakout-split-escape's own
      // left+right padding, an estimate rather than a live measurement.
      setSplitWidth((w) => clampSplitWidth(w, window.innerWidth - 40));
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Bridges split-pane state to App.jsx's header, which this route has no
  // prop/context connection to (see the body.breakout-split-active rules
  // in styles.css) -- so the header can align itself with the content
  // split without lifting this state up or adding Context for one value.
  useEffect(() => {
    document.body.classList.toggle("breakout-split-active", showSplitLayout);
    return () => document.body.classList.remove("breakout-split-active");
  }, [showSplitLayout]);

  useEffect(() => {
    document.documentElement.style.setProperty("--breakout-split-width", `${splitWidth}px`);
  }, [splitWidth]);

  // The main/panel columns need a hard pixel height to fill exactly --
  // guessing "100vh minus some constant" was what let the panel overflow
  // the viewport, since the header's real rendered height isn't a fixed
  // number (it can change with content or zoom). Measure it directly and
  // publish it as a CSS var so both columns size to precisely what's left
  // of the viewport, with no page-level scrollbar.
  //
  // This also measures and publishes the true available width, replacing
  // the `100vw` used by the "escape .app-shell's max-width" trick in
  // styles.css. `100vw` is the raw window width -- it does NOT subtract a
  // vertical scrollbar's width, while the normal centered layout it needs
  // to match (.app-shell's `margin: 0 auto`) is centered against
  // `document.documentElement.clientWidth`, which DOES subtract it. Any
  // mismatch between those two throws the trick's centering off by half
  // the scrollbar's width on each side, which is exactly a page-level
  // horizontal scrollbar that "scrolls a short distance". clientWidth is
  // what the layout is actually centered against, so it's the only value
  // that keeps this exact regardless of whether a scrollbar is present.
  useEffect(() => {
    if (!showSplitLayout) return undefined;
    const headerEl = document.querySelector(".app-header");
    function updateAvailableSpace() {
      const headerHeight = headerEl ? headerEl.getBoundingClientRect().height : 0;
      document.documentElement.style.setProperty(
        "--breakout-available-height",
        `${Math.max(window.innerHeight - headerHeight, 200)}px`
      );
      document.documentElement.style.setProperty(
        "--breakout-viewport-width",
        `${document.documentElement.clientWidth}px`
      );
    }
    updateAvailableSpace();
    window.addEventListener("resize", updateAvailableSpace);
    let observer;
    if (headerEl && typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(updateAvailableSpace);
      observer.observe(headerEl);
    }
    return () => {
      window.removeEventListener("resize", updateAvailableSpace);
      observer?.disconnect();
    };
  }, [showSplitLayout]);

  function clampSplitWidth(width, containerWidth) {
    const maxMain = containerWidth - SPLIT_MIN_PANEL_WIDTH - SPLIT_DIVIDER_WIDTH;
    return Math.min(Math.max(width, SPLIT_MIN_MAIN_WIDTH), Math.max(SPLIT_MIN_MAIN_WIDTH, maxMain));
  }

  function handleDividerPointerDown(e) {
    const row = e.currentTarget.parentElement;
    dragStateRef.current = { containerWidth: row.getBoundingClientRect().width };
    e.currentTarget.classList.add("dragging");
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function handleDividerPointerMove(e) {
    if (!dragStateRef.current) return;
    const row = e.currentTarget.parentElement;
    const rowLeft = row.getBoundingClientRect().left;
    const next = clampSplitWidth(e.clientX - rowLeft, dragStateRef.current.containerWidth);
    setSplitWidth(next);
  }

  function handleDividerPointerUp(e) {
    if (!dragStateRef.current) return;
    dragStateRef.current = null;
    e.currentTarget.classList.remove("dragging");
    e.currentTarget.releasePointerCapture(e.pointerId);
    setSplitWidth((w) => {
      localStorage.setItem(SPLIT_WIDTH_STORAGE_KEY, String(w));
      return w;
    });
  }

  useEffect(() => {
    api.listMachines().then(setMachines).catch(() => setMachines([]));
    refreshPresets();
    reconnectActiveRoast();
  }, []);

  // Hot-applies Settings > Big Readout Panel changes to an already-open
  // tab -- same lightweight polling pattern MachineConfigView already
  // uses for its device list, rather than a dedicated push channel for
  // what's a rarely-changed, low-stakes display preference.
  useEffect(() => {
    const load = () =>
      api
        .getSettings()
        .then((s) => setBrokenOutPanels(s.broken_out_panels || []))
        .catch(() => {});
    load();
    const interval = setInterval(load, SETTINGS_POLL_MS);
    return () => clearInterval(interval);
  }, []);

  // A roast keeps running server-side across a page refresh -- the
  // in-memory session and its background loop don't stop just because
  // this tab reloaded. Without this, refreshing mid-roast would silently
  // drop you back to an empty Configure Roast form with no way back to
  // the live view short of visiting History and there's no "resume"
  // action there either. Check for one still-active roast (roasting or
  // cooling) and reattach to it instead.
  async function reconnectActiveRoast() {
    try {
      const [roasting, cooling] = await Promise.all([
        api.listRoasts({ status: "roasting", limit: 1 }),
        api.listRoasts({ status: "cooling", limit: 1 }),
      ]);
      const active = roasting[0] || cooling[0];
      if (active) {
        setForm((f) => ({ ...f, mode: active.mode }));
        setRoastId(active.id);
        setPhase(active.status); // "roasting" | "cooling"
      }
    } catch {
      // No backend reachable yet, or nothing active -- fall back to the
      // normal idle Configure Roast flow.
    }
  }

  function refreshPresets() {
    api.listPresets().then(setPresets).catch(() => setPresets([]));
  }

  useEffect(() => {
    if (["complete", "stopped", "aborted"].includes(roast?.status)) {
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

  function buildConfigFromForm() {
    const payload = {
      title: form.title,
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
      payload.modbus_baudrate = Number(form.modbus_baudrate) || 19200;
      payload.modbus_control_port = form.modbus_control_port || null;
      payload.modbus_control_baudrate = Number(form.modbus_control_baudrate) || 19200;
    }
    if (form.mode === "ms6514_live") {
      payload.ms6514_port = form.ms6514_port;
    }
    if (LIVE_MODES.includes(form.mode)) {
      payload.dry_end_c = form.dry_end_c === "" ? null : Number(form.dry_end_c);
      payload.fc_start_c = form.fc_start_c === "" ? null : Number(form.fc_start_c);
    }
    return payload;
  }

  function buildControlsFromForm() {
    if (!CONTROLLABLE_MODES.includes(form.mode)) return {};
    return {
      heater_pct: Number(form.heater_pct),
      fan_pct: Number(form.fan_pct),
      drum_speed_pct: Number(form.drum_speed_pct),
    };
  }

  async function handleStart() {
    if (phase !== "armed") return;
    setError(null);
    try {
      const payload = { ...buildConfigFromForm(), title: form.title || `Roast ${new Date().toLocaleString()}` };
      const summary = await api.createRoast(payload);
      setRoastId(summary.id);
      setPhase("roasting");
      const controls = buildControlsFromForm();
      if (Object.keys(controls).length) {
        api.sendCommand(summary.id, controls).catch((err) => setError(err.message));
      }
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleSavePreset() {
    if (!presetName.trim()) return;
    setError(null);
    setPresetFeedback(null);
    try {
      const trimmed = presetName.trim();
      await api.savePreset(trimmed, buildConfigFromForm(), buildControlsFromForm());
      setPresetName("");
      setSelectedPresetId("");
      setPresetFeedback(`Saved "${trimmed}" as a new config.`);
      refreshPresets();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleUpdatePreset() {
    if (!selectedPresetId || !presetName.trim()) return;
    setError(null);
    setPresetFeedback(null);
    try {
      const trimmed = presetName.trim();
      await api.updatePreset(selectedPresetId, trimmed, buildConfigFromForm(), buildControlsFromForm());
      setPresetFeedback(`Updated "${trimmed}".`);
      refreshPresets();
    } catch (err) {
      setError(err.message);
    }
  }

  function handleLoadPreset(presetId) {
    setSelectedPresetId(presetId);
    setPresetFeedback(null);
    const preset = presets.find((p) => p.id === presetId);
    if (!preset) {
      setPresetName("");
      return;
    }
    setPresetName(preset.name);
    const c = preset.config;
    setForm((f) => ({
      ...f,
      title: c.title || "",
      mode: c.mode,
      machine_id: c.machine_id || "",
      beans: c.beans || "",
      weight_green_g: c.weight_green_g ?? "",
      alog_path: c.alog_path || f.alog_path,
      playback_speed: c.playback_speed ?? f.playback_speed,
      artisan_host: c.artisan_host || "",
      artisan_port: c.artisan_port ?? f.artisan_port,
      modbus_port: c.modbus_port || "",
      modbus_baudrate: c.modbus_baudrate ?? f.modbus_baudrate,
      modbus_control_port: c.modbus_control_port || "",
      modbus_control_baudrate: c.modbus_control_baudrate ?? f.modbus_control_baudrate,
      ms6514_port: c.ms6514_port || "",
      dry_end_c: c.dry_end_c ?? "",
      fc_start_c: c.fc_start_c ?? "",
      heater_pct: preset.heater_pct ?? f.heater_pct,
      fan_pct: preset.fan_pct ?? f.fan_pct,
      drum_speed_pct: preset.drum_speed_pct ?? f.drum_speed_pct,
    }));
  }

  async function handleDeletePreset(presetId) {
    setError(null);
    try {
      const name = presets.find((p) => p.id === presetId)?.name;
      await api.deletePreset(presetId);
      if (selectedPresetId === presetId) {
        setSelectedPresetId("");
        setPresetName("");
      }
      setPresetFeedback(name ? `Deleted "${name}".` : "Deleted.");
      refreshPresets();
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

  // Once a roast exists (including one reattached after a page refresh --
  // see the reconnect effect below), it's the source of truth for which
  // mode-specific controls/hints to show, not the Configure Roast form's
  // local state, which resets to its defaults on every page load.
  const activeMode = roast?.mode || form.mode;

  const dryEndEvent = roast?.events?.find((e) => e.type === "DRY_END");
  const fcStartEvent = roast?.events?.find((e) => e.type === "FC_START");

  // Thresholds only exist as a concept for simulator + the live-hardware
  // modes (auto-detected from a BT threshold); alog_playback just replays
  // whatever events the file already has, so there's nothing to estimate.
  const dryEndThreshold =
    activeMode === "simulator" ? SIMULATOR_DRY_END_C : LIVE_MODES.includes(activeMode) ? Number(form.dry_end_c) || null : null;
  const fcStartThreshold =
    activeMode === "simulator" ? SIMULATOR_FC_START_C : LIVE_MODES.includes(activeMode) ? Number(form.fc_start_c) || null : null;

  const dryEndEtaS = !dryEndEvent && isActive ? estimateEtaSeconds(latest?.bt, latest?.ror_bt, dryEndThreshold) : null;
  const fcStartEtaS = !fcStartEvent && isActive ? estimateEtaSeconds(latest?.bt, latest?.ror_bt, fcStartThreshold) : null;

  const milestones = {
    dryPercent:
      dryEndEvent && latest?.time_s ? `${((dryEndEvent.time_s / latest.time_s) * 100).toFixed(1)}%` : "---",
    dryTime: dryEndEvent
      ? formatElapsed(dryEndEvent.time_s)
      : dryEndEtaS != null
        ? `~${formatElapsed(latest.time_s + dryEndEtaS)}`
        : "--:--",
    fcsTime: fcStartEvent
      ? formatElapsed(fcStartEvent.time_s)
      : fcStartEtaS != null
        ? `~${formatElapsed(latest.time_s + fcStartEtaS)}`
        : "--:--",
  };

  // The Configure Roast form (with its "Load saved config" dropdown) only
  // renders while phase === "idle" -- once ON is pressed it disappears,
  // taking the only visible indication of which preset was loaded with
  // it, from then until a roast object exists (whose live-header panel
  // shows roast.title/beans/etc). presetName isn't cleared by
  // handleToggleConnect, so it's still accurate here.
  const presetHint = presetName && (phase === "idle" || phase === "armed") ? ` Loaded config: "${presetName}".` : "";
  const toolbarElement = (
    <ArtisanToolbar
      phase={phase}
      elapsedLabel={elapsedLabel}
      statusText={STATUS_TEXT[phase] + presetHint}
      milestones={milestones}
      onToggleConnect={handleToggleConnect}
      onStart={handleStart}
    />
  );

  return (
    <div className="live-view">
      {/* In split-pane mode the toolbar moves inside the left (.live-roast)
          column instead of sitting full-width above everything, so the
          divider between it and the breakout panel runs the full height
          of the page, not just alongside the chart. */}
      {!showSplitLayout && toolbarElement}

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
          {presets.length > 0 && (
            <div className="form-row">
              <label>
                Load saved config
                <select value={selectedPresetId} onChange={(e) => handleLoadPreset(e.target.value)}>
                  <option value="">(none)</option>
                  {presets.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} — {p.config.mode}
                    </option>
                  ))}
                </select>
              </label>
              {selectedPresetId && (
                <button
                  type="button"
                  className="danger"
                  style={{ alignSelf: "flex-end" }}
                  onClick={() => handleDeletePreset(selectedPresetId)}
                >
                  Delete selected config
                </button>
              )}
            </div>
          )}
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
                <option value="modbus_live">Direct Modbus (FZ-94, USB)</option>
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
              <label>
                Separate drive port — optional, uncommon
                <input
                  placeholder="only if your own wiring needs a 2nd connection for Air/Drum"
                  value={form.modbus_control_port}
                  onChange={(e) => setForm({ ...form, modbus_control_port: e.target.value })}
                />
              </label>
              <label>
                Drive baud rate
                <input
                  type="number"
                  value={form.modbus_control_baudrate}
                  onChange={(e) => setForm({ ...form, modbus_control_baudrate: e.target.value })}
                />
              </label>
              <p className="hint">
                Direct Modbus RTU to the FZ-94 over USB (not the EVO, which is network/Ethernet) — bypasses
                Artisan entirely. One connection handles BT/ET/DT/Burner (a drum-temp setpoint, not a power %)
                and Air/Drum together, matching Artisan's own shipped preset for this machine (19200 baud,
                8N2) — the "separate drive port" field only matters if your own wiring genuinely needs a
                second connection, which is uncommon; leave it blank otherwise. Mutually exclusive with
                Artisan also connected to the same port(s). Not tested against real FZ-94 hardware; the
                BT/ET/DT/Burner numbers and the single-connection setup are confirmed against Artisan's own
                shipped machine preset and source code; Air/Drum register numbers are only blog-sourced.
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
          {CONTROLLABLE_MODES.includes(form.mode) && (
            <div className="form-row">
              <label>
                Heater % at start
                <input
                  type="number" min="0" max="100"
                  value={form.heater_pct}
                  onChange={(e) => setForm({ ...form, heater_pct: e.target.value })}
                />
              </label>
              <label>
                Fan % at start
                <input
                  type="number" min="0" max="100"
                  value={form.fan_pct}
                  onChange={(e) => setForm({ ...form, fan_pct: e.target.value })}
                />
              </label>
              <label>
                Drum % at start
                <input
                  type="number" min="0" max="100"
                  value={form.drum_speed_pct}
                  onChange={(e) => setForm({ ...form, drum_speed_pct: e.target.value })}
                />
              </label>
              <p className="hint" style={{ flexBasis: "100%" }}>
                Sent as the roast's first command right after START, and used as the Controls panel's starting position.
              </p>
            </div>
          )}
          <div className="form-row save-preset-row">
            <label>
              {selectedPresetId ? "Config name" : "Save this configuration as"}
              <input
                placeholder="e.g. Fake FZ94 test rig"
                value={presetName}
                onChange={(e) => {
                  setPresetName(e.target.value);
                  setPresetFeedback(null);
                }}
              />
            </label>
            {selectedPresetId && (
              <button type="button" onClick={handleUpdatePreset} disabled={!presetName.trim()}>
                Update "{presets.find((p) => p.id === selectedPresetId)?.name}"
              </button>
            )}
            <button type="button" onClick={handleSavePreset} disabled={!presetName.trim()}>
              Save as new config
            </button>
          </div>
          {presetFeedback && <p className="hint preset-feedback">{presetFeedback}</p>}
          {/* No visible submit button here -- the toolbar's ON/OFF toggle above
              is the single control for this action. The form keeps onSubmit
              so pressing Enter in a field still arms it. */}
          <p className="hint">Press ON above when you're ready to connect.</p>
          <button type="submit" hidden />
        </form>
      )}

      {phase !== "idle" && (
        <div className={showSplitLayout ? "breakout-split-escape" : undefined}>
        <div className={showSplitLayout ? "breakout-split-row" : "live-roast-layout"}>
        <div className="live-roast" style={showSplitLayout ? { width: splitWidth } : undefined}>
          {showSplitLayout && toolbarElement}
          <div className="panel scope-panel">
            <div className="scope-body">
              <div className="scope-chart">
                <RoastChart profile={roast?.profile || []} events={roast?.events || []} title={null} />
              </div>
              <LiveReadouts latest={latest} />
            </div>
            <EventButtonRow disabled={!isActive} events={roast?.events || []} onFire={handleFireEvent} />
          </div>

          {roast && (
            <div className="live-header panel">
              <div>
                <h2>{roast.title}</h2>
                <p className="sub">
                  {roast.mode} · status: <strong>{roast.status}</strong>
                </p>
                <ul className="live-meta">
                  {roast.mode === "alog_playback" && roast.source_alog_path && (
                    <li>
                      <span className="meta-label">Source file</span>
                      <span className="meta-value">{roast.source_alog_path}</span>
                    </li>
                  )}
                  {roast.mode === "alog_playback" && roast.playback_speed != null && (
                    <li>
                      <span className="meta-label">Playback speed</span>
                      <span className="meta-value">{roast.playback_speed}x</span>
                    </li>
                  )}
                  {roast.beans && (
                    <li>
                      <span className="meta-label">Beans</span>
                      <span className="meta-value">{roast.beans}</span>
                    </li>
                  )}
                  {roast.weight_green_g != null && (
                    <li>
                      <span className="meta-label">Green weight</span>
                      <span className="meta-value">{roast.weight_green_g} g</span>
                    </li>
                  )}
                  {roast.machine_label && (
                    <li>
                      <span className="meta-label">Machine</span>
                      <span className="meta-value">{roast.machine_label}</span>
                    </li>
                  )}
                </ul>
              </div>
              <div className="live-header-actions">
                <ConnectionBadge status={connectionStatus} />
                {phase === "finished" && <Link to={`/roasts/${roastId}`}>View detail</Link>}
              </div>
            </div>
          )}

          <div className="live-grid">
            {CONTROLLABLE_MODES.includes(activeMode) && (
              <ControlPanel
                disabled={!isActive}
                onSend={handleCommand}
                initial={{
                  heater_pct: latest?.heater_pct ?? Number(form.heater_pct),
                  fan_pct: latest?.fan_pct ?? Number(form.fan_pct),
                  drum_speed_pct: latest?.drum_speed_pct ?? Number(form.drum_speed_pct),
                }}
              />
            )}
            {activeMode === "alog_playback" && (
              <div className="panel control-panel">
                <h3>Playback Speed</h3>
                <input
                  type="range"
                  min="0.5"
                  max="20"
                  step="0.5"
                  defaultValue={roast?.playback_speed ?? form.playback_speed}
                  disabled={!isActive}
                  onChange={(e) => handleCommand({ speed: Number(e.target.value) })}
                />
              </div>
            )}
            {activeMode === "artisan_live" && (
              <div className="panel control-panel">
                <h3>Artisan Live Bridge</h3>
                <p className="hint">
                  Mirroring {form.artisan_host || "the configured host"}:{form.artisan_port}. Read-only — no
                  control commands go back to the real roaster. Charge and Turning Point auto-detect from the BT
                  curve; Dry End/FC Start trigger at the thresholds you set. Mark Drop and Cool End yourself
                  below when you make the call.
                </p>
              </div>
            )}
            {activeMode === "modbus_live" && (
              <p className="hint" style={{ gridColumn: "1 / -1" }}>
                Controls above write directly to the FZ-94 over {form.modbus_port || "the serial port"}:
                Heater → Burner (register 35, 30–100), Fan → Air (register 20, 30–70), Drum Speed → Drum
                (register 16, 30–70) — out-of-range values are clamped to the machine's valid range. Charge and
                Turning Point auto-detect from BT; Dry End/FC Start trigger at your set thresholds. Mark Drop
                and Cool End yourself when you make the call.
              </p>
            )}
            {activeMode === "ms6514_live" && (
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
                <textarea
                  placeholder="Add a note…"
                  rows={3}
                  value={noteText}
                  onChange={(e) => setNoteText(e.target.value)}
                  disabled={!isActive}
                />
                <button onClick={handleAddNote} disabled={!isActive || !noteText.trim()}>
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
        {showSplitLayout && (
          <div
            className="breakout-split-divider"
            title="Drag to resize"
            onPointerDown={handleDividerPointerDown}
            onPointerMove={handleDividerPointerMove}
            onPointerUp={handleDividerPointerUp}
          />
        )}
        {showSplitLayout ? (
          <div className="breakout-split-panel-pane">
            <BreakoutPanel
              enabledKeys={brokenOutPanels}
              latest={latest}
              milestones={milestones}
              elapsedLabel={elapsedLabel}
              roast={roast}
            />
          </div>
        ) : (
          <BreakoutPanel
            enabledKeys={brokenOutPanels}
            latest={latest}
            milestones={milestones}
            elapsedLabel={elapsedLabel}
            roast={roast}
          />
        )}
        </div>
        </div>
      )}
    </div>
  );
}
