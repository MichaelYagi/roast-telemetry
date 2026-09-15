import { useEffect, useRef, useState } from "react";
import { normalizeLayout, verticalControlItem } from "../verticalControl.js";

// Replaces the old horizontal Controls panel entirely -- no fallback
// below the chart any more (see the design discussion this came out of:
// Drum/Air are always shown here specifically so there's never a roast
// with zero way to touch a control, even before Settings has been
// visited). Sits beside the chart (LiveRoastView.jsx's .scope-body, before
// .scope-chart), one vertical slider per configured channel, arranged into
// lanes per the settings' ordered-groups layout -- a lane with more than
// one channel splits its height between them, stacked top to bottom,
// rather than each getting its own lane.
const LOCAL_ECHO_GUARD_MS = 1500;
const STEP = 1;

export default function VerticalControlPanel({ disabled, onSend, initial, layout, arrows, svRangeC }) {
  const groups = normalizeLayout(layout, { svAvailable: svRangeC != null });

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

  const CHANNELS = {
    drum_speed_pct: { value: drum, min: 0, max: 100, onChange: handleDrumChange },
    fan_pct: { value: fan, min: 0, max: 100, onChange: handleFanChange },
    heater_pct: { value: heater, min: 0, max: 100, onChange: handleBurnerPctChange },
    burner_sv_c: svRangeC ? { value: sv, min: Math.min(...svRangeC), max: Math.max(...svRangeC), onChange: handleBurnerSvChange } : null,
  };

  function slider(key) {
    const item = verticalControlItem(key);
    const ch = CHANNELS[key];
    if (!item || !ch) return null;
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
        <div className="vertical-slider-value">{unknown ? "—" : `${Math.round(displayValue)}${item.unit}`}</div>
        {showArrows && (
          <button type="button" className="vertical-slider-arrow" disabled={disabled || unknown} onClick={() => nudge(1)} title={`+${step}${item.unit}`}>
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
          <button type="button" className="vertical-slider-arrow" disabled={disabled || unknown} onClick={() => nudge(-1)} title={`-${step}${item.unit}`}>
            ▼
          </button>
        )}
        <div className="vertical-slider-label">{item.label}</div>
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
      {disabled && <p className="hint vertical-control-hint">Controls are inactive: roast not currently active.</p>}
    </div>
  );
}
