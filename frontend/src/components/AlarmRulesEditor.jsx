import { useState } from "react";
import { EVENT_BUTTONS } from "./EventButtonRow.jsx";

// Every markable milestone (CHARGE..COOL_END) -- TURNING_POINT/CUSTOM
// aren't valid triggers (TURNING_POINT is never manually markable; CUSTOM
// isn't a milestone), and neither has a row in EVENT_BUTTONS to begin with.
const TRIGGER_OPTIONS = EVENT_BUTTONS.map((b) => b.type);

function summarizeCommand(rule) {
  const parts = [];
  if (rule.heater_pct != null) parts.push(`Burner→${rule.heater_pct}%`);
  if (rule.fan_pct != null) parts.push(`Air→${rule.fan_pct}%`);
  if (rule.drum_speed_pct != null) parts.push(`Drum→${rule.drum_speed_pct}%`);
  return parts.join(", ") || "(no command set)";
}

// Bound to a saved preset's own config (see LiveRoastView.jsx's
// buildConfigFromForm/handleLoadPreset) -- modbus_live only, since
// ms6514_live has no write capability to bind a command to at all.
export default function AlarmRulesEditor({ rules, onChange }) {
  const [expanded, setExpanded] = useState(false);
  const [draftTrigger, setDraftTrigger] = useState(TRIGGER_OPTIONS[0]);
  const [draftDelay, setDraftDelay] = useState(0);
  const [draftHeater, setDraftHeater] = useState("");
  const [draftFan, setDraftFan] = useState("");
  const [draftDrum, setDraftDrum] = useState("");
  // A rule that touches Burner needs an explicit extra confirm before it
  // can be added -- same caution Testing Mode's nudge feature uses, since
  // a bad Burner setpoint can make the roaster's own bang-bang controller
  // start firing heating elements. Any draft-field edit while this is
  // showing resets it, so a changed value always needs a fresh read of
  // the warning rather than silently reusing an earlier confirm click.
  const [burnerConfirming, setBurnerConfirming] = useState(false);

  function draftSetter(setter) {
    return (value) => {
      setBurnerConfirming(false);
      setter(value);
    };
  }

  function resetDraft() {
    setDraftTrigger(TRIGGER_OPTIONS[0]);
    setDraftDelay(0);
    setDraftHeater("");
    setDraftFan("");
    setDraftDrum("");
    setBurnerConfirming(false);
  }

  function addRule() {
    const touchesBurner = draftHeater !== "";
    if (touchesBurner && !burnerConfirming) {
      setBurnerConfirming(true);
      return;
    }
    const rule = {
      trigger: draftTrigger,
      delay_s: Number(draftDelay) || 0,
      heater_pct: draftHeater === "" ? null : Number(draftHeater),
      fan_pct: draftFan === "" ? null : Number(draftFan),
      drum_speed_pct: draftDrum === "" ? null : Number(draftDrum),
    };
    onChange([...rules, rule]);
    resetDraft();
  }

  function removeRule(index) {
    onChange(rules.filter((_, i) => i !== index));
  }

  const hasAnyCommand = draftHeater !== "" || draftFan !== "" || draftDrum !== "";

  return (
    <div className="alarm-rules-editor">
      <button type="button" className="advanced-toggle" onClick={() => setExpanded((v) => !v)}>
        {expanded ? "▾" : "▸"} Milestone automation (optional)
      </button>
      {expanded && (
        <div className="alarm-rules-fields">
          <p className="hint">
            Bind a Burner/Air/Drum command to fire automatically when a milestone is marked below — immediately,
            or after a delay (Artisan calls this "Alarms"). Saved as part of this configuration, so it travels with
            "Save this configuration as" below.
          </p>

          {rules.length > 0 && (
            <ul className="alarm-rules-list">
              {rules.map((rule, i) => (
                <li key={rule.id || i} className="alarm-rules-row">
                  <span className="alarm-rules-trigger">{rule.trigger.replace("_", " ")}</span>
                  <span className="alarm-rules-detail">
                    {rule.delay_s > 0 ? `+${rule.delay_s}s` : "immediately"} → {summarizeCommand(rule)}
                  </span>
                  <button type="button" className="danger" onClick={() => removeRule(i)}>
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}

          <div className="form-row">
            <label>
              On milestone
              <select value={draftTrigger} onChange={(e) => draftSetter(setDraftTrigger)(e.target.value)}>
                {TRIGGER_OPTIONS.map((t) => (
                  <option key={t} value={t}>
                    {t.replace("_", " ")}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Delay (seconds)
              <input
                type="number"
                min="0"
                step="1"
                value={draftDelay}
                onChange={(e) => draftSetter(setDraftDelay)(e.target.value)}
              />
            </label>
          </div>
          <div className="form-row">
            <label>
              Burner %
              <input
                type="number"
                min="0"
                max="100"
                placeholder="unset"
                value={draftHeater}
                onChange={(e) => draftSetter(setDraftHeater)(e.target.value)}
              />
            </label>
            <label>
              Air %
              <input
                type="number"
                min="0"
                max="100"
                placeholder="unset"
                value={draftFan}
                onChange={(e) => draftSetter(setDraftFan)(e.target.value)}
              />
            </label>
            <label>
              Drum %
              <input
                type="number"
                min="0"
                max="100"
                placeholder="unset"
                value={draftDrum}
                onChange={(e) => draftSetter(setDraftDrum)(e.target.value)}
              />
            </label>
          </div>

          {burnerConfirming ? (
            <>
              <p className="hint">
                This rule sets Burner to {draftHeater}% automatically the instant {draftTrigger.replace("_", " ")}
                {Number(draftDelay) > 0 ? ` (after a ${draftDelay}s delay)` : ""} is marked — no further confirmation
                once saved. Only add this if you're sure.
              </p>
              <div className="event-button-row">
                <button type="button" onClick={addRule}>
                  Confirm: add Burner rule
                </button>
                <button type="button" className="danger" onClick={() => setBurnerConfirming(false)}>
                  Cancel
                </button>
              </div>
            </>
          ) : (
            <button type="button" onClick={addRule} disabled={!hasAnyCommand}>
              Add rule
            </button>
          )}
        </div>
      )}
    </div>
  );
}
