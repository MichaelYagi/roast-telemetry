import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { TERM_TOOLTIPS } from "../termTooltips.js";
import { normalizeLayout, verticalControlItem, CONTROL_KEY_TO_BREAKOUT_KEY } from "../verticalControl.js";
import { celsiusToUnit, unitToCelsius, unitSuffix } from "../tempUnits.js";

// Replaces the old horizontal Controls panel entirely -- no fallback
// below the chart any more (see the design discussion this came out of:
// Drum/Fan are always shown here specifically so there's never a roast
// with zero way to touch a control, even before Settings has been
// visited). Sits beside the chart (LiveRoastView.jsx's .scope-body, before
// .scope-chart), one vertical slider per configured channel, arranged into
// lanes per the settings' ordered-groups layout -- a lane with more than
// one channel splits its height between them, stacked top to bottom,
// rather than each getting its own lane. That stacking is desktop-only
// (see MOBILE_BREAKPOINT_PX below) -- confirmed live on a real phone
// that splitting a lane's already-limited height between 2 stacked
// channels left both cramped and, with certain layouts, one member's
// own value/label rendering invisibly thin. Below the breakpoint every
// enabled channel gets its own full-height lane instead, side by side,
// scrolling horizontally if there isn't room for all of them --
// simpler and legible over matching the desktop pairing exactly. See
// VerticalControlSettingsEditor.jsx's own note about this.
const LOCAL_ECHO_GUARD_MS = 1500;
// A native range input's onChange (mapped to the DOM "input" event) fires
// on every pixel of a drag -- without this, a single drag gesture fired a
// full onSend/network command per tick (dozens of POSTs/second), flooding
// the backend/hardware and making real hardware's own feedback lag behind
// wherever you'd since dragged to. Throttled to at most one send per this
// many ms while a pointer is actively down on the slider; a plain click or
// arrow-button nudge (dragging never true) still sends immediately, same
// as always. See beginDrag/sendThrottled/endDrag below.
const DRAG_SEND_THROTTLE_MS = 150;
const STEP = 1;
const MOBILE_BREAKPOINT_PX = 700; // matches styles.css's own @media (max-width: 700px) for this panel

function useIsMobileViewport() {
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.innerWidth <= MOBILE_BREAKPOINT_PX
  );
  useEffect(() => {
    function onResize() {
      setIsMobile(window.innerWidth <= MOBILE_BREAKPOINT_PX);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return isMobile;
}

export default function VerticalControlPanel({ disabled, onSend, initial, layout, arrows, svRangeC, tempUnit = "c", colorOverrides }) {
  const { t } = useTranslation();
  const isMobile = useIsMobileViewport();
  const normalizedGroups = normalizeLayout(layout, { svAvailable: svRangeC != null });
  // Flatten every group down to single-channel lanes on mobile -- see
  // the module comment above for why. Order is preserved (just the
  // pairing dropped), so this still respects the Settings ordering.
  const groups = isMobile ? normalizedGroups.flat().map((key) => [key]) : normalizedGroups;

  // Two independent numeric-state pairs: Drum/Fan are simple, one write
  // path each, same as the old ControlPanel. Burner is a pair (heater_pct
  // %, burner_sv_c °C) that mirror each other -- see handleBurnerPctChange/
  // handleBurnerSvChange below for the optimistic local conversion between
  // them ("one moves the other", per the design discussion -- both write
  // the exact same underlying setpoint, just in different units).
  const [drum, setDrum] = useState(initial?.drum_speed_pct ?? null);
  const [fan, setFan] = useState(initial?.fan_pct ?? null);
  const [heater, setHeater] = useState(initial?.heater_pct ?? null);
  const [sv, setSv] = useState(initial?.burner_sv_c ?? null);
  // Drum/Fan only -- a real on/off gesture separate from the slider's own
  // position (see toggleDrumOn/toggleAirOn below), matching a real VFD
  // drive's own run/stop control instead of overloading "value is 0" as
  // the only way to stop it. Burner/SV have no equivalent -- they're a
  // continuous setpoint, not a drive that's literally on or off.
  const [drumOn, setDrumOn] = useState(true);
  const [airOn, setAirOn] = useState(true);
  const lastLocalChangeAt = useRef({ drum_speed_pct: 0, fan_pct: 0, heater_pct: 0, burner_sv_c: 0 });
  // One throttle/drag-tracking slot per channel -- see DRAG_SEND_THROTTLE_MS
  // above and sendThrottled/beginDrag/endDrag below.
  const dragState = useRef({
    drum_speed_pct: { dragging: false, lastSentAt: 0, timer: null, pendingValue: null },
    fan_pct: { dragging: false, lastSentAt: 0, timer: null, pendingValue: null },
    heater_pct: { dragging: false, lastSentAt: 0, timer: null, pendingValue: null },
    burner_sv_c: { dragging: false, lastSentAt: 0, timer: null, pendingValue: null },
  });

  // Sends immediately outside of a drag (plain click, arrow nudge, toggle --
  // all exactly as before). While dragging, sends at most once every
  // DRAG_SEND_THROTTLE_MS, always with the latest value by the time a
  // pending send actually fires -- a real throttle (periodic sends during
  // continuous motion), not a debounce that would otherwise go silent
  // until the gesture pauses.
  function sendThrottled(key, value) {
    const state = dragState.current[key];
    state.pendingValue = value;
    if (!state.dragging) {
      if (state.timer) {
        clearTimeout(state.timer);
        state.timer = null;
      }
      onSend({ [key]: value });
      state.lastSentAt = Date.now();
      return;
    }
    if (state.timer) return; // a trailing send is already scheduled -- it'll pick up pendingValue
    const elapsed = Date.now() - state.lastSentAt;
    if (elapsed >= DRAG_SEND_THROTTLE_MS) {
      onSend({ [key]: value });
      state.lastSentAt = Date.now();
      return;
    }
    state.timer = setTimeout(() => {
      state.timer = null;
      onSend({ [key]: state.pendingValue });
      state.lastSentAt = Date.now();
    }, DRAG_SEND_THROTTLE_MS - elapsed);
  }

  function beginDrag(key) {
    dragState.current[key].dragging = true;
  }

  // Unconditionally flushes the real final value the instant the pointer
  // lifts, bypassing the throttle window -- without this, releasing right
  // after a throttled send could leave hardware briefly sitting on a
  // slightly-stale mid-drag value instead of exactly where you let go.
  function endDrag(key, value) {
    const state = dragState.current[key];
    state.dragging = false;
    if (state.timer) {
      clearTimeout(state.timer);
      state.timer = null;
    }
    if (value == null) return; // input was disabled/unknown -- nothing real was dragged
    // Toggled off (Drum/Fan only) -- same reasoning as handleDrumChange/
    // handleFanChange's own gate: the value is staged in local state for
    // when it's turned back on, but the drive itself stays stopped, so
    // this release doesn't get to bypass that via the throttle's own
    // unconditional flush.
    if ((key === "drum_speed_pct" && !drumOn) || (key === "fan_pct" && !airOn)) return;
    onSend({ [key]: value });
    state.lastSentAt = Date.now();
  }

  // The raw/canonical value sendThrottled already uses for this key --
  // burner_sv_c's own slider displays/drags in celsiusToUnit(sv, tempUnit)
  // (see svRangeDisplay below), but what's actually sent (and what `sv`
  // itself holds) is always Celsius, so endDrag's pointerup flush has to
  // read this, never the display-unit value the input element shows.
  function canonicalValueFor(key) {
    switch (key) {
      case "drum_speed_pct": return drum;
      case "fan_pct": return fan;
      case "heater_pct": return heater;
      case "burner_sv_c": return sv;
      default: return null;
    }
  }

  // Same reasoning as ControlPanel.jsx's own version of this effect pair:
  // a live device read lags at least one tick behind a write, so without
  // the guard window, a stale reading from before the user's own drag can
  // land moments after it and yank the slider back mid-drag. Also skipped
  // entirely while toggled off (!drumOn/!airOn) -- the whole point of the
  // toggle is that the displayed value stays frozen at whatever it was
  // right up until toggled back on, not just for the short echo-guard
  // window, even once the device's own feedback genuinely settles at 0.
  useEffect(() => {
    if (initial?.drum_speed_pct == null || !drumOn) return;
    if (dragState.current.drum_speed_pct.dragging) return; // never mid-gesture, guard window or not
    if (Date.now() - lastLocalChangeAt.current.drum_speed_pct < LOCAL_ECHO_GUARD_MS) return;
    setDrum(initial.drum_speed_pct);
  }, [initial?.drum_speed_pct, drumOn]);
  useEffect(() => {
    if (initial?.fan_pct == null || !airOn) return;
    if (dragState.current.fan_pct.dragging) return;
    if (Date.now() - lastLocalChangeAt.current.fan_pct < LOCAL_ECHO_GUARD_MS) return;
    setFan(initial.fan_pct);
  }, [initial?.fan_pct, airOn]);
  useEffect(() => {
    if (initial?.heater_pct == null) return;
    if (dragState.current.heater_pct.dragging) return;
    if (Date.now() - lastLocalChangeAt.current.heater_pct < LOCAL_ECHO_GUARD_MS) return;
    setHeater(initial.heater_pct);
  }, [initial?.heater_pct]);
  useEffect(() => {
    if (initial?.burner_sv_c == null) return;
    if (dragState.current.burner_sv_c.dragging) return;
    if (Date.now() - lastLocalChangeAt.current.burner_sv_c < LOCAL_ECHO_GUARD_MS) return;
    setSv(initial.burner_sv_c);
  }, [initial?.burner_sv_c]);

  function pctToSvC(pct) {
    if (!svRangeC) return null;
    const [lo, hi] = svRangeC;
    return lo + (pct / 100) * (hi - lo);
  }
  function svCToPct(svC) {
    if (!svRangeC) return null;
    const [lo, hi] = svRangeC;
    if (hi === lo) return null;
    return Math.max(0, Math.min(100, ((svC - lo) / (hi - lo)) * 100));
  }

  // While toggled off, dragging/nudging still moves the displayed value
  // (so you can stage where it'll resume) but deliberately skips
  // sendThrottled -- the drive is stopped, and sending a new nonzero
  // speed while "off" would re-energize it out from under the operator.
  // toggleDrumOn/toggleAirOn below already resend whatever `drum`/`fan`
  // holds the moment you turn it back on, so staging it here is the
  // entire mechanism -- nothing else has to remember it.
  function handleDrumChange(v) {
    lastLocalChangeAt.current.drum_speed_pct = Date.now();
    setDrum(v);
    if (drumOn) sendThrottled("drum_speed_pct", v);
  }
  function handleFanChange(v) {
    lastLocalChangeAt.current.fan_pct = Date.now();
    setFan(v);
    if (airOn) sendThrottled("fan_pct", v);
  }

  // Toggling off sends 0 (the backend already treats that as Stop
  // without zeroing the stored frequency register -- see
  // ModbusEngine._write_vfd_drive) but deliberately does NOT call
  // setDrum/setFan, so the slider keeps showing its last real value
  // instead of jumping to 0 -- matches a real VFD drive's own run/stop
  // button, which doesn't forget its speed setpoint either. Toggling
  // back on resends that same last value to actually resume at it.
  function toggleDrumOn() {
    const next = !drumOn;
    setDrumOn(next);
    lastLocalChangeAt.current.drum_speed_pct = Date.now();
    sendThrottled("drum_speed_pct", next ? (drum ?? 0) : 0);
  }
  function toggleAirOn() {
    const next = !airOn;
    setAirOn(next);
    lastLocalChangeAt.current.fan_pct = Date.now();
    sendThrottled("fan_pct", next ? (fan ?? 0) : 0);
  }
  // Both of these stamp/preview the *other* unit too -- "one moves the
  // other", both writing the same underlying PID setpoint (see
  // ModbusEngine.apply_command's _write_sv_raw). Instant local preview via
  // svRangeC (client-side optimistic conversion, no round trip) rather than
  // waiting for the next telemetry sample to see the other slider move.
  function handleBurnerPctChange(v) {
    const now = Date.now();
    lastLocalChangeAt.current.heater_pct = now;
    setHeater(v);
    const svPreview = pctToSvC(v);
    if (svPreview != null) {
      lastLocalChangeAt.current.burner_sv_c = now;
      setSv(svPreview);
    }
    sendThrottled("heater_pct", v);
  }
  function handleBurnerSvChange(v) {
    const now = Date.now();
    lastLocalChangeAt.current.burner_sv_c = now;
    setSv(v);
    const pctPreview = svCToPct(v);
    if (pctPreview != null) {
      lastLocalChangeAt.current.heater_pct = now;
      setHeater(pctPreview);
    }
    sendThrottled("burner_sv_c", v);
  }

  // svRangeC/sv are always Celsius underneath (same setpoint ModbusEngine
  // writes) -- only this slider's display/drag values convert to the
  // selected tempUnit, same as every live reading elsewhere in the app.
  const svRangeDisplay = svRangeC ? svRangeC.map((c) => celsiusToUnit(c, tempUnit)) : null;
  const CHANNELS = {
    drum_speed_pct: { value: drum, min: 0, max: 100, onChange: handleDrumChange },
    fan_pct: { value: fan, min: 0, max: 100, onChange: handleFanChange },
    heater_pct: { value: heater, min: 0, max: 100, onChange: handleBurnerPctChange },
    burner_sv_c: svRangeDisplay
      ? {
          value: sv != null ? celsiusToUnit(sv, tempUnit) : null,
          min: Math.min(...svRangeDisplay),
          max: Math.max(...svRangeDisplay),
          onChange: (displayV) => handleBurnerSvChange(unitToCelsius(displayV, tempUnit)),
        }
      : null,
  };

  // Drum/Fan only -- [isOn, toggle]. Burner/SV aren't in here at all,
  // since they have no on/off concept; `TOGGLES[key]` being undefined is
  // exactly how slider() below tells "plain text label" from "button."
  const TOGGLES = {
    drum_speed_pct: [drumOn, toggleDrumOn],
    fan_pct: [airOn, toggleAirOn],
  };

  function slider(key) {
    const item = verticalControlItem(key);
    const ch = CHANNELS[key];
    if (!item || !ch) return null;
    const color = colorOverrides?.[CONTROL_KEY_TO_BREAKOUT_KEY[key]] || item.color;
    const unit = key === "burner_sv_c" ? unitSuffix(tempUnit) : item.unit;
    const unknown = ch.value == null;
    const displayValue = unknown ? ch.min : ch.value;
    const showArrows = Boolean(arrows?.[key]);
    // arrows[key] carries the +/- buttons' own step size (see
    // VerticalControlSettingsEditor.jsx) -- deliberately NOT the same
    // value as the range input's own `step` below. Dragging (or
    // arrow-key nudging while the slider has focus, the native range
    // input behavior) always moves by 1 regardless of what the +/-
    // buttons are configured to skip by -- only a dedicated ▲/▼ click
    // uses the configured amount.
    const arrowStep = arrows?.[key] || STEP;
    const toggle = TOGGLES[key];
    const isOn = toggle ? toggle[0] : true;
    // Toggled off (!isOn) deliberately isn't in here -- that's a grey-out,
    // not a lock (see vertical-slider-col-off in styles.css, driven by
    // isOn alone). disabled (no active roast) and unknown (no reading
    // yet) are the only two reasons there's really nothing to adjust.
    const isDisabled = disabled || unknown;

    function nudge(dir) {
      const base = unknown ? ch.min : ch.value;
      const next = Math.max(ch.min, Math.min(ch.max, base + dir * arrowStep));
      ch.onChange(next);
    }

    return (
      <div className={`vertical-slider-col${isOn ? "" : " vertical-slider-col-off"}`} key={key} style={{ "--item-color": color }}>
        <div className="vertical-slider-value">{unknown ? "—" : `${Math.round(displayValue)}${unit}`}</div>
        {showArrows && (
          <button type="button" className="vertical-slider-arrow" disabled={isDisabled} onClick={() => nudge(1)} title={`+${arrowStep}${unit}`}>
            ▲
          </button>
        )}
        <input
          type="range"
          className="vertical-slider-track"
          min={ch.min}
          max={ch.max}
          step={STEP}
          value={displayValue}
          disabled={isDisabled}
          title={
            unknown
              ? t("common.verticalControlPanel.waitingForReading")
              : !isOn
                ? t("common.verticalControlPanel.channelOff", { label: item.label })
                : undefined
          }
          onChange={(e) => ch.onChange(Number(e.target.value))}
          // Marks the whole gesture as a drag (throttles sendThrottled,
          // blocks the readback-sync effects above) from the first touch
          // to the last -- onBlur is a safety net for a drag that ends
          // without a clean pointerup (e.g. losing focus mid-gesture),
          // so "dragging" can never get stuck true.
          onPointerDown={() => beginDrag(key)}
          onPointerUp={() => endDrag(key, canonicalValueFor(key))}
          onPointerCancel={() => endDrag(key, canonicalValueFor(key))}
          onBlur={() => dragState.current[key].dragging && endDrag(key, canonicalValueFor(key))}
        />
        {showArrows && (
          <button type="button" className="vertical-slider-arrow" disabled={isDisabled} onClick={() => nudge(-1)} title={`-${arrowStep}${unit}`}>
            ▼
          </button>
        )}
        {toggle ? (
          <button
            type="button"
            className="vertical-slider-toggle"
            disabled={disabled}
            onClick={toggle[1]}
            title={t(
              isOn ? "common.verticalControlPanel.clickToTurnOff" : "common.verticalControlPanel.clickToTurnOn",
              { tooltip: TERM_TOOLTIPS[item.label] || item.label }
            )}
          >
            {item.label}
          </button>
        ) : (
          <div className="vertical-slider-label" title={TERM_TOOLTIPS[item.label]}>{item.label}</div>
        )}
      </div>
    );
  }

  return (
    <div className="panel vertical-control-panel">
      <div className="vertical-control-stack">
        {groups.map((group, i) => (
          <div className="vertical-control-group" key={group.join("+") || i}>
            {group.map((key) => slider(key))}
          </div>
        ))}
      </div>
    </div>
  );
}
