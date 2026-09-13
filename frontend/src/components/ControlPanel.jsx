import { useEffect, useRef, useState } from "react";

// How long to ignore an `initial` update right after the user's own local
// slider change, for a given control. A live device read lags at least one
// tick behind a write (see modbus_bridge/engine.py's tick() reading BT/ET/
// Burner-SV/etc back), so without this, an in-flight stale reading from
// *before* the user's drag can land moments after it and yank the slider
// back to the old value mid-drag before the fresh reading catches up.
const LOCAL_ECHO_GUARD_MS = 1500;

export default function ControlPanel({ initial, disabled, onSend }) {
  const [heater, setHeater] = useState(initial?.heater_pct ?? 70);
  const [fan, setFan] = useState(initial?.fan_pct ?? 20);
  const [drum, setDrum] = useState(initial?.drum_speed_pct ?? 50);
  const lastLocalChangeAt = useRef({ heater_pct: 0, fan_pct: 0, drum_speed_pct: 0 });

  // useState above only seeds from `initial` on first mount -- for
  // modbus_live, `initial` starts out as the (possibly stale) form default
  // because the very first render happens before any real device reading
  // has arrived, then updates once `latest.heater_pct`/etc. do (see
  // LiveRoastView.jsx, which prefers those over the form value). Without
  // these effects the sliders would freeze on that first-render default
  // forever instead of ever catching up to what the roaster's actually
  // doing -- exactly the case this prop's real reads are supposed to fix.
  useEffect(() => {
    if (initial?.heater_pct == null) return;
    if (Date.now() - lastLocalChangeAt.current.heater_pct < LOCAL_ECHO_GUARD_MS) return;
    setHeater(initial.heater_pct);
  }, [initial?.heater_pct]);
  useEffect(() => {
    if (initial?.fan_pct == null) return;
    if (Date.now() - lastLocalChangeAt.current.fan_pct < LOCAL_ECHO_GUARD_MS) return;
    setFan(initial.fan_pct);
  }, [initial?.fan_pct]);
  useEffect(() => {
    if (initial?.drum_speed_pct == null) return;
    if (Date.now() - lastLocalChangeAt.current.drum_speed_pct < LOCAL_ECHO_GUARD_MS) return;
    setDrum(initial.drum_speed_pct);
  }, [initial?.drum_speed_pct]);

  const slider = (label, value, setValue, key) => (
    <label className="control-slider">
      <span>
        {label}: <strong>{value}%</strong>
      </span>
      <input
        type="range"
        min="0"
        max="100"
        value={value}
        disabled={disabled}
        onChange={(e) => {
          const v = Number(e.target.value);
          lastLocalChangeAt.current[key] = Date.now();
          setValue(v);
          onSend({ [key]: v });
        }}
      />
    </label>
  );

  return (
    <div className="panel control-panel">
      <h3>Controls</h3>
      {slider("Heater", heater, setHeater, "heater_pct")}
      {slider("Fan", fan, setFan, "fan_pct")}
      {slider("Drum Speed", drum, setDrum, "drum_speed_pct")}
      {disabled && <p className="hint">Controls are inactive: roast not currently active.</p>}
    </div>
  );
}
