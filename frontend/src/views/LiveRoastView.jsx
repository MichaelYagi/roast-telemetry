import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, settingsStreamUrl } from "../api/client.js";
import { useRoastStream } from "../api/ws.js";
import ArtisanToolbar from "../components/ArtisanToolbar.jsx";
import BackgroundProfilePicker from "../components/BackgroundProfilePicker.jsx";
import BreakoutPanel from "../components/BreakoutPanel.jsx";
import { SMALL_READOUT_EXCLUDED_KEYS } from "../breakoutPanels.js";
import ConnectionBadge from "../components/ConnectionBadge.jsx";
import AlarmRulesEditor from "../components/AlarmRulesEditor.jsx";
import ConnectionTestPanel from "../components/ConnectionTestPanel.jsx";
import VerticalControlPanel from "../components/VerticalControlPanel.jsx";
import DeviceProfileEditor from "../components/DeviceProfileEditor.jsx";
import EventButtonRow from "../components/EventButtonRow.jsx";
import RoastChart from "../components/RoastChart.jsx";
import RoastStatsPanel from "../components/RoastStatsPanel.jsx";
import WeightField from "../components/WeightField.jsx";
import { formatTemp } from "../tempUnits.js";

const SAMPLE_ALOG_PATH = "backend/data/sample_roasts/demo_roast.alog";

// Empty-string form field -> null (meaning "use ModbusEngine's own
// default"), otherwise the numeric value -- used for all the advanced
// register-map override fields below, since RoastCreateRequest treats
// null/omitted the same as never having set them.
function numOrNull(v) {
  return v === "" || v == null ? null : Number(v);
}

// YYYYMMDDHHMMSS in local time (not UTC) -- a human reading it back later
// expects it to match the wall-clock time they actually started the roast
// at, not a UTC-shifted one. Just a starting point in the Title field, not
// a silent fallback -- it's visible and editable before you ever connect,
// so (unlike the old auto-generated-on-submit "Roast <timestamp>" this
// replaced) you actually see it and can overwrite it with something more
// descriptive, rather than only discovering it later in History.
function defaultTitle() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

function formatElapsed(seconds) {
  if (seconds == null) return "00:00";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

const LIVE_MODES = ["modbus_live", "ms6514_live", "aillio_live", "tc4_live"];
// modbus_live and aillio_live both read Heater/Fan/Drum back from the
// device itself (genuine PLC/VFD registers or, for Aillio, the device's
// own last-reported state -- not an echo of this app's own commands, see
// modbus_bridge/engine.py's and aillio_bridge/engine.py's own tick()).
// tc4_live is controllable too (OT1/DCFAN) but *doesn't* read either
// back -- TC4's own READ command only returns temperature channels, so
// its heater_pct/fan_pct readouts just stay blank, same as ms6514_live's
// (which has no control at all) -- still belongs in this list rather
// than AUTO_APPLY_STARTING_CONTROLS_MODES below, though: it's real
// hardware with real control capability, so auto-sending a starting
// value on mere connect risks clobbering physical state an operator
// already set by hand, the same reason modbus_live/aillio_live don't
// auto-apply either -- that concern doesn't depend on whether the app
// can read the result back afterward.
const CONTROLLABLE_LIVE_MODES = ["modbus_live", "aillio_live", "tc4_live"];
const CONTROLLABLE_MODES = ["simulator", ...CONTROLLABLE_LIVE_MODES];
// Reading Heater/Fan/Drum back from the device means auto-sending a
// "starting value" on connect would overwrite whatever the roaster's
// actually doing instead of just reflecting it -- e.g. an operator's own
// manual setting, or state left over from a previous session. simulator
// has no such real state to clobber (and nothing to read back), so it
// still needs an explicit starting point.
const AUTO_APPLY_STARTING_CONTROLS_MODES = ["simulator"];
// CONTROLLABLE_LIVE_MODES still won't auto-apply a *fresh* (no preset
// loaded) starting value -- that's the clobbering concern above. But a
// saved preset is an explicit choice to replace whatever's there with
// these specific values, so once one's loaded (selectedPresetId), its
// starting controls should actually reach the device at START, not just
// sit in the form -- see shouldAutoApplyStartingControls below, computed
// with access to that state (this constant alone can't express the
// CONTROLLABLE_LIVE_MODES+preset case).

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
// collapsing it to nothing useful). Also the floor for the vertical
// control panel and Small Readout column beside it -- both size off this
// same chartHeight/--scope-chart-height value (see the ResizeObserver
// effect below), so raising it here is what keeps all three visually
// aligned at a shared minimum instead of the chart shrinking further
// than a 2-member stacked control lane can stay legible at (a CSS-only
// floor on just the control panel was tried and reverted -- a flex row's
// height follows its tallest child regardless of align-self, so that
// dragged the whole row taller than the chart's own real height instead
// of keeping the three in sync). No hard ceiling -- unlike splitWidth,
// which trades width against a sibling panel with its own floor, height
// only trades against page scroll, which is the user's own call.
const CHART_MIN_HEIGHT = 260;
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
    title: defaultTitle(),
    mode: "simulator",
    beans: "",
    weight_green_g: "",
    alog_path: SAMPLE_ALOG_PATH,
    playback_speed: 4,
    modbus_port: "",
    modbus_baudrate: 19200,
    modbus_control_port: "",
    modbus_control_baudrate: 19200,
    // "serial" (USB/RTU, uses modbus_port) or "tcp" (Modbus TCP/Ethernet,
    // e.g. the Coffee-Tech FZ-94 Evo, uses modbus_host/modbus_tcp_port
    // instead) -- see the Data Source dropdown below, which maps its
    // "Direct Modbus (Ethernet)" option onto mode=modbus_live +
    // modbus_transport="tcp" together (mode alone can't distinguish the
    // two -- see backend/app/models.py's RoastCreateRequest.modbus_transport).
    modbus_transport: "serial",
    modbus_host: "",
    modbus_tcp_port: 502,
    // "" = "Custom (advanced fields below)" -- today's exact flow, the
    // 26 flat modbus_* override fields still drive the register map.
    // Any other value is a DeviceProfile id, which takes over instead
    // (see buildConfigFromForm below) and hides the advanced section.
    modbus_device_profile_id: "",
    ms6514_port: "",
    // aillio_live only -- a raw USB device, not a port/host, so this is
    // the whole "connection config" (see backend's
    // RoastCreateRequest.aillio_model docstring). "r1" is the only
    // known value today.
    aillio_model: "r1",
    tc4_port: "",
    // Off by default -- real hardware means a real operator marking
    // milestones by hand, not an algorithm guessing, unless explicitly
    // opted in. Manual clicks still work as an override even when on
    // (see backend's RoastCreateRequest.auto_detect_milestones).
    auto_detect_milestones: false,
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
  const [deviceProfiles, setDeviceProfiles] = useState([]);

  function refreshDeviceProfiles() {
    api.listDeviceProfiles().then(setDeviceProfiles).catch(() => {});
  }
  useEffect(refreshDeviceProfiles, []);

  // Configure Roast form tabs -- General/Device always exist (every data
  // source has *some* Device-tab content: connection fields, an .alog
  // path, or the simulator's starting %s), Milestone/Automation only for
  // the two live-hardware modes (auto-detect + Automation Rules are both
  // meaningless for simulator/alog_playback, which just replay/generate
  // events on their own). The tab bar itself changes shape with the data
  // source rather than ever showing an empty tab.
  const [activeTab, setActiveTab] = useState("general");
  const availableTabs = LIVE_MODES.includes(form.mode) ? ["general", "device", "milestones"] : ["general", "device"];
  useEffect(() => {
    // Lands back on General, not Device -- if Milestone/Automation just
    // disappeared out from under you (switched to Simulator while on
    // it), General is the tab you'd expect after a data-source change,
    // not wherever you happened to be.
    if (!availableTabs.includes(activeTab)) setActiveTab("general");
  }, [availableTabs, activeTab]);
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
  // Background Profile (see BackgroundProfilePicker.jsx/RoastChart.jsx) --
  // a purely visual reference overlay, never touched by anything else in
  // this view (no automation, no control writes read this). Not tied to
  // roastId -- cleared explicitly by the user or a fresh mount, not by
  // starting a new roast, so you can keep pacing against the same
  // reference across several attempts without re-picking it every time.
  const [backgroundRoastId, setBackgroundRoastId] = useState(null);
  const [backgroundProfile, setBackgroundProfile] = useState([]);
  const [backgroundLabel, setBackgroundLabel] = useState(null);
  const [noteText, setNoteText] = useState("");
  const [error, setError] = useState(null);
  const [presets, setPresets] = useState([]);
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [presetName, setPresetName] = useState("");
  const [presetFeedback, setPresetFeedback] = useState(null);
  // Both weight fields use the shared WeightField component (see that
  // file) -- same edit/add/delete UI RoastDetailView.jsx uses. `roast`
  // comes from useRoastStream's websocket state (no setter exposed, and
  // the backend never pushes a message for this REST-only write), so
  // these track a local override instead of trying to mutate `roast`
  // itself. `undefined` means "no override yet, defer to roast.weight_*_g"
  // -- deliberately NOT `null` for that (nullish coalescing can't tell
  // "never touched" apart from "explicitly deleted to empty" if both use
  // null), so a delete can actually override an already-loaded roast
  // value back to empty instead of falling straight through to it again.
  const [greenWeightOverride, setGreenWeightOverride] = useState(undefined);
  const [roastedWeightOverride, setRoastedWeightOverride] = useState(undefined);
  // tc4_live only -- the board's own onboard PID loop (see
  // tc4_bridge/engine.py's own docstring for why this is a stateful
  // mode, not just another slider). Local-only, no server round trip
  // to read it back (the protocol has no PID-state query).
  const [tc4PidEnabled, setTc4PidEnabled] = useState(false);
  const [tc4PidTargetInput, setTc4PidTargetInput] = useState("");
  // Same local-override reasoning as greenWeightOverride above -- `roast`
  // itself is never mutated, tagsSaved (once set) is what actually
  // renders, falling back to roast.tags until the first edit. allTags
  // feeds the "add tag" input's <datalist>, same as RoastDetailView.jsx.
  const [tagsSaved, setTagsSaved] = useState(null);
  const [newTagInput, setNewTagInput] = useState("");
  const [tagsError, setTagsError] = useState(null);
  const [allTags, setAllTags] = useState([]);
  const [brokenOutPanels, setBrokenOutPanels] = useState([]); // ordered array, matches Settings' display order
  const [panelColors, setPanelColors] = useState({}); // key -> hex override, shared by both readout panels
  const [smallReadoutPanels, setSmallReadoutPanels] = useState([]); // ordered array, independent from brokenOutPanels
  const [tempUnit, setTempUnit] = useState("c"); // "c" | "f" -- display only, see Settings > Temperature Unit
  const [verticalControlLayout, setVerticalControlLayout] = useState([]); // Settings > Controls -- see VerticalControlPanel.jsx
  const [verticalControlArrows, setVerticalControlArrows] = useState({});
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

  // undefined override means "defer to roast's own field" -- WeightField
  // itself derives its editing/display state fresh from whatever value
  // it's handed, so this needs no separate seeding effect the way the
  // old hand-rolled version did (that's exactly the class of bug this
  // shared component was built to stop happening again).
  const greenWeightValue = greenWeightOverride !== undefined ? greenWeightOverride : roast?.weight_green_g;
  const roastedWeightValue = roastedWeightOverride !== undefined ? roastedWeightOverride : roast?.weight_roasted_g;

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
    api.listTags().then(setAllTags);
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
      setVerticalControlLayout(s.vertical_control_layout || []);
      setVerticalControlArrows(s.vertical_control_arrows || {});
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
        setForm((f) => ({
          ...f,
          mode: active.mode,
          // Without these, a page refresh mid-roast silently fell back
          // to the form's own mount-time defaults (160/196) instead of
          // whatever this roast was actually configured with -- the ETA
          // estimate below would then quietly compute against the wrong
          // threshold for the rest of the roast.
          auto_detect_milestones: active.auto_detect_milestones ?? f.auto_detect_milestones,
          dry_end_c: active.dry_end_c ?? f.dry_end_c,
          fc_start_c: active.fc_start_c ?? f.fc_start_c,
        }));
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
    } else if (roast?.status === "roasting") {
      // Without this, a second tab/client connected to the same roast
      // never notices when START fires elsewhere -- the tab that calls
      // handleStart() itself already sets this directly, but any other
      // tab only ever learns about status changes through this effect.
      setPhase("roasting");
    }
  }, [roast?.status]);

  function handleReset() {
    setRoastId(null);
    setPhase("idle");
    setError(null);
    setForm((f) => ({ ...f, title: defaultTitle() }));
  }

  // Still required (this only guards against deliberately clearing the
  // field) -- the field itself starts pre-filled with defaultTitle(), a
  // visible starting point you can see and overwrite before connecting,
  // not a silent submit-time fallback the old "Roast <timestamp>"
  // auto-generation used to be (which meant you only ever discovered an
  // unhelpfully-titled roast later in History).
  function requireTitle() {
    if (form.title.trim()) return true;
    setError("Title is required.");
    return false;
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
        if (!requireTitle()) return;
        try {
          const payload = { ...buildConfigFromForm(), title: form.title };
          const summary = await api.createRoast(payload);
          setRoastId(summary.id);
          setPhase("armed");
        } catch (err) {
          setError(err.message);
        }
      } else {
        if (!requireTitle()) return;
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
      payload.modbus_transport = form.modbus_transport;
      if (form.modbus_transport === "tcp") {
        payload.modbus_host = form.modbus_host;
        payload.modbus_tcp_port = Number(form.modbus_tcp_port) || 502;
      } else {
        payload.modbus_port = form.modbus_port;
        payload.modbus_baudrate = Number(form.modbus_baudrate) || 19200;
        payload.modbus_control_port = form.modbus_control_port || null;
        payload.modbus_control_baudrate = Number(form.modbus_control_baudrate) || 19200;
      }
      payload.modbus_device_profile_id = form.modbus_device_profile_id || null;
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
    if (form.mode === "aillio_live") {
      payload.aillio_model = form.aillio_model;
    }
    if (form.mode === "tc4_live") {
      payload.tc4_port = form.tc4_port;
    }
    if (LIVE_MODES.includes(form.mode)) {
      payload.auto_detect_milestones = form.auto_detect_milestones;
      payload.dry_end_c = form.dry_end_c === "" ? null : Number(form.dry_end_c);
      payload.fc_start_c = form.fc_start_c === "" ? null : Number(form.fc_start_c);
    }
    return payload;
  }

  // See AUTO_APPLY_STARTING_CONTROLS_MODES's comment -- simulator always
  // applies its starting values; a CONTROLLABLE_LIVE_MODES mode only does
  // when those values came from an explicitly-loaded saved preset, not a
  // fresh/blank start.
  const shouldAutoApplyStartingControls =
    AUTO_APPLY_STARTING_CONTROLS_MODES.includes(form.mode) ||
    (CONTROLLABLE_LIVE_MODES.includes(form.mode) && Boolean(selectedPresetId));

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
        // Title is already guaranteed non-blank by the time this runs --
        // the Configure Roast form only exists while phase === "idle",
        // and armed (required to get here) is only reachable past
        // handleToggleConnect's own requireTitle() check.
        const payload = { ...buildConfigFromForm(), title: form.title };
        summary = await api.createRoast(payload);
        setRoastId(summary.id);
      }
      setPhase("roasting");
      if (shouldAutoApplyStartingControls) {
        const controls = buildControlsFromForm();
        if (Object.keys(controls).length) {
          api.sendCommand(summary.id, controls).catch((err) => setError(err.message));
        }
      }
    } catch (err) {
      setError(err.message);
    }
  }

  // No !roastId guard here (unlike other handlers in this file that
  // silently no-op without one) -- WeightField's onSave/onDelete are
  // expected to reject on failure so it can show the error instead of
  // wrongly flipping to "saved" display state, and this UI only ever
  // renders once `roast` (and so roastId) already exists anyway (see
  // the `{roast && (...)}` gate around the panel that contains it).
  async function handleSaveGreenWeight(grams) {
    await api.setWeightGreen(roastId, grams);
    setGreenWeightOverride(grams);
  }

  async function handleDeleteGreenWeight() {
    await api.deleteWeightGreen(roastId);
    setGreenWeightOverride(null);
  }

  async function handleSaveRoastedWeight(grams) {
    await api.setWeightRoasted(roastId, grams);
    setRoastedWeightOverride(grams);
  }

  async function handleDeleteRoastedWeight() {
    await api.deleteWeightRoasted(roastId);
    setRoastedWeightOverride(null);
  }

  // Same local-override pattern as the weight setters above -- `roast`
  // is never mutated directly. Both add and remove go through this one
  // function, always PUTting the full tag list (replace-the-whole-set
  // semantics, see storage.set_roast_tags), not a single add/remove call.
  async function handleTagsChange(nextTags) {
    if (!roastId) return;
    setTagsError(null);
    try {
      await api.setTags(roastId, nextTags);
      setTagsSaved(nextTags);
    } catch (err) {
      setTagsError(err.message);
    }
  }

  function handleAddTag() {
    const tag = newTagInput.trim();
    const currentTags = tagsSaved ?? roast?.tags ?? [];
    if (!tag || currentTags.includes(tag)) {
      setNewTagInput("");
      return;
    }
    setNewTagInput("");
    handleTagsChange([...currentTags, tag]);
  }

  function handleRemoveTag(tag) {
    const currentTags = tagsSaved ?? roast?.tags ?? [];
    handleTagsChange(currentTags.filter((t) => t !== tag));
  }

  // Both are effectively fire-and-forget here -- the actual UI update
  // comes back through useRoastStream's websocket ("event_deleted"/
  // "event_updated", see ws.js), not a local state mutation, since the
  // backend publishes those the same way it already does for "event"/
  // "note" on every other roast mutation. Errors still surface via the
  // same error banner every other action on this page uses.
  async function handleDeleteMilestone(eventId) {
    if (!roastId) return;
    try {
      await api.deleteEvent(roastId, eventId);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleRetimeMilestone(eventId, timeS) {
    if (!roastId) return;
    try {
      await api.retimeEvent(roastId, eventId, timeS);
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
      modbus_transport: c.modbus_transport || "serial",
      modbus_host: c.modbus_host || "",
      modbus_tcp_port: c.modbus_tcp_port ?? 502,
      modbus_device_profile_id: c.modbus_device_profile_id || "",
      ms6514_port: c.ms6514_port || "",
      aillio_model: c.aillio_model || "r1",
      tc4_port: c.tc4_port || "",
      auto_detect_milestones: c.auto_detect_milestones ?? false,
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

  function handleSelectBackground(id, roastDetail) {
    setBackgroundRoastId(id);
    setBackgroundProfile(roastDetail?.profile || []);
    setBackgroundLabel(roastDetail?.title || null);
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
  // numOrNull, not `Number(x) || null` -- the latter treats a threshold
  // of exactly 0 the same as blank/unset, silently disabling it instead
  // of respecting a genuinely-typed 0.
  const dryEndThreshold =
    activeMode === "simulator" ? SIMULATOR_DRY_END_C : LIVE_MODES.includes(activeMode) ? numOrNull(form.dry_end_c) : null;
  const fcStartThreshold =
    activeMode === "simulator" ? SIMULATOR_FC_START_C : LIVE_MODES.includes(activeMode) ? numOrNull(form.fc_start_c) : null;

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

  // Actual connection/config details (which port, which device profile,
  // etc.) are shown per-mode in the .live-meta list below once `roast`
  // exists, matching the existing alog_playback rows there (source
  // file/speed) -- not appended to this status line.
  const toolbarElement = (
    <ArtisanToolbar
      title={roast?.title || form.title}
      beans={roast?.beans || form.beans}
      weightGreenG={roast?.weight_green_g ?? (form.weight_green_g ? Number(form.weight_green_g) : null)}
      phase={phase}
      elapsedLabel={elapsedLabel}
      statusText={STATUS_TEXT[phase]}
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
                  {/* User's own configs first (in their existing order),
                      built-ins appended at the bottom and grouped by
                      manufacturer -- built-ins are non-deletable
                      reference templates (see handleDeletePreset's own
                      built_in check below), so keeping them out of the
                      way of a user's own growing list matters more as
                      more get added over time. */}
                  {presets.filter((p) => !p.built_in).map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} — {p.config.mode}
                    </option>
                  ))}
                  {Object.entries(
                    presets
                      .filter((p) => p.built_in)
                      .reduce((groups, p) => {
                        const label = p.manufacturer || "Built-in";
                        (groups[label] = groups[label] || []).push(p);
                        return groups;
                      }, {})
                  ).map(([manufacturer, group]) => (
                    <optgroup key={manufacturer} label={manufacturer}>
                      {group.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} — {p.config.mode}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </label>
              {selectedPresetId && !presets.find((p) => p.id === selectedPresetId)?.built_in && (
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
          <div className="roast-form-tabs">
            <button type="button" className={activeTab === "general" ? "active" : ""} onClick={() => setActiveTab("general")}>
              General
            </button>
            <button type="button" className={activeTab === "device" ? "active" : ""} onClick={() => setActiveTab("device")}>
              Device
            </button>
            {availableTabs.includes("milestones") && (
              <button type="button" className={activeTab === "milestones" ? "active" : ""} onClick={() => setActiveTab("milestones")}>
                Milestone / Automation
              </button>
            )}
          </div>
          {activeTab === "general" && (
            <>
              <div className="form-row">
                <label>
                  Title *
                  <input
                    required
                    value={form.title}
                    onChange={(e) => setForm({ ...form, title: e.target.value })}
                  />
                </label>
                <label>
                  Data source
                  {/* modbus_live covers both transports (see
                      modbus_transport above) -- USB and Ethernet are two
                      distinct dropdown entries here for clarity, but both
                      set mode="modbus_live", so they need their own
                      sentinel value/onChange mapping rather than just
                      mirroring form.mode 1:1. */}
                  <select
                    value={form.mode === "modbus_live" && form.modbus_transport === "tcp" ? "modbus_live_tcp" : form.mode}
                    onChange={(e) => {
                      const value = e.target.value;
                      if (value === "modbus_live_tcp") {
                        setForm({ ...form, mode: "modbus_live", modbus_transport: "tcp" });
                      } else if (value === "modbus_live") {
                        setForm({ ...form, mode: "modbus_live", modbus_transport: "serial" });
                      } else {
                        setForm({ ...form, mode: value });
                      }
                    }}
                  >
                    <option value="simulator">Simulator</option>
                    <option value="alog_playback">.alog Playback</option>
                    <option value="modbus_live">Direct Modbus (USB)</option>
                    <option value="modbus_live_tcp">Direct Modbus (Ethernet)</option>
                    <option value="ms6514_live">Direct USB (thermocouple meter)</option>
                    <option value="aillio_live">Aillio Bullet (USB)</option>
                    <option value="tc4_live">TC4+ (USB, PID firmware)</option>
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
            </>
          )}
          {activeTab === "device" && form.mode === "alog_playback" && (
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
          {activeTab === "device" && form.mode === "modbus_live" && (
            <div className="form-row">
              {form.modbus_transport === "tcp" ? (
                <>
                  <label>
                    Host / IP address
                    <input
                      placeholder="192.168.1.2"
                      value={form.modbus_host}
                      onChange={(e) => setForm({ ...form, modbus_host: e.target.value })}
                    />
                  </label>
                  <label>
                    TCP port
                    <input
                      type="number"
                      value={form.modbus_tcp_port}
                      onChange={(e) => setForm({ ...form, modbus_tcp_port: e.target.value })}
                    />
                  </label>
                </>
              ) : (
                <>
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
                    Separate drive port (optional)
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
                </>
              )}
              <label>
                Device profile
                <select
                  value={form.modbus_device_profile_id}
                  onChange={(e) => setForm({ ...form, modbus_device_profile_id: e.target.value })}
                >
                  {/* TCP has no flat-register "Custom" fallback -- a
                      profile is required (see RoastSession's own guard). */}
                  {form.modbus_transport !== "tcp" && <option value="">Custom (advanced fields below)</option>}
                  {deviceProfiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                      {p.built_in ? " (built-in)" : ""}
                    </option>
                  ))}
                </select>
              </label>
              {form.modbus_device_profile_id && (
                <p className="hint">
                  Using the "{deviceProfiles.find((p) => p.id === form.modbus_device_profile_id)?.name}" register
                  map -- the advanced fields below don't apply while a profile is selected. Pick "Custom" above to
                  go back to setting individual registers by hand.
                </p>
              )}
              <DeviceProfileEditor onChange={refreshDeviceProfiles} />
              {form.modbus_transport !== "tcp" && !form.modbus_device_profile_id && (
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
                        placeholder="2"
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
                      Air min RPM
                      <input
                        type="number"
                        placeholder="0"
                        value={form.modbus_air_min_pct}
                        onChange={(e) => setForm({ ...form, modbus_air_min_pct: e.target.value })}
                      />
                    </label>
                    <label>
                      Air max RPM
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
                        placeholder="1"
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
                      Drum min RPM
                      <input
                        type="number"
                        placeholder="0"
                        value={form.modbus_drum_min_pct}
                        onChange={(e) => setForm({ ...form, modbus_drum_min_pct: e.target.value })}
                      />
                    </label>
                    <label>
                      Drum max RPM
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
                        placeholder="260"
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
          {activeTab === "device" && form.mode === "ms6514_live" && (
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
          {activeTab === "device" && form.mode === "aillio_live" && (
            <div className="form-row">
              <label>
                Model
                <select
                  value={form.aillio_model}
                  onChange={(e) => setForm({ ...form, aillio_model: e.target.value })}
                >
                  <option value="r1">Bullet R1</option>
                </select>
              </label>
              <p className="hint">
                Talks directly to the roaster over USB (no port/host to configure — the app finds it by its
                own USB vendor/product id). Heater/Fan/Drum read back the device's own last-reported state,
                same "don't clobber real state" behavior as Direct Modbus.
              </p>
            </div>
          )}
          {activeTab === "device" && form.mode === "tc4_live" && (
            <div className="form-row">
              <label>
                Serial port
                <input
                  placeholder="COM5"
                  value={form.tc4_port}
                  onChange={(e) => setForm({ ...form, tc4_port: e.target.value })}
                />
              </label>
              <p className="hint">
                Direct USB read/write of a TC4+ shield running the aArtisanQ (PID) firmware, 115200 baud —
                talks straight over USB, no other software needed. Channel 1 → BT, channel 2 → ET, channel 3
                (if wired) → DT. Heater and Fan sliders send real OT1/DCFAN commands; there's no Drum output
                on TC4, so that slider doesn't do anything here.
              </p>
            </div>
          )}
          {activeTab === "milestones" && LIVE_MODES.includes(form.mode) && (
            <div className="form-row">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={form.auto_detect_milestones}
                  onChange={(e) => setForm({ ...form, auto_detect_milestones: e.target.checked })}
                />
                Auto-detect Charge/Dry End/FC Start from BT (opt-in)
              </label>
              <label>
                Dry End BT threshold (°C, blank to disable)
                <input
                  type="number"
                  value={form.dry_end_c}
                  disabled={!form.auto_detect_milestones}
                  onChange={(e) => setForm({ ...form, dry_end_c: e.target.value })}
                />
              </label>
              <label>
                FC Start BT threshold (°C, blank to disable)
                <input
                  type="number"
                  value={form.fc_start_c}
                  disabled={!form.auto_detect_milestones}
                  onChange={(e) => setForm({ ...form, fc_start_c: e.target.value })}
                />
              </label>
            </div>
          )}
          {activeTab === "device" && (AUTO_APPLY_STARTING_CONTROLS_MODES.includes(form.mode) || CONTROLLABLE_LIVE_MODES.includes(form.mode)) && (
            <div className="form-row">
              <label>
                Burner % at start
                <span className="input-suffix-group">
                  <input
                    type="number" min="0" max="100"
                    value={form.heater_pct}
                    onChange={(e) => setForm({ ...form, heater_pct: e.target.value })}
                  />
                  <span className="input-suffix">%</span>
                </span>
              </label>
              <label>
                {/* "RPM" matches the FZ-94's own real VFD drives; aillio_live's
                    Fan/Drum are a small device-native scale (remapped from
                    this same 0-100% field server-side, see
                    aillio_bridge/r1.py), not RPM, so it gets the generic "%"
                    label instead rather than a unit that isn't true for it. */}
                {form.mode === "modbus_live" ? "Air RPM at start" : "Fan % at start"}
                <span className="input-suffix-group">
                  <input
                    type="number" min="0" max="100"
                    value={form.fan_pct}
                    onChange={(e) => setForm({ ...form, fan_pct: e.target.value })}
                  />
                  <span className="input-suffix">{form.mode === "modbus_live" ? "RPM" : "%"}</span>
                </span>
              </label>
              <label>
                {form.mode === "modbus_live" ? "Drum RPM at start" : "Drum % at start"}
                <span className="input-suffix-group">
                  <input
                    type="number" min="0" max="100"
                    value={form.drum_speed_pct}
                    onChange={(e) => setForm({ ...form, drum_speed_pct: e.target.value })}
                  />
                  <span className="input-suffix">{form.mode === "modbus_live" ? "RPM" : "%"}</span>
                </span>
              </label>
              <p className="hint" style={{ flexBasis: "100%" }}>
                {shouldAutoApplyStartingControls
                  ? "Sent as the roast's first command right after START, and used as the Controls panel's starting position."
                  : CONTROLLABLE_LIVE_MODES.includes(form.mode)
                    ? "Only applied at START when loaded from a saved preset (see \"Load saved config\" above) -- a fresh start like this one leaves the roaster wherever it already is, so it's not clobbered by an unrelated stale value. Otherwise these are just what gets saved into a new preset below."
                    : "Sent as the roast's first command right after START, and used as the Controls panel's starting position."}
              </p>
            </div>
          )}
          {activeTab === "milestones" && CONTROLLABLE_LIVE_MODES.includes(form.mode) && (
            <p className="hint">
              {shouldAutoApplyStartingControls
                ? "Burner/Air/Drum start from this preset's saved values (see the Device tab), sent right after START -- from then on, the Controls panel on the Live Roast page reads and shows whatever the roaster is actually doing."
                : "Burner/Air/Drum aren't set here -- once connected, the Controls panel on the Live Roast page reads and shows whatever the roaster is actually doing (from the device itself, not a guess), and nothing is written to it until you move a slider yourself."}
            </p>
          )}
          {activeTab === "milestones" && form.mode === "modbus_live" && (
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
            {selectedPresetId && !presets.find((p) => p.id === selectedPresetId)?.built_in && (
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
              {CONTROLLABLE_MODES.includes(activeMode) && (
                <VerticalControlPanel
                  disabled={!isActive}
                  onSend={handleCommand}
                  // No form-value fallback here -- that would silently
                  // substitute a plausible-looking made-up number whenever
                  // `latest` hadn't arrived yet, indistinguishable from a
                  // register that's genuinely misconfigured/never going to
                  // arrive. Each slider renders null as an honest "—"/
                  // disabled state instead, which resolves the instant a
                  // real sample lands.
                  initial={{
                    heater_pct: latest?.heater_pct,
                    fan_pct: latest?.fan_pct,
                    drum_speed_pct: latest?.drum_speed_pct,
                    burner_sv_c: latest?.burner_sv_c,
                  }}
                  layout={verticalControlLayout}
                  arrows={verticalControlArrows}
                  svRangeC={roast?.burner_sv_range_c || null}
                  tempUnit={tempUnit}
                />
              )}
              <div className="scope-chart" ref={scopeChartRef}>
                <RoastChart
                  profile={roast?.profile || []}
                  events={roast?.events || []}
                  background={backgroundProfile}
                  backgroundLabel={backgroundLabel}
                  title={null}
                  height={chartHeight}
                  tempUnit={tempUnit}
                  interactive={!isRecording}
                  onDeleteEvent={handleDeleteMilestone}
                  onRetimeEvent={handleRetimeMilestone}
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
            {/* Moved out of .scope-chart -- as a sibling spanning the whole
                .scope-body row, this line (and the drag handle) now runs
                under the vertical control sliders and the Small Readout
                column too, not just the chart, so it actually reads as the
                bottom edge of the whole row instead of stopping short on
                both sides. Still resizes the same --scope-chart-height
                every one of those three columns already sizes itself off
                of, so dragging it still resizes all three together. */}
            <div
              className="scope-chart-resize-handle"
              title="Drag to resize the chart row (controls/chart/Small Readout follow it)"
              onPointerDown={handleChartResizePointerDown}
              onPointerMove={handleChartResizePointerMove}
              onPointerUp={handleChartResizePointerUp}
            />
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
            {/* Below the chart and milestone buttons, not above -- nothing
                should sit between opening this page and seeing the live
                curve once a roast is actually running. Picking/changing
                a background reference is a secondary, occasional action. */}
            <BackgroundProfilePicker
              excludeId={roastId}
              selectedId={backgroundRoastId}
              selectedTitle={backgroundLabel}
              onSelect={handleSelectBackground}
            />
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
                  {roast.mode === "modbus_live" && (
                    <li>
                      <span className="meta-label">Connection</span>
                      <span className="meta-value">
                        {roast.modbus_transport === "tcp"
                          ? `${roast.modbus_host}:${roast.modbus_tcp_port}`
                          : roast.modbus_port}
                        {roast.modbus_device_profile_name ? ` — ${roast.modbus_device_profile_name}` : ""}
                      </span>
                    </li>
                  )}
                  {roast.mode === "ms6514_live" && roast.ms6514_port && (
                    <li>
                      <span className="meta-label">Serial port</span>
                      <span className="meta-value">{roast.ms6514_port}</span>
                    </li>
                  )}
                  {roast.mode === "aillio_live" && roast.aillio_model && (
                    <li>
                      <span className="meta-label">Model</span>
                      <span className="meta-value">Aillio Bullet {roast.aillio_model.toUpperCase()}</span>
                    </li>
                  )}
                  {roast.mode === "tc4_live" && roast.tc4_port && (
                    <li>
                      <span className="meta-label">Serial port</span>
                      <span className="meta-value">{roast.tc4_port}</span>
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

          {/* Batch info (beans/tags/weights) split out of the connection-facts
              header above into its own panel -- mixing short read-only mode
              details with increasingly wide interactive editors (tags,
              weight) in one inline-wrapping row got genuinely hard to read
              as those editors were added one at a time. Same kv-list
              label-left/value-right pattern RoastDetailView.jsx's own
              "Batch" section already uses, for the same fields. */}
          {roast && (
            <div className="panel live-batch">
              <ul className="kv-list">
                {roast.beans && (
                  <li>
                    <span>Beans</span>
                    <span>{roast.beans}</span>
                  </li>
                )}
                <li>
                  <span>Tags</span>
                  <span className="tag-edit-group">
                    {(tagsSaved ?? roast.tags ?? []).map((t) => (
                      <span key={t} className="tag-chip">
                        {t}
                        <button type="button" className="tag-chip-remove" onClick={() => handleRemoveTag(t)} aria-label={`Remove tag ${t}`}>
                          ×
                        </button>
                      </span>
                    ))}
                    <span className="input-suffix-group">
                      <input
                        type="text"
                        value={newTagInput}
                        onChange={(e) => setNewTagInput(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            handleAddTag();
                          }
                        }}
                        placeholder="Add tag…"
                        list="existing-tags-live"
                      />
                      <button type="button" onClick={handleAddTag} disabled={!newTagInput.trim()}>
                        Add
                      </button>
                      <datalist id="existing-tags-live">
                        {allTags
                          .filter((t) => !(tagsSaved ?? roast.tags ?? []).includes(t.tag))
                          .map((t) => (
                            <option key={t.tag} value={t.tag} />
                          ))}
                      </datalist>
                    </span>
                  </span>
                </li>
                {tagsError && (
                  <li>
                    <span></span>
                    <span className="error">{tagsError}</span>
                  </li>
                )}
                <li>
                  <span>Green weight</span>
                  <span>
                    <WeightField value={greenWeightValue} onSave={handleSaveGreenWeight} onDelete={handleDeleteGreenWeight} />
                  </span>
                </li>
                {phase === "finished" && (
                  <li>
                    <span>Roasted weight</span>
                    <span>
                      <WeightField value={roastedWeightValue} onSave={handleSaveRoastedWeight} onDelete={handleDeleteRoastedWeight} />
                    </span>
                  </li>
                )}
                {phase === "finished" && greenWeightValue && roastedWeightValue != null && (
                  <li>
                    <span>Weight loss</span>
                    <span>{(((roastedWeightValue / greenWeightValue) - 1) * 100).toFixed(1)}%</span>
                  </li>
                )}
              </ul>
            </div>
          )}

          {phase === "finished" && roast && (
            <div className="panel">
              <h3>Roast Stats</h3>
              <RoastStatsPanel roastId={roast.id} />
            </div>
          )}

          <div className="live-grid">
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
                The controls beside the chart read and write directly over{" "}
                {form.modbus_transport === "tcp" ? form.modbus_host || "the configured host" : form.modbus_port || "the serial port"}:
                Burner is a drum-temperature setpoint (register 5, default 100–250°C — not a power %, shown as
                both a % slider and a direct °C slider that move each other), Air and Drum are VFD drives
                (run/stop + frequency registers 8192/8193, default 0–100%/0–70%), each with its own feedback
                register (8451) reporting the drive's actual current speed. Out-of-range values are clamped to
                the configured range. See "Advanced Modbus register map" above to override any of these for
                your own unit. Every milestone (Charge, Dry End, FC Start, Drop, etc.) is a manual click — mark
                them yourself as the roast happens.
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
            {activeMode === "tc4_live" && (
              <div className="panel control-panel">
                <h3>TC4+</h3>
                <p className="hint">
                  Reading/writing {form.tc4_port || "the serial port"} directly (115200 baud, aArtisanQ/PID
                  firmware) — no other software needed. The Heater/Fan sliders beside the chart send real OT1/
                  DCFAN commands; there's no Drum output on TC4, so that slider doesn't do anything here.
                  Heater/Fan readouts stay blank — TC4's own READ command only reports temperature channels, not
                  its current output duty. Every milestone (Charge, Dry End, FC Start, Drop, etc.) is a manual
                  click — mark them yourself as the roast happens.
                </p>
                <div className="form-row">
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={tc4PidEnabled}
                      disabled={!isActive}
                      onChange={(e) => {
                        const enabled = e.target.checked;
                        setTc4PidEnabled(enabled);
                        handleCommand({ tc4_pid_enabled: enabled });
                      }}
                    />
                    Enable onboard PID
                  </label>
                  <label>
                    Target BT (°C)
                    <span className="input-suffix-group">
                      <input
                        type="number"
                        value={tc4PidTargetInput}
                        onChange={(e) => setTc4PidTargetInput(e.target.value)}
                        placeholder="e.g. 200"
                        disabled={!isActive}
                      />
                      <button
                        type="button"
                        disabled={!isActive || !tc4PidTargetInput.trim()}
                        onClick={() => handleCommand({ tc4_pid_target_c: Number(tc4PidTargetInput) })}
                      >
                        Set
                      </button>
                    </span>
                  </label>
                </div>
                <p className="hint">The Heater slider has no effect while onboard PID is enabled.</p>
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
