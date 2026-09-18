import { useEffect, useRef, useState } from "react";
import { TERM_TOOLTIPS } from "../termTooltips.js";
import { normalizeLayout, verticalControlItem } from "../verticalControl.js";
import { celsiusToUnit, unitToCelsius, unitSuffix } from "../tempUnits.js";

// Replaces the old horizontal Controls panel entirely -- no fallback
// below the chart any more (see the design discussion this came out of:
// Drum/Air are always shown here specifically so there's never a roast
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

export default function VerticalControlPanel({ disabled, onSend, initial, layout, arrows, svRangeC, tempUnit = "c" }) {
  const isMobile = useIsMobileViewport();
  const normalizedGroups = normalizeLayout(layout, { svAvailable: svRangeC != null });
  // Flatten every group down to single-channel lanes on mobile -- see
  // the module comment above for why. Order is preserved (just the
  // pairing dropped), so this still respects the Settings ordering.
  const groups = isMobile ? normalizedGroups.flat().map((key) => [key]) : normalizedGroups;

  // Two independent numeric-state pairs: Drum/Air are simple, one write
  // path each, same as the old ControlPanel. Burner is a pair (heater_pct
  // %, burner_sv_c °C) that mirror each other -- see handleBurnerPctChange/
  // handleBurnerSvChange below for the optimistic local conversion between
  // them ("one moves the other", per the design discussion -- both write
  // the exact same underlying setpoint, just in different units).
  const [drum, setDrum] = useState(initial?.drum_speed_pct ?? null);
  const [fan, setFan] = useState(initial?.fan_pct ?? null);
  const [heater, setHeater] = useState(initial?.heater_pct ?? null);
  const [sv, setSv] = useState(initial?.burner_sv_c ?? null);
  const lastLocalChangeAt = useRef({ drum_speed_pct: 0, fan_pct: 0, heater_pct: 0, burner_sv_c: 0 });

  // Same reasoning as ControlPanel.jsx's own version of this effect pair:
  // a live device read lags at least one tick behind a write, so without
  // the guard window, a stale reading from before the user's own drag can
  // land moments after it and yank the slider back mid-drag.
  useEffect(() => {
    if (initial?.drum_speed_pct == null) return;
    if (Date.now() - lastLocalChangeAt.current.drum_speed_pct < LOCAL_ECHO_GUARD_MS) return;
    setDrum(initial.drum_speed_pct);
  }, [initial?.drum_speed_pct]);
  useEffect(() => {
    if (initial?.fan_pct == null) return;
    if (Date.now() - lastLocalChangeAt.current.fan_pct < LOCAL_ECHO_GUARD_MS) return;
    setFan(initial.fan_pct);
  }, [initial?.fan_pct]);
  useEffect(() => {
    if (initial?.heater_pct == null) return;
    if (Date.now() - lastLocalChangeAt.current.heater_pct < LOCAL_ECHO_GUARD_MS) return;
    setHeater(initial.heater_pct);
  }, [initial?.heater_pct]);
  useEffect(() => {
    if (initial?.burner_sv_c == null) return;
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

  function handleDrumChange(v) {
    lastLocalChangeAt.current.drum_speed_pct = Date.now();
    setDrum(v);
    onSend({ drum_speed_pct: v });
  }
  function handleFanChange(v) {
    lastLocalChangeAt.current.fan_pct = Date.now();
    setFan(v);
    onSend({ fan_pct: v });
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
    onSend({ heater_pct: v });
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
    onSend({ burner_sv_c: v });
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

  function slider(key) {
    const item = verticalControlItem(key);
    const ch = CHANNELS[key];
    if (!item || !ch) return null;
    const unit = key === "burner_sv_c" ? unitSuffix(tempUnit) : item.unit;
    const unknown = ch.value == null;
    const displayValue = unknown ? ch.min : ch.value;
    const showArrows = Boolean(arrows?.[key]);
    const step = STEP;

    function nudge(dir) {
      const base = unknown ? ch.min : ch.value;
      const next = Math.max(ch.min, Math.min(ch.max, base + dir * step));
      ch.onChange(next);
    }

    return (
      <div className="vertical-slider-col" key={key} style={{ "--item-color": item.color }}>
        <div className="vertical-slider-value">{unknown ? "—" : `${Math.round(displayValue)}${unit}`}</div>
        {showArrows && (
          <button type="button" className="vertical-slider-arrow" disabled={disabled || unknown} onClick={() => nudge(1)} title={`+${step}${unit}`}>
            ▲
          </button>
        )}
        <input
          type="range"
          className="vertical-slider-track"
          min={ch.min}
          max={ch.max}
          step={step}
          value={displayValue}
          disabled={disabled || unknown}
          title={unknown ? "Waiting for a real reading from the device" : undefined}
          onChange={(e) => ch.onChange(Number(e.target.value))}
        />
        {showArrows && (
          <button type="button" className="vertical-slider-arrow" disabled={disabled || unknown} onClick={() => nudge(-1)} title={`-${step}${unit}`}>
            ▼
          </button>
        )}
        <div className="vertical-slider-label" title={TERM_TOOLTIPS[item.label]}>{item.label}</div>
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
