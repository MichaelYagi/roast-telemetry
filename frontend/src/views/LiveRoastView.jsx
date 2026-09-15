import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, settingsStreamUrl } from "../api/client.js";
import { useRoastStream } from "../api/ws.js";
import ArtisanToolbar from "../components/ArtisanToolbar.jsx";
import BreakoutPanel from "../components/BreakoutPanel.jsx";
import { SMALL_READOUT_EXCLUDED_KEYS } from "../breakoutPanels.js";
import ConnectionBadge from "../components/ConnectionBadge.jsx";
import AlarmRulesEditor from "../components/AlarmRulesEditor.jsx";
import ConnectionTestPanel from "../components/ConnectionTestPanel.jsx";
import ControlPanel from "../components/ControlPanel.jsx";
import EventButtonRow from "../components/EventButtonRow.jsx";
import RoastChart from "../components/RoastChart.jsx";
import { formatTemp } from "../tempUnits.js";

const SAMPLE_ALOG_PATH = "backend/data/sample_roasts/demo_roast.alog";

// Empty-string form field -> null (meaning "use ModbusEngine's own
// default"), otherwise the numeric value -- used for all the advanced
// register-map override fields below, since RoastCreateRequest treats
// null/omitted the same as never having set them.
function numOrNull(v) {
  return v === "" || v == null ? null : Number(v);
}

function formatElapsed(seconds) {
  if (seconds == null) return "00:00";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

const LIVE_MODES = ["modbus_live", "ms6514_live"];
const CONTROLLABLE_MODES = ["simulator", "modbus_live"];
// modbus_live reads Heater/Fan/Drum back from the device itself (genuine
// PLC/VFD registers, not an echo of this app's own commands -- see
// modbus_bridge/engine.py's tick()), so auto-sending a "starting value" on
// connect would overwrite whatever the roaster's actually doing instead of
// just reflecting it -- e.g. an operator's own manual setting, or state
// left over from a previous session. simulator has no such real state to
// clobber (and nothing to read back), so it still needs an explicit
// starting point.
const AUTO_APPLY_STARTING_CONTROLS_MODES = ["simulator"];

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

// Below this the chart still shows a curve and its axes, just cramped --
// smaller than that starts throwing away the actual point of a chart (on
// a short phone screen, being able to shrink it well past its 420
// default is the whole ask; a hard floor just stops a drag from
// collapsing it to nothing useful). No hard ceiling -- unlike splitWidth,
// which trades width against a sibling panel with its own floor, height
// only trades against page scroll, which is the user's own call.
const CHART_MIN_HEIGHT = 160;
const CHART_HEIGHT_STORAGE_KEY = "roast-telemetry:chartHeight";
const CHART_DEFAULT_HEIGHT = 420; // RoastChart's own default -- kept in sync explicitly, see chartHeight state below

const STATUS_TEXT = {
  idle: "Configure a roast, then press ON to connect the device.",
  armed: "Device connected. Press START to begin recording.",
  roasting: "Scope recording…",
  cooling: "Cooling…",
  finished: "Roast finished. Press RESET to configure a new one.",
};

export default function LiveRoastView() {
  const [form, setForm] = useState({
    title: "",
    mode: "simulator",
    beans: "",
    weight_green_g: "",
    alog_path: SAMPLE_ALOG_PATH,
    playback_speed: 4,
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
    // Milestone-triggered automations (Artisan-style Alarms), modbus_live
    // only -- see AlarmRulesEditor. Lives inside the roast's own config
    // (not a separate "controls" field like heater_pct/fan_pct/
    // drum_speed_pct above), since it rides through saved-preset
    // config_json automatically that way -- see backend/app/models.py's
    // AlarmRule/RoastCreateRequest.alarms comment.
    alarmRules: [],
    // Advanced Modbus register-map overrides -- all blank by default,
    // meaning "use ModbusEngine's own (FZ-94) default". See the New Roast
    // form's "Advanced Modbus register map" section.
    modbus_bt_slave_id: "",
    modbus_bt_register: "",
    modbus_bt_divisor: "",
    modbus_et_slave_id: "",
    modbus_et_register: "",
    modbus_et_divisor: "",
    modbus_dt_slave_id: "",
    modbus_dt_register: "",
    modbus_dt_divisor: "",
    modbus_burner_slave_id: "",
    modbus_burner_register: "",
    modbus_burner_divisor: "",
    modbus_air_slave_id: "",
    modbus_air_control_register: "",
    modbus_air_frequency_register: "",
    modbus_air_feedback_register: "",
    modbus_air_min_pct: "",
    modbus_air_max_pct: "",
    modbus_drum_slave_id: "",
    modbus_drum_control_register: "",
    modbus_drum_frequency_register: "",
    modbus_drum_feedback_register: "",
    modbus_drum_min_pct: "",
    modbus_drum_max_pct: "",
    modbus_burner_sv_min_c: "",
    modbus_burner_sv_max_c: "",
  });
  const [showAdvancedModbus, setShowAdvancedModbus] = useState(false);
  // Populates the Serial port / drive-port fields' <datalist> -- a
  // convenience list of what the OS currently sees plugged in, not a
  // whitelist (both fields stay plain text inputs, so a port not
  // currently enumerated -- unplugged, or the fake hardware's /tmp path
  // used for testing -- still works by typing it in).
  const [serialPorts, setSerialPorts] = useState([]);
  const [serialPortsLoading, setSerialPortsLoading] = useState(false);
  function refreshSerialPorts() {
    setSerialPortsLoading(true);
    api
      .listSerialPorts()
      .then(setSerialPorts)
      .catch(() => setSerialPorts([]))
      .finally(() => setSerialPortsLoading(false));
  }
  const [phase, setPhase] = useState("idle"); // idle | armed | roasting | cooling | finished
  const [roastId, setRoastId] = useState(null);
  const [noteText, setNoteText] = useState("");
  const [error, setError] = useState(null);
  const [presets, setPresets] = useState([]);
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [presetName, setPresetName] = useState("");
  const [presetFeedback, setPresetFeedback] = useState(null);
  const [brokenOutPanels, setBrokenOutPanels] = useState([]); // ordered array, matches Settings' display order
  const [panelColors, setPanelColors] = useState({}); // key -> hex override, shared by both readout panels
  const [smallReadoutPanels, setSmallReadoutPanels] = useState([]); // ordered array, independent from brokenOutPanels
  const [tempUnit, setTempUnit] = useState("c"); // "c" | "f" -- display only, see Settings > Temperature Unit
  const [viewportWide, setViewportWide] = useState(
    () => typeof window !== "undefined" && window.innerWidth >= BREAKOUT_SPLIT_MIN_VIEWPORT
  );
  const [splitWidth, setSplitWidth] = useState(() => {
    const saved = typeof window !== "undefined" && Number(localStorage.getItem(SPLIT_WIDTH_STORAGE_KEY));
    return saved > 0 ? saved : 780;
  });
  // Drives both RoastChart's own height prop and, indirectly, the Small
  // Readout column's height -- that column already tracks the chart's
  // *rendered* height via --scope-chart-height (a ResizeObserver, not a
  // copy of this state -- see that effect below), so shrinking the chart
  // here shrinks Small Readout right along with it for free.
  const [chartHeight, setChartHeight] = useState(() => {
    const saved = typeof window !== "undefined" && Number(localStorage.getItem(CHART_HEIGHT_STORAGE_KEY));
    return saved >= CHART_MIN_HEIGHT ? saved : CHART_DEFAULT_HEIGHT;
  });
  const chartDragStateRef = useRef(null);
  // Tracked only so the render below can derive a *display* width that's
  // clamped to whatever room the window currently has -- see
  // effectiveSplitWidth. Resizing used to clamp splitWidth itself (the
  // user's actual saved preference) directly, which meant shrinking the
  // window even briefly below the panel's minimum and then restoring it
  // left the divider stuck wherever it got squeezed to, instead of back
  // where the user had put it.
  const [windowWidth, setWindowWidth] = useState(() => (typeof window !== "undefined" ? window.innerWidth : 1400));
  const dragStateRef = useRef(null);
  const scopeChartRef = useRef(null);

  // Must be computed here, before the effects below that depend on them --
  // they used to live much further down near the render return, which put
  // their `const` declarations after the useEffect calls that reference
  // them in dependency arrays, and a dependency array is evaluated
  // immediately (not deferred like the effect body), so that ordering threw
  // "Cannot access before initialization" (TDZ) on every render.
  const showBreakoutPanel = phase !== "idle" && brokenOutPanels.length > 0;
  const showSplitLayout = showBreakoutPanel && viewportWide;

  const { roast, connectionStatus, latestPreview, lastError, pendingAlarms, alarmNotifications, dismissAlarmNotification } =
    useRoastStream(roastId);

  // Surfaces a server-side read/tick failure (e.g. the real serial link
  // dropping mid-test) into the same banner connect failures already use
  // -- useRoastStream's "error" WS message used to be silently dropped
  // entirely (there was no handler for it at all).
  useEffect(() => {
    if (lastError) setError(lastError);
  }, [lastError]);

  useEffect(() => {
    function onResize() {
      setViewportWide(window.innerWidth >= BREAKOUT_SPLIT_MIN_VIEWPORT);
      setWindowWidth(window.innerWidth);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Derived, not stored -- clamps the *saved* splitWidth down only for
  // display when the current window doesn't have room for it (40px is
  // .breakout-split-escape's own left+right padding, an estimate rather
  // than a live measurement), without ever overwriting that saved
  // preference. Recomputes fresh on every resize, so widening the window
  // back out snaps the divider right back to where the user left it.
  const effectiveSplitWidth = clampSplitWidth(splitWidth, windowWidth - 40);

  // Bridges split-pane state to App.jsx's header, which this route has no
  // prop/context connection to (see the body.breakout-split-active rules
  // in styles.css) -- so the header can align itself with the content
  // split without lifting this state up or adding Context for one value.
  useEffect(() => {
    document.body.classList.toggle("breakout-split-active", showSplitLayout);
    return () => document.body.classList.remove("breakout-split-active");
  }, [showSplitLayout]);

  useEffect(() => {
    document.documentElement.style.setProperty("--breakout-split-width", `${effectiveSplitWidth}px`);
  }, [effectiveSplitWidth]);

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

  // Caps the Small Readout column to the chart's own rendered height (see
  // .small-readout-col in styles.css) instead of letting it stretch the
  // whole row taller when enough items are enabled -- RoastChart has a
  // fixed height (420 by default), but that's a prop, not a CSS constant,
  // so this measures it rather than hardcoding a number that'd silently
  // drift out of sync if that default ever changed. Runs independent of
  // showSplitLayout -- unlike the split-pane measurement above, Small
  // Readout is shown at any width.
  useEffect(() => {
    const el = scopeChartRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(([entry]) => {
      document.documentElement.style.setProperty("--scope-chart-height", `${entry.contentRect.height}px`);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [phase]);

  function clampSplitWidth(width, containerWidth) {
    const maxMain = containerWidth - SPLIT_MIN_PANEL_WIDTH - SPLIT_DIVIDER_WIDTH;
    return Math.min(Math.max(width, SPLIT_MIN_MAIN_WIDTH), Math.max(SPLIT_MIN_MAIN_WIDTH, maxMain));
  }

  // Tracks the drag by delta-from-start (start Y + start height), not
  // recomputed from the pointer's absolute position like the width
  // divider above -- there's no equivalent of that divider's "distance
  // from the row's left edge" for a horizontal handle sitting at a
  // variable Y position, so this is simpler to reason about anyway.
  function handleChartResizePointerDown(e) {
    chartDragStateRef.current = { startY: e.clientY, startHeight: chartHeight };
    e.currentTarget.classList.add("dragging");
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function handleChartResizePointerMove(e) {
    if (!chartDragStateRef.current) return;
    const { startY, startHeight } = chartDragStateRef.current;
    setChartHeight(Math.max(CHART_MIN_HEIGHT, startHeight + (e.clientY - startY)));
  }

  function handleChartResizePointerUp(e) {
    if (!chartDragStateRef.current) return;
    chartDragStateRef.current = null;
    e.currentTarget.classList.remove("dragging");
    e.currentTarget.releasePointerCapture(e.pointerId);
    setChartHeight((h) => {
      localStorage.setItem(CHART_HEIGHT_STORAGE_KEY, String(h));
      return h;
    });
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
    refreshPresets();
    reconnectActiveRoast();
    refreshSerialPorts();
  }, []);

  // Hot-applies Settings > Big Readout Panel / Small Readout changes to an
  // already-open tab -- server pushes the current settings on connect and
  // again on every save from any client (see backend/app/api/settings.py's
  // /settings/stream), rather than this tab polling on a timer.
  useEffect(() => {
    const applySettings = (s) => {
      setBrokenOutPanels(s.broken_out_panels || []);
      setPanelColors(s.breakout_panel_colors || {});
      // Defensive filter, not just SettingsView's editor-side one -- an
      // older save (from before "time" was excluded) could still have it
      // in the array.
      setSmallReadoutPanels((s.small_readout_panels || []).filter((k) => !SMALL_READOUT_EXCLUDED_KEYS.includes(k)));
      setTempUnit(s.temperature_unit || "c");
    };
    const source = new EventSource(settingsStreamUrl());
    source.addEventListener("settings", (e) => {
      try {
        applySettings(JSON.parse(e.data));
      } catch {
        // malformed/partial event -- ignore, next push will self-correct
      }
    });
    // EventSource retries on its own after a drop; nothing else to do here.
    return () => source.close();
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
      if (LIVE_MODES.includes(form.mode)) {
        // modbus_live/ms6514_live: this is the real ON action -- connects
        // and starts streaming live readings (see api.createRoast's
        // backend counterpart, which for these two modes only connects,
        // not records -- see handleStart below for the actual START).
        // Everything else (simulator/alog_playback) has no real
        // connection to make here, so stays the plain local flip it's
        // always been.
        try {
          const payload = { ...buildConfigFromForm(), title: form.title || `Roast ${new Date().toLocaleString()}` };
          const summary = await api.createRoast(payload);
          setRoastId(summary.id);
          setPhase("armed");
        } catch (err) {
          setError(err.message);
        }
      } else {
        setPhase("armed");
      }
    } else if (phase === "armed") {
      if (roastId) {
        await api.stopRoast(roastId).catch((err) => setError(err.message));
        setRoastId(null);
      }
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
      beans: form.beans || null,
      weight_green_g: form.weight_green_g ? Number(form.weight_green_g) : null,
      sample_interval_s: 1.0,
    };
    if (form.mode === "alog_playback") {
      payload.alog_path = form.alog_path;
      payload.playback_speed = Number(form.playback_speed) || 1;
    }
    if (form.mode === "modbus_live") {
      payload.modbus_port = form.modbus_port;
      payload.modbus_baudrate = Number(form.modbus_baudrate) || 19200;
      payload.modbus_control_port = form.modbus_control_port || null;
      payload.modbus_control_baudrate = Number(form.modbus_control_baudrate) || 19200;
      payload.modbus_bt_slave_id = numOrNull(form.modbus_bt_slave_id);
      payload.modbus_bt_register = numOrNull(form.modbus_bt_register);
      payload.modbus_bt_divisor = numOrNull(form.modbus_bt_divisor);
      payload.modbus_et_slave_id = numOrNull(form.modbus_et_slave_id);
      payload.modbus_et_register = numOrNull(form.modbus_et_register);
      payload.modbus_et_divisor = numOrNull(form.modbus_et_divisor);
      payload.modbus_dt_slave_id = numOrNull(form.modbus_dt_slave_id);
      payload.modbus_dt_register = numOrNull(form.modbus_dt_register);
      payload.modbus_dt_divisor = numOrNull(form.modbus_dt_divisor);
      payload.modbus_burner_slave_id = numOrNull(form.modbus_burner_slave_id);
      payload.modbus_burner_register = numOrNull(form.modbus_burner_register);
      payload.modbus_burner_divisor = numOrNull(form.modbus_burner_divisor);
      payload.modbus_air_slave_id = numOrNull(form.modbus_air_slave_id);
      payload.modbus_air_control_register = numOrNull(form.modbus_air_control_register);
      payload.modbus_air_frequency_register = numOrNull(form.modbus_air_frequency_register);
      payload.modbus_air_feedback_register = numOrNull(form.modbus_air_feedback_register);
      payload.modbus_air_min_pct = numOrNull(form.modbus_air_min_pct);
      payload.modbus_air_max_pct = numOrNull(form.modbus_air_max_pct);
      payload.modbus_drum_slave_id = numOrNull(form.modbus_drum_slave_id);
      payload.modbus_drum_control_register = numOrNull(form.modbus_drum_control_register);
      payload.modbus_drum_frequency_register = numOrNull(form.modbus_drum_frequency_register);
      payload.modbus_drum_feedback_register = numOrNull(form.modbus_drum_feedback_register);
      payload.modbus_drum_min_pct = numOrNull(form.modbus_drum_min_pct);
      payload.modbus_drum_max_pct = numOrNull(form.modbus_drum_max_pct);
      payload.modbus_burner_sv_min_c = numOrNull(form.modbus_burner_sv_min_c);
      payload.modbus_burner_sv_max_c = numOrNull(form.modbus_burner_sv_max_c);
      payload.alarms = form.alarmRules || [];
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
      let summary;
      if (LIVE_MODES.includes(form.mode)) {
        // Already connect()ed (see handleToggleConnect's idle branch) --
        // this is the real START, begin recording what's already flowing
        // over the existing roastId instead of creating a new connection.
        summary = await api.beginRecording(roastId);
      } else {
        const payload = { ...buildConfigFromForm(), title: form.title || `Roast ${new Date().toLocaleString()}` };
        summary = await api.createRoast(payload);
        setRoastId(summary.id);
      }
      setPhase("roasting");
      if (AUTO_APPLY_STARTING_CONTROLS_MODES.includes(form.mode)) {
        const controls = buildControlsFromForm();
        if (Object.keys(controls).length) {
          api.sendCommand(summary.id, controls).catch((err) => setError(err.message));
        }
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
      beans: c.beans || "",
      weight_green_g: c.weight_green_g ?? "",
      alog_path: c.alog_path || f.alog_path,
      playback_speed: c.playback_speed ?? f.playback_speed,
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
      modbus_bt_slave_id: c.modbus_bt_slave_id ?? "",
      modbus_bt_register: c.modbus_bt_register ?? "",
      modbus_bt_divisor: c.modbus_bt_divisor ?? "",
      modbus_et_slave_id: c.modbus_et_slave_id ?? "",
      modbus_et_register: c.modbus_et_register ?? "",
      modbus_et_divisor: c.modbus_et_divisor ?? "",
      modbus_dt_slave_id: c.modbus_dt_slave_id ?? "",
      modbus_dt_register: c.modbus_dt_register ?? "",
      modbus_dt_divisor: c.modbus_dt_divisor ?? "",
      modbus_burner_slave_id: c.modbus_burner_slave_id ?? "",
      modbus_burner_register: c.modbus_burner_register ?? "",
      modbus_burner_divisor: c.modbus_burner_divisor ?? "",
      modbus_air_slave_id: c.modbus_air_slave_id ?? "",
      modbus_air_control_register: c.modbus_air_control_register ?? "",
      modbus_air_frequency_register: c.modbus_air_frequency_register ?? "",
      modbus_air_feedback_register: c.modbus_air_feedback_register ?? "",
      modbus_air_min_pct: c.modbus_air_min_pct ?? "",
      modbus_air_max_pct: c.modbus_air_max_pct ?? "",
      modbus_drum_slave_id: c.modbus_drum_slave_id ?? "",
      modbus_drum_control_register: c.modbus_drum_control_register ?? "",
      modbus_drum_frequency_register: c.modbus_drum_frequency_register ?? "",
      modbus_drum_feedback_register: c.modbus_drum_feedback_register ?? "",
      modbus_drum_min_pct: c.modbus_drum_min_pct ?? "",
      modbus_drum_max_pct: c.modbus_drum_max_pct ?? "",
      modbus_burner_sv_min_c: c.modbus_burner_sv_min_c ?? "",
      modbus_burner_sv_max_c: c.modbus_burner_sv_max_c ?? "",
      alarmRules: c.alarms || [],
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

  // "idle" here means "connected via connect(), not yet recording" (see
  // RoastSession.connect()/apply_command() on the backend) -- lets
  // Air/Drum/Burner controls (and Testing Mode's checks) work while
  // merely armed, matching Artisan's own control-before-record model, not
  // just once an actual roast is roasting/cooling.
  const isActive = roast && (roast.status === "roasting" || roast.status === "cooling" || roast.status === "idle");
  // Narrower than isActive on purpose -- milestone events (EventButtonRow)
  // shouldn't be markable during the merely-armed/preview window: profile
  // is empty there, so a click would land at time_s=0.0 and then silently
  // survive into the real recording once START is pressed (see
  // add_event()'s own status guard on the backend, which rejects this
  // server-side too -- this is just so the button is disabled *before*
  // that doomed request round trips).
  const isRecording = roast && (roast.status === "roasting" || roast.status === "cooling");
  // While armed (status "idle"), roast.profile is intentionally empty --
  // the server never appends to it before recording starts (that's what
  // keeps the chart showing no curve pre-recording) -- so BT/ET/etc. come
  // from the live preview stream instead during that window.
  const latest = roast?.status === "idle" ? latestPreview : roast?.profile?.[roast.profile.length - 1];
  // Stays 00:00 while merely connected/previewing (status "idle") even
  // though the engine's own clock is already ticking (that's how BT/ET's
  // RoR gets computed live) -- matches Artisan, where the elapsed timer
  // starts at Charge, not at connect. The recorded roast's own time axis
  // genuinely resets to 0 at that point too (see ModbusEngine/MS6514Engine's
  // reset_detection(), called from begin_recording()) -- this just keeps
  // the *display* in sync with that reset instead of visibly jumping
  // backward once recording actually starts.
  const elapsedLabel = roast?.status === "idle" ? formatElapsed(null) : formatElapsed(latest?.time_s);

  // Once a roast exists (including one reattached after a page refresh --
  // see the reconnect effect below), it's the source of truth for which
  // mode-specific controls/hints to show, not the Configure Roast form's
  // local state, which resets to its defaults on every page load.
  const activeMode = roast?.mode || form.mode;

  const chargeEvent = roast?.events?.find((e) => e.type === "CHARGE");
  const dryEndEvent = roast?.events?.find((e) => e.type === "DRY_END");
  const fcStartEvent = roast?.events?.find((e) => e.type === "FC_START");
  const dropEvent = roast?.events?.find((e) => e.type === "DROP");

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
    // Same convention as dryPercent above: only shown once the phase's
    // own boundary events have *both* fired (a completed duration, not a
    // guess at one still in progress), as a % of elapsed time so far --
    // not of the final Charge-to-Drop total, which isn't known yet mid-roast
    // (that's what RoastChart.jsx's own computePhases() shows post-hoc).
    maillardPercent:
      dryEndEvent && fcStartEvent && latest?.time_s
        ? `${(((fcStartEvent.time_s - dryEndEvent.time_s) / latest.time_s) * 100).toFixed(1)}%`
        : "---",
    // Unlike dryPercent/maillardPercent above, this one only ever shows
    // once dropEvent exists -- by then the true Charge-to-Drop total is
    // already fully known (not still in progress), so unlike those two
    // this deliberately does NOT divide by latest.time_s (which keeps
    // growing through cooling, silently shrinking an already-final DTR%
    // the longer cooling runs) -- matches RoastChart.jsx's own post-hoc
    // computePhases(), which uses the same drop-minus-charge total.
    devPercent:
      fcStartEvent && dropEvent && chargeEvent
        ? `${(((dropEvent.time_s - fcStartEvent.time_s) / (dropEvent.time_s - chargeEvent.time_s)) * 100).toFixed(1)}%`
        : "---",
    devTime:
      dropEvent && fcStartEvent
        ? formatElapsed(dropEvent.time_s - fcStartEvent.time_s) // frozen, final
        : fcStartEvent && latest?.time_s
          ? formatElapsed(latest.time_s - fcStartEvent.time_s) // live, still growing
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
                <option value="simulator">Simulator</option>
                <option value="alog_playback">.alog Playback</option>
                <option value="modbus_live">Direct Modbus (USB)</option>
                <option value="ms6514_live">Direct USB (thermocouple meter)</option>
              </select>
            </label>
          </div>
          <div className="form-row">
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
          {form.mode === "modbus_live" && (
            <div className="form-row">
              {/* Shared by both port fields below via list=. Plain text
                  inputs, not a <select> -- this is a convenience list of
                  what the OS currently sees, not a whitelist, so a port
                  not currently enumerated (unplugged, or the fake
                  hardware's /tmp path used for testing) still works by
                  typing it in. label carries the driver's own
                  description (e.g. "USB-SERIAL CH340") when there is
                  one; value is the bare device name, so picking a
                  suggestion fills in exactly what the field needs, not
                  the description text too. */}
              <datalist id="serial-ports-list">
                {serialPorts.map((p) => (
                  <option key={p.device} value={p.device} label={p.description || undefined} />
                ))}
              </datalist>
              <label>
                Serial port
                <span className="serial-port-input-row">
                  <input
                    placeholder="COM3"
                    list="serial-ports-list"
                    value={form.modbus_port}
                    onChange={(e) => setForm({ ...form, modbus_port: e.target.value })}
                  />
                  <button
                    type="button"
                    className="advanced-toggle"
                    onClick={refreshSerialPorts}
                    disabled={serialPortsLoading}
                    title="Re-scan for connected serial ports"
                  >
                    {serialPortsLoading ? "…" : "⟳"}
                  </button>
                </span>
                {serialPorts.length === 0 && !serialPortsLoading && (
                  <span className="hint">No serial ports detected -- plug your adapter in, then ⟳.</span>
                )}
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
                  list="serial-ports-list"
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
                Direct Modbus RTU to the FZ-94 over USB (not the EVO, which is network/Ethernet) — talks
                straight to the roaster's own PLC. One connection handles BT/ET/DT/Burner (a drum-temp
                setpoint, not a power %) and Air/Drum together (19200 baud, 8N2) — the "separate drive port"
                field only matters if your own wiring genuinely needs a second connection, which is
                uncommon; leave it blank otherwise. Mutually exclusive with any other software already
                connected to the same port(s). Not tested against real FZ-94 hardware; the BT/ET/DT/Burner
                numbers and the single-connection setup are confirmed against a shipped machine preset and
                its interpreting source code for this exact model; Air/Drum register numbers are only
                blog-sourced.
              </p>
              <button
                type="button"
                className="advanced-toggle"
                onClick={() => setShowAdvancedModbus((v) => !v)}
              >
                {showAdvancedModbus ? "▾" : "▸"} Advanced Modbus register map
              </button>
              {showAdvancedModbus && (
                <div className="advanced-modbus-fields">
                  <p className="hint">
                    Leave any of these blank to use the FZ-94 defaults above. Only worth touching once you've
                    confirmed your own unit's actual register map differs (see the VFD's own nameplate/front-panel
                    parameters, or a real probe's slave ID). BT/ET/DT/Burner are confirmed against a shipped
                    FZ-94 machine preset and its interpreting source code — exposed here for a genuinely different
                    Modbus roaster, not because a real FZ-94 should need them changed. Air/Drum (control+feedback
                    registers, operating range) and the Burner SV°C range are that engine's least-confirmed,
                    blog-sourced defaults.
                  </p>
                  <div className="form-row">
                    <label>
                      BT slave ID
                      <input
                        type="number"
                        placeholder="11"
                        value={form.modbus_bt_slave_id}
                        onChange={(e) => setForm({ ...form, modbus_bt_slave_id: e.target.value })}
                      />
                    </label>
                    <label>
                      BT register
                      <input
                        type="number"
                        placeholder="0"
                        value={form.modbus_bt_register}
                        onChange={(e) => setForm({ ...form, modbus_bt_register: e.target.value })}
                      />
                    </label>
                    <label>
                      BT divisor
                      <input
                        type="number"
                        placeholder="10"
                        value={form.modbus_bt_divisor}
                        onChange={(e) => setForm({ ...form, modbus_bt_divisor: e.target.value })}
                      />
                    </label>
                    <label>
                      ET slave ID
                      <input
                        type="number"
                        placeholder="13"
                        value={form.modbus_et_slave_id}
                        onChange={(e) => setForm({ ...form, modbus_et_slave_id: e.target.value })}
                      />
                    </label>
                    <label>
                      ET register
                      <input
                        type="number"
                        placeholder="0"
                        value={form.modbus_et_register}
                        onChange={(e) => setForm({ ...form, modbus_et_register: e.target.value })}
                      />
                    </label>
                    <label>
                      ET divisor
                      <input
                        type="number"
                        placeholder="10"
                        value={form.modbus_et_divisor}
                        onChange={(e) => setForm({ ...form, modbus_et_divisor: e.target.value })}
                      />
                    </label>
                  </div>
                  <div className="form-row">
                    <label>
                      DT slave ID
                      <input
                        type="number"
                        placeholder="12"
                        value={form.modbus_dt_slave_id}
                        onChange={(e) => setForm({ ...form, modbus_dt_slave_id: e.target.value })}
                      />
                    </label>
                    <label>
                      DT register
                      <input
                        type="number"
                        placeholder="0"
                        value={form.modbus_dt_register}
                        onChange={(e) => setForm({ ...form, modbus_dt_register: e.target.value })}
                      />
                    </label>
                    <label>
                      DT divisor
                      <input
                        type="number"
                        placeholder="10"
                        value={form.modbus_dt_divisor}
                        onChange={(e) => setForm({ ...form, modbus_dt_divisor: e.target.value })}
                      />
                    </label>
                    <label>
                      Burner slave ID
                      <input
                        type="number"
                        placeholder="12"
                        value={form.modbus_burner_slave_id}
                        onChange={(e) => setForm({ ...form, modbus_burner_slave_id: e.target.value })}
                      />
                    </label>
                    <label>
                      Burner register
                      <input
                        type="number"
                        placeholder="5"
                        value={form.modbus_burner_register}
                        onChange={(e) => setForm({ ...form, modbus_burner_register: e.target.value })}
                      />
                    </label>
                    <label>
                      Burner divisor
                      <input
                        type="number"
                        placeholder="10"
                        value={form.modbus_burner_divisor}
                        onChange={(e) => setForm({ ...form, modbus_burner_divisor: e.target.value })}
                      />
                    </label>
                  </div>
                  <div className="form-row">
                    <label>
                      Air slave ID
                      <input
                        type="number"
                        placeholder="1"
                        value={form.modbus_air_slave_id}
                        onChange={(e) => setForm({ ...form, modbus_air_slave_id: e.target.value })}
                      />
                    </label>
                    <label>
                      Air run/stop register
                      <input
                        type="number"
                        placeholder="8192"
                        value={form.modbus_air_control_register}
                        onChange={(e) => setForm({ ...form, modbus_air_control_register: e.target.value })}
                      />
                    </label>
                    <label>
                      Air frequency register
                      <input
                        type="number"
                        placeholder="8193"
                        value={form.modbus_air_frequency_register}
                        onChange={(e) => setForm({ ...form, modbus_air_frequency_register: e.target.value })}
                      />
                    </label>
                    <label>
                      Air feedback register
                      <input
                        type="number"
                        placeholder="8451"
                        value={form.modbus_air_feedback_register}
                        onChange={(e) => setForm({ ...form, modbus_air_feedback_register: e.target.value })}
                      />
                    </label>
                    <label>
                      Air min %
                      <input
                        type="number"
                        placeholder="0"
                        value={form.modbus_air_min_pct}
                        onChange={(e) => setForm({ ...form, modbus_air_min_pct: e.target.value })}
                      />
                    </label>
                    <label>
                      Air max %
                      <input
                        type="number"
                        placeholder="100"
                        value={form.modbus_air_max_pct}
                        onChange={(e) => setForm({ ...form, modbus_air_max_pct: e.target.value })}
                      />
                    </label>
                  </div>
                  <div className="form-row">
                    <label>
                      Drum slave ID
                      <input
                        type="number"
                        placeholder="2"
                        value={form.modbus_drum_slave_id}
                        onChange={(e) => setForm({ ...form, modbus_drum_slave_id: e.target.value })}
                      />
                    </label>
                    <label>
                      Drum run/stop register
                      <input
                        type="number"
                        placeholder="8192"
                        value={form.modbus_drum_control_register}
                        onChange={(e) => setForm({ ...form, modbus_drum_control_register: e.target.value })}
                      />
                    </label>
                    <label>
                      Drum frequency register
                      <input
                        type="number"
                        placeholder="8193"
                        value={form.modbus_drum_frequency_register}
                        onChange={(e) => setForm({ ...form, modbus_drum_frequency_register: e.target.value })}
                      />
                    </label>
                    <label>
                      Drum feedback register
                      <input
                        type="number"
                        placeholder="8451"
                        value={form.modbus_drum_feedback_register}
                        onChange={(e) => setForm({ ...form, modbus_drum_feedback_register: e.target.value })}
                      />
                    </label>
                    <label>
                      Drum min %
                      <input
                        type="number"
                        placeholder="0"
                        value={form.modbus_drum_min_pct}
                        onChange={(e) => setForm({ ...form, modbus_drum_min_pct: e.target.value })}
                      />
                    </label>
                    <label>
                      Drum max %
                      <input
                        type="number"
                        placeholder="70"
                        value={form.modbus_drum_max_pct}
                        onChange={(e) => setForm({ ...form, modbus_drum_max_pct: e.target.value })}
                      />
                    </label>
                  </div>
                  <div className="form-row">
                    <label>
                      Burner SV min °C (0% heater)
                      <input
                        type="number"
                        placeholder="100"
                        value={form.modbus_burner_sv_min_c}
                        onChange={(e) => setForm({ ...form, modbus_burner_sv_min_c: e.target.value })}
                      />
                    </label>
                    <label>
                      Burner SV max °C (100% heater)
                      <input
                        type="number"
                        placeholder="250"
                        value={form.modbus_burner_sv_max_c}
                        onChange={(e) => setForm({ ...form, modbus_burner_sv_max_c: e.target.value })}
                      />
                    </label>
                    <p className="hint" style={{ flexBasis: "100%" }}>
                      Air/Drum min+max and the Burner SV min+max only take effect as pairs — set both sides or
                      neither.
                    </p>
                  </div>
                </div>
              )}
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
                Direct USB read of the Mastech MS6514 — reads straight over USB, no other software needed.
                Read-only. T1 → BT, T2 → ET. Keep the meter's display set to "T1" or "T2" (not "T1-T2") for
                reliable dual-channel reading. Every milestone (Charge, Dry End, FC Start, Drop, etc.) is a
                manual click — mark them yourself as the roast happens.
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
          {AUTO_APPLY_STARTING_CONTROLS_MODES.includes(form.mode) && (
            <div className="form-row">
              <label>
                Burner % at start
                <input
                  type="number" min="0" max="100"
                  value={form.heater_pct}
                  onChange={(e) => setForm({ ...form, heater_pct: e.target.value })}
                />
              </label>
              <label>
                Air % at start
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
          {form.mode === "modbus_live" && (
            <p className="hint">
              Burner/Air/Drum aren't set here -- once connected, the Controls panel on the Live Roast page reads and
              shows whatever the roaster is actually doing (from the device itself, not a guess), and nothing is
              written to it until you move a slider yourself.
            </p>
          )}
          {form.mode === "modbus_live" && (
            <AlarmRulesEditor
              rules={form.alarmRules}
              onChange={(rules) => setForm((f) => ({ ...f, alarmRules: rules }))}
            />
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
        <div className="live-roast" style={showSplitLayout ? { width: effectiveSplitWidth } : undefined}>
          {showSplitLayout && toolbarElement}
          {/* Only while armed (connected via ON, not yet recording -- see
              handleToggleConnect/RoastSession.connect()) and only for the
              two modes that have a real connection worth verifying before
              committing to a roast. */}
          {phase === "armed" && LIVE_MODES.includes(activeMode) && (
            <ConnectionTestPanel roastId={roastId} latest={latest} mode={activeMode} tempUnit={tempUnit} />
          )}
          <div className="panel scope-panel">
            <div className="scope-body">
              <div className="scope-chart" ref={scopeChartRef}>
                <RoastChart
                  profile={roast?.profile || []}
                  events={roast?.events || []}
                  title={null}
                  height={chartHeight}
                  tempUnit={tempUnit}
                />
                <div
                  className="scope-chart-resize-handle"
                  title="Drag to resize the chart (Small Readout follows it)"
                  onPointerDown={handleChartResizePointerDown}
                  onPointerMove={handleChartResizePointerMove}
                  onPointerUp={handleChartResizePointerUp}
                />
              </div>
              {/* Independent from the split breakout panel now (see
                  Settings > Small Readout) -- always shown regardless of
                  showSplitLayout, since it's the user's own separate
                  choice of what goes here, not an automatic duplicate of
                  the big panel. Renders nothing (via BreakoutPanel's own
                  empty-list check) if nothing's enabled, same as before
                  Small Readout existed as a concept -- used to be a
                  hardcoded ET/BT/DT/deltaBT legend instead. */}
              <div className="small-readout-col">
                <BreakoutPanel
                  enabledKeys={smallReadoutPanels}
                  latest={latest}
                  milestones={milestones}
                  elapsedLabel={elapsedLabel}
                  roast={roast}
                  colorOverrides={panelColors}
                  tempUnit={tempUnit}
                />
              </div>
            </div>
            <EventButtonRow
              disabled={!isRecording}
              events={roast?.events || []}
              onFire={handleFireEvent}
              manualCharge={LIVE_MODES.includes(activeMode)}
            />
            {pendingAlarms.length > 0 && (
              <p className="hint alarm-pending-hint">
                {pendingAlarms
                  .map((a) => `${a.trigger.replace("_", " ")} automation fires in ~${a.delaySeconds}s`)
                  .join(" · ")}
              </p>
            )}
            {alarmNotifications.map((n) => (
              <p key={n.id} className="alarm-notification-banner" onClick={() => dismissAlarmNotification(n.id)}>
                {n.text}
              </p>
            ))}
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
                // No form-value fallback here (used to be `?? Number(form.heater_pct)`)
                // -- that silently substituted a plausible-looking made-up number
                // whenever `latest` hadn't arrived yet, indistinguishable from a
                // register that's genuinely misconfigured/never going to arrive.
                // ControlPanel itself now renders null as an honest "—"/disabled
                // state instead, which resolves the instant a real sample lands.
                initial={{
                  heater_pct: latest?.heater_pct,
                  fan_pct: latest?.fan_pct,
                  drum_speed_pct: latest?.drum_speed_pct,
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
            {activeMode === "modbus_live" && (
              <p className="hint" style={{ gridColumn: "1 / -1" }}>
                Controls above read and write directly over {form.modbus_port || "the serial port"}: Burner is a
                drum-temperature setpoint (register 5, default 100–250°C — not a power %), Air and Drum are VFD
                drives (run/stop + frequency registers 8192/8193, default 0–100%/0–70%), each with its own
                feedback register (8451) reporting the drive's actual current speed. Out-of-range values are
                clamped to the configured range. See "Advanced Modbus register map" above to override any of
                these for your own unit. Every milestone (Charge, Dry End, FC Start, Drop, etc.) is a manual
                click — mark them yourself as the roast happens.
              </p>
            )}
            {activeMode === "ms6514_live" && (
              <div className="panel control-panel">
                <h3>Mastech MS6514</h3>
                <p className="hint">
                  Reading {form.ms6514_port || "the serial port"} directly — no other software needed.
                  Read-only, this meter has no command to control anything. Every milestone (Charge, Dry
                  End, FC Start, Drop, etc.) is a manual click — mark them yourself below.
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
                      <strong>{ev.label}</strong> @ {formatElapsed(ev.time_s)} ({ev.value != null ? formatTemp(ev.value, tempUnit) : "--"})
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
              colorOverrides={panelColors}
              tempUnit={tempUnit}
            />
          </div>
        ) : (
          <BreakoutPanel
            enabledKeys={brokenOutPanels}
            latest={latest}
            milestones={milestones}
            elapsedLabel={elapsedLabel}
            roast={roast}
            colorOverrides={panelColors}
            tempUnit={tempUnit}
          />
        )}
        </div>
        </div>
      )}
    </div>
  );
}
