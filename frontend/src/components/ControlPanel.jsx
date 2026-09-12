import { useState } from "react";

export default function ControlPanel({ initial, disabled, onSend }) {
  const [heater, setHeater] = useState(initial?.heater_pct ?? 70);
  const [fan, setFan] = useState(initial?.fan_pct ?? 20);
  const [drum, setDrum] = useState(initial?.drum_speed_pct ?? 50);

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
