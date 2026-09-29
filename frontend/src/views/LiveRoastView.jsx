import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { activeRoastStreamUrl, api, settingsStreamUrl } from "../api/client.js";
import { useRoastStream } from "../api/ws.js";
import SimulatedDeviceHint from "../components/SimulatedDeviceHint.jsx";
import { isSimulatedForm, isSimulatedRoast } from "../simulated.js";
import RoastToolbar from "../components/RoastToolbar.jsx";
import useAwayAlarm from "../useAwayAlarm.js";
import useServerStatus from "../useServerStatus.js";
import BackgroundProfilePicker from "../components/BackgroundProfilePicker.jsx";
import BreakoutPanel from "../components/BreakoutPanel.jsx";
import { SMALL_READOUT_EXCLUDED_KEYS } from "../breakoutPanels.js";
import AlarmRulesEditor from "../components/AlarmRulesEditor.jsx";
import ConnectionTestPanel from "../components/ConnectionTestPanel.jsx";
import VerticalControlPanel from "../components/VerticalControlPanel.jsx";
import DeviceProfileEditor from "../components/DeviceProfileEditor.jsx";
import EventButtonRow from "../components/EventButtonRow.jsx";
import RoastChart from "../components/RoastChart.jsx";
import RoastStatsPanel from "../components/RoastStatsPanel.jsx";
import WeightField from "../components/WeightField.jsx";
import NotesPanel from "../components/NotesPanel.jsx";
import BeansField from "../components/BeansField.jsx";
import AutoControlPanel from "../components/AutoControlPanel.jsx";
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
  // Before Charge the roast clock is negative ("-00:06"), not "-1:-6".
  const total = Math.round(Math.abs(seconds));
  const sign = seconds < 0 && total > 0 ? "-" : "";
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${sign}${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
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

export default function LiveRoastView() {
  const { t } = useTranslation();
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
    // instead) -- see the Connection type dropdown below, which maps its
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
    // Milestone-triggered automations (alarm-style rules), modbus_live
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
    modbus_air_frequency_scale: "",
    modbus_air_frequency_offset: "",
    modbus_drum_slave_id: "",
    modbus_drum_control_register: "",
    modbus_drum_frequency_register: "",
    modbus_drum_feedback_register: "",
    modbus_drum_min_pct: "",
    modbus_drum_max_pct: "",
    modbus_drum_frequency_scale: "",
    modbus_drum_frequency_offset: "",
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
  // Real ports, plus the simulated devices that belong to this data source.
  function portsFor(mode) {
    return serialPorts.filter((p) => !p.simulated || p.mode === mode);
  }
  const [phase, setPhase] = useState("idle"); // idle | armed | roasting | cooling | finished
  const [roastId, setRoastId] = useState(null);
  // Let the active-roast SSE effect below (mount-once, so it can't just
  // close over `phase`/`roastId`) always check their *current* values, not
  // whatever they were when that effect first subscribed.
  const phaseRef = useRef(phase);
  const roastIdRef = useRef(roastId);
  useEffect(() => {
    phaseRef.current = phase;
    roastIdRef.current = roastId;
  }, [phase, roastId]);
  // Background Profile (see BackgroundProfilePicker.jsx/RoastChart.jsx) --
  // a purely visual reference overlay, never touched by anything else in
  // this view (no automation, no control writes read this). Not tied to
  // roastId -- cleared explicitly by the user or a fresh mount, not by
  // starting a new roast, so you can keep pacing against the same
  // reference across several attempts without re-picking it every time.
  const [backgroundRoastId, setBackgroundRoastId] = useState(null);
  const [backgroundProfile, setBackgroundProfile] = useState([]);
  const [backgroundLabel, setBackgroundLabel] = useState(null);
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
  const [awayAlarmEnabled, setAwayAlarmEnabled] = useState(true); // Settings > Roaster safety
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

  const { roast, setRoast, latestPreview, lastError, pendingAlarms, alarmNotifications, dismissAlarmNotification } =
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
      // Not `s.away_alarm_enabled ?? true` -- that treats false the same
      // as "unset" and would force the alarm back on for anyone who
      // turned it off.
      setAwayAlarmEnabled(s.away_alarm_enabled !== false);
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

  // A roast started from another device/tab (e.g. a phone) doesn't touch
  // this tab at all -- without a push, a tab left sitting on the idle
  // Configure Roast form would only ever find out by a manual reload,
  // since reconnectActiveRoast() below only runs once on mount. Server
  // pushes whenever any roast starts recording (see roasts.py's
  // /roasts/active/stream and session.py's active_roast_pubsub.publish()
  // calls), same push-not-poll pattern as the settings stream above.
  useEffect(() => {
    const source = new EventSource(activeRoastStreamUrl());
    source.addEventListener("active_roast", (e) => {
      // Skip only when this tab is itself actively recording (roasting/
      // cooling) or has its own roastId -- the latter covers "armed" with a
      // real hardware connection already open (POST /roasts already made,
      // see handleToggleConnect), which reconnectActiveRoast() would
      // otherwise silently abandon by overwriting roastId/phase out from
      // under it. A plain "armed" with no roastId yet (simulator/
      // alog_playback, before the real POST /roasts on START) has nothing
      // to lose, so it's fine to fall through and reconnect.
      if (phaseRef.current === "roasting" || phaseRef.current === "cooling" || roastIdRef.current) return;
      let info;
      try {
        info = JSON.parse(e.data);
      } catch {
        return;
      }
      if (info) reconnectActiveRoast();
    });
    return () => source.close();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
    setError(t("liveRoast.requireTitleError"));
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
      payload.modbus_air_frequency_scale = numOrNull(form.modbus_air_frequency_scale);
      payload.modbus_air_frequency_offset = numOrNull(form.modbus_air_frequency_offset);
      payload.modbus_drum_slave_id = numOrNull(form.modbus_drum_slave_id);
      payload.modbus_drum_control_register = numOrNull(form.modbus_drum_control_register);
      payload.modbus_drum_frequency_register = numOrNull(form.modbus_drum_frequency_register);
      payload.modbus_drum_feedback_register = numOrNull(form.modbus_drum_feedback_register);
      payload.modbus_drum_min_pct = numOrNull(form.modbus_drum_min_pct);
      payload.modbus_drum_max_pct = numOrNull(form.modbus_drum_max_pct);
      payload.modbus_drum_frequency_scale = numOrNull(form.modbus_drum_frequency_scale);
      payload.modbus_drum_frequency_offset = numOrNull(form.modbus_drum_frequency_offset);
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
  // useCallback -- see RoastDetailView.jsx's identical comment on its own
  // handleDeleteMilestone/handleRetimeMilestone for why: a fresh function
  // identity here on every render (even one unrelated to the chart)
  // cascades into RoastChart's memoized `options` object, which makes
  // react-chartjs-2 reapply the x-axis's fixed bounds and silently wipe
  // out the user's zoom/pan.
  const handleDeleteMilestone = useCallback(
    async (eventId) => {
      if (!roastId) return;
      try {
        await api.deleteEvent(roastId, eventId);
      } catch (err) {
        setError(err.message);
      }
    },
    [roastId]
  );

  const handleRetimeMilestone = useCallback(
    async (eventId, timeS) => {
      if (!roastId) return;
      try {
        await api.retimeEvent(roastId, eventId, timeS);
      } catch (err) {
        setError(err.message);
      }
    },
    [roastId]
  );

  async function handleSavePreset() {
    if (!presetName.trim()) return;
    setError(null);
    setPresetFeedback(null);
    try {
      const trimmed = presetName.trim();
      await api.savePreset(trimmed, buildConfigFromForm(), buildControlsFromForm());
      setPresetName("");
      setSelectedPresetId("");
      setPresetFeedback(t("liveRoast.savedFeedback", { name: trimmed }));
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
      setPresetFeedback(t("liveRoast.updatedFeedback", { name: trimmed }));
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
      modbus_air_frequency_scale: c.modbus_air_frequency_scale ?? "",
      modbus_air_frequency_offset: c.modbus_air_frequency_offset ?? "",
      modbus_drum_slave_id: c.modbus_drum_slave_id ?? "",
      modbus_drum_control_register: c.modbus_drum_control_register ?? "",
      modbus_drum_frequency_register: c.modbus_drum_frequency_register ?? "",
      modbus_drum_feedback_register: c.modbus_drum_feedback_register ?? "",
      modbus_drum_min_pct: c.modbus_drum_min_pct ?? "",
      modbus_drum_max_pct: c.modbus_drum_max_pct ?? "",
      modbus_drum_frequency_scale: c.modbus_drum_frequency_scale ?? "",
      modbus_drum_frequency_offset: c.modbus_drum_frequency_offset ?? "",
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
      setPresetFeedback(name ? t("liveRoast.deletedFeedback", { name }) : t("liveRoast.deletedFeedbackPlain"));
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

  // "idle" here means "connected via connect(), not yet recording" (see
  // RoastSession.connect()/apply_command() on the backend) -- lets
  // Fan/Drum/Burner controls (and Testing Mode's checks) work while
  // merely armed (controls work before recording starts), not
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
  // RoR gets computed live) -- the elapsed timer
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

  // A repeating audio alarm if this tab is hidden (minimized, switched
  // away from) while actually roasting -- heat is being applied, not
  // just connected/armed. See useAwayAlarm.js for how this differs from
  // the server-side "no viewer" watchdog in Settings > Roaster safety.
  // Settings > Roaster safety can turn it off entirely too.
  useAwayAlarm(phase === "roasting" && awayAlarmEnabled);

  // Server OS/version/LAN address, shown in the connection-details list
  // below alongside the mode-specific fields (Serial port, Connection,
  // etc.) -- useful for confirming which machine on the network actually
  // has the roaster plugged in, same reasoning as the footer's own copy
  // of this (a separate independent poll -- same precedent as
  // RoastToolbar/AutoControlPanel each polling /control on their own).
  const { platform: serverPlatform, osVersion: serverOsVersion, lanIp: serverLanIp } = useServerStatus();

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
  // True while a simulated device is (or is about to be) behind this roast --
  // shown as a badge on the toolbar and a note in Test Connection.
  const simulated = roast ? isSimulatedRoast(roast) : isSimulatedForm(form);

  const toolbarElement = (
    <RoastToolbar
      title={roast?.title || form.title}
      beans={roast?.beans || form.beans}
      weightGreenG={roast?.weight_green_g ?? (form.weight_green_g ? Number(form.weight_green_g) : null)}
      phase={phase}
      elapsedLabel={elapsedLabel}
      statusText={t(`liveRoast.statusText.${phase}`)}
      onToggleConnect={handleToggleConnect}
      onStart={handleStart}
      simulated={simulated}
      roastId={roastId}
      showEmergencyStop={CONTROLLABLE_MODES.includes(activeMode)}
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
          <h2>{t("liveRoast.configureRoast")}</h2>
          {presets.length > 0 && (
            <div className="form-row">
              <label>
                {t("liveRoast.loadSavedConfig")}
                <select value={selectedPresetId} onChange={(e) => handleLoadPreset(e.target.value)}>
                  <option value="">{t("liveRoast.none")}</option>
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
                  {t("liveRoast.deleteSelectedConfig")}
                </button>
              )}
            </div>
          )}
          <div className="roast-form-tabs">
            <button type="button" className={activeTab === "general" ? "active" : ""} onClick={() => setActiveTab("general")}>
              {t("liveRoast.tabs.general")}
            </button>
            <button type="button" className={activeTab === "device" ? "active" : ""} onClick={() => setActiveTab("device")}>
              {t("liveRoast.tabs.device")}
            </button>
            {availableTabs.includes("milestones") && (
              <button type="button" className={activeTab === "milestones" ? "active" : ""} onClick={() => setActiveTab("milestones")}>
                {t("liveRoast.tabs.milestones")}
              </button>
            )}
          </div>
          {activeTab === "general" && (
            <>
              <div className="form-row">
                <label>
                  {t("liveRoast.title")}
                  <input
                    required
                    value={form.title}
                    onChange={(e) => setForm({ ...form, title: e.target.value })}
                  />
                </label>
                <label>
                  {t("liveRoast.connectionType")}
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
                    <option value="simulator">{t("liveRoast.connectionOptions.simulator")}</option>
                    <option value="alog_playback">{t("liveRoast.connectionOptions.alogPlayback")}</option>
                    <option value="modbus_live">{t("liveRoast.connectionOptions.modbusUsb")}</option>
                    <option value="modbus_live_tcp">{t("liveRoast.connectionOptions.modbusEthernet")}</option>
                    <option value="ms6514_live">{t("liveRoast.connectionOptions.ms6514")}</option>
                    <option value="aillio_live">{t("liveRoast.connectionOptions.aillio")}</option>
                    <option value="tc4_live">{t("liveRoast.connectionOptions.tc4")}</option>
                  </select>
                </label>
              </div>
              <div className="form-row">
                <BeansField className="form-field" value={form.beans} onChange={(text) => setForm({ ...form, beans: text })} />
                <label>
                  {t("liveRoast.greenWeightG")}
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
                {t("liveRoast.alogPath")}
                <input value={form.alog_path} onChange={(e) => setForm({ ...form, alog_path: e.target.value })} />
              </label>
              <label>
                {t("liveRoast.playbackSpeed")}
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
                    {t("liveRoast.hostIpAddress")}
                    <input
                      placeholder="192.168.1.2"
                      value={form.modbus_host}
                      onChange={(e) => setForm({ ...form, modbus_host: e.target.value })}
                    />
                    <SimulatedDeviceHint kind="fz94_evo" value={form.modbus_host} onChange={(v) => setForm({ ...form, modbus_host: v })} />
                  </label>
                  <label>
                    {t("liveRoast.tcpPort")}
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
                    {portsFor("modbus_live").map((p) => (
                      <option key={p.device} value={p.device} label={p.description || undefined} />
                    ))}
                  </datalist>
                  <label>
                    {t("liveRoast.serialPort")}
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
                        title={t("liveRoast.rescanPorts")}
                      >
                        {serialPortsLoading ? "…" : "⟳"}
                      </button>
                    </span>
                    {portsFor("modbus_live").every((p) => p.simulated) && !serialPortsLoading && (
                      <span className="hint">{t("liveRoast.noSerialPortsDetected")}</span>
                    )}
                    <SimulatedDeviceHint kind="fz94" value={form.modbus_port} onChange={(v) => setForm({ ...form, modbus_port: v })} />
                  </label>
                  <label>
                    {t("liveRoast.baudRate")}
                    <input
                      type="number"
                      value={form.modbus_baudrate}
                      onChange={(e) => setForm({ ...form, modbus_baudrate: e.target.value })}
                    />
                  </label>
                  <label>
                    {t("liveRoast.separateDrivePort")}
                    <input
                      placeholder={t("liveRoast.separateDrivePortPlaceholder")}
                      list="serial-ports-list"
                      value={form.modbus_control_port}
                      onChange={(e) => setForm({ ...form, modbus_control_port: e.target.value })}
                    />
                  </label>
                  <label>
                    {t("liveRoast.driveBaudRate")}
                    <input
                      type="number"
                      value={form.modbus_control_baudrate}
                      onChange={(e) => setForm({ ...form, modbus_control_baudrate: e.target.value })}
                    />
                  </label>
                </>
              )}
              <label>
                {t("liveRoast.deviceProfile")}
                <select
                  value={form.modbus_device_profile_id}
                  onChange={(e) => setForm({ ...form, modbus_device_profile_id: e.target.value })}
                >
                  {/* TCP has no flat-register "Custom" fallback -- a
                      profile is required (see RoastSession's own guard). */}
                  {form.modbus_transport !== "tcp" && <option value="">{t("liveRoast.customAdvancedFields")}</option>}
                  {deviceProfiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                      {p.built_in ? t("liveRoast.builtIn") : ""}
                    </option>
                  ))}
                </select>
              </label>
              {form.modbus_device_profile_id && (
                <p className="hint">
                  {t("liveRoast.usingProfileHint", {
                    name: deviceProfiles.find((p) => p.id === form.modbus_device_profile_id)?.name,
                  })}
                </p>
              )}
              <DeviceProfileEditor onChange={refreshDeviceProfiles} />
              {form.modbus_transport !== "tcp" && !form.modbus_device_profile_id && (
                <div className="advanced-modbus-fields">
                  <p className="hint">{t("liveRoast.advancedModbusHint")}</p>
                  <div className="form-row">
                    <label>
                      {t("liveRoast.modbus.slaveId", { ch: "BT" })}
                      <input
                        type="number"
                        placeholder="11"
                        value={form.modbus_bt_slave_id}
                        onChange={(e) => setForm({ ...form, modbus_bt_slave_id: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.register", { ch: "BT" })}
                      <input
                        type="number"
                        placeholder="0"
                        value={form.modbus_bt_register}
                        onChange={(e) => setForm({ ...form, modbus_bt_register: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.divisor", { ch: "BT" })}
                      <input
                        type="number"
                        placeholder="10"
                        value={form.modbus_bt_divisor}
                        onChange={(e) => setForm({ ...form, modbus_bt_divisor: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.slaveId", { ch: "ET" })}
                      <input
                        type="number"
                        placeholder="13"
                        value={form.modbus_et_slave_id}
                        onChange={(e) => setForm({ ...form, modbus_et_slave_id: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.register", { ch: "ET" })}
                      <input
                        type="number"
                        placeholder="0"
                        value={form.modbus_et_register}
                        onChange={(e) => setForm({ ...form, modbus_et_register: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.divisor", { ch: "ET" })}
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
                      {t("liveRoast.modbus.slaveId", { ch: "DT" })}
                      <input
                        type="number"
                        placeholder="12"
                        value={form.modbus_dt_slave_id}
                        onChange={(e) => setForm({ ...form, modbus_dt_slave_id: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.register", { ch: "DT" })}
                      <input
                        type="number"
                        placeholder="0"
                        value={form.modbus_dt_register}
                        onChange={(e) => setForm({ ...form, modbus_dt_register: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.divisor", { ch: "DT" })}
                      <input
                        type="number"
                        placeholder="10"
                        value={form.modbus_dt_divisor}
                        onChange={(e) => setForm({ ...form, modbus_dt_divisor: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.slaveId", { ch: "Burner" })}
                      <input
                        type="number"
                        placeholder="12"
                        value={form.modbus_burner_slave_id}
                        onChange={(e) => setForm({ ...form, modbus_burner_slave_id: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.register", { ch: "Burner" })}
                      <input
                        type="number"
                        placeholder="5"
                        value={form.modbus_burner_register}
                        onChange={(e) => setForm({ ...form, modbus_burner_register: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.divisor", { ch: "Burner" })}
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
                      {t("liveRoast.modbus.slaveId", { ch: "Fan" })}
                      <input
                        type="number"
                        placeholder="2"
                        value={form.modbus_air_slave_id}
                        onChange={(e) => setForm({ ...form, modbus_air_slave_id: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.runStopRegister", { ch: "Fan" })}
                      <input
                        type="number"
                        placeholder="8192"
                        value={form.modbus_air_control_register}
                        onChange={(e) => setForm({ ...form, modbus_air_control_register: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.frequencyRegister", { ch: "Fan" })}
                      <input
                        type="number"
                        placeholder="8193"
                        value={form.modbus_air_frequency_register}
                        onChange={(e) => setForm({ ...form, modbus_air_frequency_register: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.feedbackRegister", { ch: "Fan" })}
                      <input
                        type="number"
                        placeholder="8451"
                        value={form.modbus_air_feedback_register}
                        onChange={(e) => setForm({ ...form, modbus_air_feedback_register: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.factor", { ch: "Fan" })}
                      <input
                        type="number"
                        placeholder="100"
                        value={form.modbus_air_frequency_scale}
                        onChange={(e) => setForm({ ...form, modbus_air_frequency_scale: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.offset", { ch: "Fan" })}
                      <input
                        type="number"
                        placeholder="0"
                        value={form.modbus_air_frequency_offset}
                        onChange={(e) => setForm({ ...form, modbus_air_frequency_offset: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.minRpm", { ch: "Fan" })}
                      <input
                        type="number"
                        placeholder="0"
                        value={form.modbus_air_min_pct}
                        onChange={(e) => setForm({ ...form, modbus_air_min_pct: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.maxRpm", { ch: "Fan" })}
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
                      {t("liveRoast.modbus.slaveId", { ch: "Drum" })}
                      <input
                        type="number"
                        placeholder="1"
                        value={form.modbus_drum_slave_id}
                        onChange={(e) => setForm({ ...form, modbus_drum_slave_id: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.runStopRegister", { ch: "Drum" })}
                      <input
                        type="number"
                        placeholder="8192"
                        value={form.modbus_drum_control_register}
                        onChange={(e) => setForm({ ...form, modbus_drum_control_register: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.frequencyRegister", { ch: "Drum" })}
                      <input
                        type="number"
                        placeholder="8193"
                        value={form.modbus_drum_frequency_register}
                        onChange={(e) => setForm({ ...form, modbus_drum_frequency_register: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.feedbackRegister", { ch: "Drum" })}
                      <input
                        type="number"
                        placeholder="8451"
                        value={form.modbus_drum_feedback_register}
                        onChange={(e) => setForm({ ...form, modbus_drum_feedback_register: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.factor", { ch: "Drum" })}
                      <input
                        type="number"
                        placeholder="100"
                        value={form.modbus_drum_frequency_scale}
                        onChange={(e) => setForm({ ...form, modbus_drum_frequency_scale: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.offset", { ch: "Drum" })}
                      <input
                        type="number"
                        placeholder="0"
                        value={form.modbus_drum_frequency_offset}
                        onChange={(e) => setForm({ ...form, modbus_drum_frequency_offset: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.minRpm", { ch: "Drum" })}
                      <input
                        type="number"
                        placeholder="0"
                        value={form.modbus_drum_min_pct}
                        onChange={(e) => setForm({ ...form, modbus_drum_min_pct: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.modbus.maxRpm", { ch: "Drum" })}
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
                      {t("liveRoast.burnerSvMin")}
                      <input
                        type="number"
                        placeholder="100"
                        value={form.modbus_burner_sv_min_c}
                        onChange={(e) => setForm({ ...form, modbus_burner_sv_min_c: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("liveRoast.burnerSvMax")}
                      <input
                        type="number"
                        placeholder="260"
                        value={form.modbus_burner_sv_max_c}
                        onChange={(e) => setForm({ ...form, modbus_burner_sv_max_c: e.target.value })}
                      />
                    </label>
                    <p className="hint" style={{ flexBasis: "100%" }}>
                      {t("liveRoast.burnerSvPairHint")}
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}
          {activeTab === "device" && form.mode === "ms6514_live" && (
            <div className="form-row">
              <label>
                {t("liveRoast.serialPort")}
                <input
                  placeholder="COM5"
                  list="serial-ports-ms6514"
                  value={form.ms6514_port}
                  onChange={(e) => setForm({ ...form, ms6514_port: e.target.value })}
                />
                <datalist id="serial-ports-ms6514">
                  {portsFor("ms6514_live").map((p) => (
                    <option key={p.device} value={p.device} label={p.description || undefined} />
                  ))}
                </datalist>
                <SimulatedDeviceHint kind="ms6514" value={form.ms6514_port} onChange={(v) => setForm({ ...form, ms6514_port: v })} />
              </label>
              <p className="hint">{t("liveRoast.ms6514Hint")}</p>
            </div>
          )}
          {activeTab === "device" && form.mode === "aillio_live" && (
            <div className="form-row">
              <label>
                {t("liveRoast.model")}
                <select
                  value={form.aillio_model}
                  onChange={(e) => setForm({ ...form, aillio_model: e.target.value })}
                >
                  <option value="r1">{t("liveRoast.bulletR1")}</option>
                </select>
              </label>
              <p className="hint">{t("liveRoast.aillioHint")}</p>
            </div>
          )}
          {activeTab === "device" && form.mode === "tc4_live" && (
            <div className="form-row">
              <label>
                {t("liveRoast.serialPort")}
                <input
                  placeholder="COM5"
                  list="serial-ports-tc4"
                  value={form.tc4_port}
                  onChange={(e) => setForm({ ...form, tc4_port: e.target.value })}
                />
                <datalist id="serial-ports-tc4">
                  {portsFor("tc4_live").map((p) => (
                    <option key={p.device} value={p.device} label={p.description || undefined} />
                  ))}
                </datalist>
                <SimulatedDeviceHint kind="tc4" value={form.tc4_port} onChange={(v) => setForm({ ...form, tc4_port: v })} />
              </label>
              <p className="hint">{t("liveRoast.tc4Hint")}</p>
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
                {t("liveRoast.autoDetectMilestones")}
              </label>
              <label>
                {t("liveRoast.dryEndThreshold")}
                <input
                  type="number"
                  value={form.dry_end_c}
                  disabled={!form.auto_detect_milestones}
                  onChange={(e) => setForm({ ...form, dry_end_c: e.target.value })}
                />
              </label>
              <label>
                {t("liveRoast.fcStartThreshold")}
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
                {t("liveRoast.burnerPctAtStart")}
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
                {/* Every live mode's Fan/Drum is a 0-100% share of the
                    drive's own max speed -- the FZ-94's VFD registers,
                    Aillio's small device-native scale (remapped from this
                    same field server-side, see aillio_bridge/r1.py) -- so
                    this no longer needs a mode-specific label/suffix. */}
                {t("liveRoast.fanPctAtStart")}
                <span className="input-suffix-group">
                  <input
                    type="number" min="0" max="100"
                    value={form.fan_pct}
                    onChange={(e) => setForm({ ...form, fan_pct: e.target.value })}
                  />
                  <span className="input-suffix">%</span>
                </span>
              </label>
              <label>
                {t("liveRoast.drumPctAtStart")}
                <span className="input-suffix-group">
                  <input
                    type="number" min="0" max="100"
                    value={form.drum_speed_pct}
                    onChange={(e) => setForm({ ...form, drum_speed_pct: e.target.value })}
                  />
                  <span className="input-suffix">%</span>
                </span>
              </label>
              <p className="hint" style={{ flexBasis: "100%" }}>
                {shouldAutoApplyStartingControls
                  ? t("liveRoast.startingControlsHintAlways")
                  : CONTROLLABLE_LIVE_MODES.includes(form.mode)
                    ? t("liveRoast.startingControlsHintPresetOnly")
                    : t("liveRoast.startingControlsHintAlways")}
              </p>
            </div>
          )}
          {activeTab === "milestones" && CONTROLLABLE_LIVE_MODES.includes(form.mode) && (
            <p className="hint">
              {shouldAutoApplyStartingControls
                ? t("liveRoast.milestonesHintPreset")
                : t("liveRoast.milestonesHintNoPreset")}
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
              {selectedPresetId ? t("liveRoast.configName") : t("liveRoast.saveConfigAs")}
              <input
                placeholder={t("liveRoast.savePresetPlaceholder")}
                value={presetName}
                onChange={(e) => {
                  setPresetName(e.target.value);
                  setPresetFeedback(null);
                }}
              />
            </label>
            {selectedPresetId && !presets.find((p) => p.id === selectedPresetId)?.built_in && (
              <button type="button" onClick={handleUpdatePreset} disabled={!presetName.trim()}>
                {t("liveRoast.updateConfig", { name: presets.find((p) => p.id === selectedPresetId)?.name })}
              </button>
            )}
            <button type="button" onClick={handleSavePreset} disabled={!presetName.trim()}>
              {t("liveRoast.saveAsNewConfig")}
            </button>
          </div>
          {presetFeedback && <p className="hint preset-feedback">{presetFeedback}</p>}
          {/* No visible submit button here -- the toolbar's ON/OFF toggle above
              is the single control for this action. The form keeps onSubmit
              so pressing Enter in a field still arms it. */}
          <p className="hint">{t("liveRoast.pressOnHint")}</p>
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
              committing to a roast. The ambient connection-status dot
              itself lives in RoastToolbar (see toolbarElement below) --
              it stays visible through roasting/cooling too, not just here. */}
          {phase === "armed" && LIVE_MODES.includes(activeMode) && (
            <ConnectionTestPanel roastId={roastId} latest={latest} mode={activeMode} tempUnit={tempUnit} simulated={simulated} />
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
                  the big panel. The wrapper itself (not just BreakoutPanel
                  inside it) is skipped when nothing's enabled -- otherwise
                  its fixed width (.small-readout-col in styles.css) sits
                  there empty and .scope-chart's flex: 1 never gets to
                  reclaim that space. */}
              {smallReadoutPanels.length > 0 && (
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
              )}
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
              title={t("liveRoast.resizeChartHandle")}
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
                  .map((a) => t("liveRoast.pendingAlarmText", { trigger: a.trigger.replace("_", " "), seconds: a.delaySeconds }))
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
                  {roast.mode} · {t("liveRoast.statusLabel")} <strong>{roast.status}</strong>
                </p>
                <ul className="live-meta">
                  {roast.mode === "alog_playback" && roast.source_alog_path && (
                    <li>
                      <span className="meta-label">{t("liveRoast.sourceFile")}</span>
                      <span className="meta-value">{roast.source_alog_path}</span>
                    </li>
                  )}
                  {roast.mode === "alog_playback" && roast.playback_speed != null && (
                    <li>
                      <span className="meta-label">{t("liveRoast.playbackSpeed")}</span>
                      <span className="meta-value">{roast.playback_speed}x</span>
                    </li>
                  )}
                  {roast.mode === "modbus_live" && (
                    <li>
                      <span className="meta-label">{t("liveRoast.connection")}</span>
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
                      <span className="meta-label">{t("liveRoast.serialPort")}</span>
                      <span className="meta-value">{roast.ms6514_port}</span>
                    </li>
                  )}
                  {roast.mode === "aillio_live" && roast.aillio_model && (
                    <li>
                      <span className="meta-label">{t("liveRoast.model")}</span>
                      <span className="meta-value">{t("liveRoast.aillioBulletModel", { model: roast.aillio_model.toUpperCase() })}</span>
                    </li>
                  )}
                  {roast.mode === "tc4_live" && roast.tc4_port && (
                    <li>
                      <span className="meta-label">{t("liveRoast.serialPort")}</span>
                      <span className="meta-value">{roast.tc4_port}</span>
                    </li>
                  )}
                  {serverPlatform && (
                    <li>
                      <span className="meta-label">{t("liveRoast.server")}</span>
                      <span className="meta-value">
                        {serverPlatform}
                        {serverOsVersion ? ` ${serverOsVersion}` : ""}
                        {serverLanIp ? ` — ${serverLanIp}` : ""}
                      </span>
                    </li>
                  )}
                </ul>
              </div>
              <div className="live-header-actions">
                {phase === "finished" && <Link to={`/roasts/${roastId}`}>{t("liveRoast.viewDetail")}</Link>}
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
                    <span>{t("liveRoast.beans")}</span>
                    <span>{roast.beans}</span>
                  </li>
                )}
                <li>
                  <span>{t("liveRoast.tags")}</span>
                  <span className="tag-edit-group">
                    {(tagsSaved ?? roast.tags ?? []).map((tagValue) => (
                      <span key={tagValue} className="tag-chip">
                        {tagValue}
                        <button
                          type="button"
                          className="tag-chip-remove"
                          onClick={() => handleRemoveTag(tagValue)}
                          aria-label={t("liveRoast.removeTag", { tag: tagValue })}
                        >
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
                        placeholder={t("liveRoast.addTagPlaceholder")}
                        list="existing-tags-live"
                      />
                      <button type="button" onClick={handleAddTag} disabled={!newTagInput.trim()}>
                        {t("liveRoast.add")}
                      </button>
                      <datalist id="existing-tags-live">
                        {allTags
                          .filter((tagObj) => !(tagsSaved ?? roast.tags ?? []).includes(tagObj.tag))
                          .map((tagObj) => (
                            <option key={tagObj.tag} value={tagObj.tag} />
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
                  <span>{t("liveRoast.greenWeight")}</span>
                  <span>
                    <WeightField value={greenWeightValue} onSave={handleSaveGreenWeight} onDelete={handleDeleteGreenWeight} />
                  </span>
                </li>
                {phase === "finished" && (
                  <li>
                    <span>{t("liveRoast.roastedWeight")}</span>
                    <span>
                      <WeightField value={roastedWeightValue} onSave={handleSaveRoastedWeight} onDelete={handleDeleteRoastedWeight} />
                    </span>
                  </li>
                )}
                {phase === "finished" && greenWeightValue && roastedWeightValue != null && (
                  <li>
                    <span>{t("liveRoast.weightLoss")}</span>
                    <span>{(((roastedWeightValue / greenWeightValue) - 1) * 100).toFixed(1)}%</span>
                  </li>
                )}
              </ul>
            </div>
          )}

          {phase === "finished" && roast && (
            <div className="panel">
              <h3>{t("liveRoast.roastStats")}</h3>
              <RoastStatsPanel roastId={roast.id} />
            </div>
          )}

          <div className="live-grid">
            {CONTROLLABLE_MODES.includes(activeMode) && isActive && <AutoControlPanel roastId={roastId} />}
            {activeMode === "alog_playback" && (
              <div className="panel control-panel">
                <h3>{t("liveRoast.playbackSpeedHeading")}</h3>
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
            {activeMode === "ms6514_live" && (
              <div className="panel control-panel">
                <h3>{t("liveRoast.mastechHeading")}</h3>
                <p className="hint">
                  {t("liveRoast.mastechHint", { port: form.ms6514_port || t("liveRoast.theSerialPort") })}
                </p>
              </div>
            )}
            <div className="panel">
              <h3>{t("liveRoast.events")}</h3>
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

            <NotesPanel roastId={roastId} notes={roast?.notes || []} onReplace={(notes) => setRoast((r) => (r ? { ...r, notes } : r))} />

            {/* Full-width (gridColumn: 1/-1) and deliberately last in this
                grid -- placed anywhere earlier, it forces a row break right
                after it, stranding whatever narrow panel came just before
                it (AutoControlPanel) alone in a row with empty grid tracks
                beside it (those tracks stay alive/non-collapsed because
                Events/NotesPanel use them lower down). Last item has
                nothing after it to misalign, so AutoControlPanel/Events/
                Notes can pair up normally above it instead. */}
            {activeMode === "modbus_live" && (
              <p className="hint" style={{ gridColumn: "1 / -1" }}>
                {t("liveRoast.modbusLiveHint", {
                  target:
                    form.modbus_transport === "tcp"
                      ? form.modbus_host || t("liveRoast.theConfiguredHost")
                      : form.modbus_port || t("liveRoast.theSerialPort"),
                })}
              </p>
            )}
            {activeMode === "tc4_live" && (
              <p className="hint" style={{ gridColumn: "1 / -1" }}>
                {t("liveRoast.tc4LiveHint", { port: form.tc4_port || t("liveRoast.theSerialPort") })}
              </p>
            )}
          </div>
        </div>
        {showSplitLayout && (
          <div
            className="breakout-split-divider"
            title={t("liveRoast.resizeDivider")}
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
