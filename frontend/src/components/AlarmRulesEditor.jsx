import { useState } from "react";
import { EVENT_BUTTONS } from "./EventButtonRow.jsx";

// Every markable milestone (CHARGE..COOL_END) -- TURNING_POINT/CUSTOM
// aren't valid triggers (TURNING_POINT is never manually markable; CUSTOM
// isn't a milestone), and neither has a row in EVENT_BUTTONS to begin with.
const EVENT_TRIGGER_OPTIONS = EVENT_BUTTONS.map((b) => b.type);

const TRIGGER_KINDS = [
  { value: "event", label: "Milestone" },
  { value: "temperature", label: "Temperature" },
  { value: "time", label: "Elapsed time" },
];

function summarizeCommand(rule) {
  const parts = [];
  if (rule.heater_pct != null) parts.push(`Burner→${rule.heater_pct}%`);
  if (rule.fan_pct != null) parts.push(`Air→${rule.fan_pct}%`);
  if (rule.drum_speed_pct != null) parts.push(`Drum→${rule.drum_speed_pct}%`);
  if (rule.message) parts.push(`banner: "${rule.message}"`);
  if (rule.mark_milestone) parts.push(`mark ${rule.mark_milestone.replace("_", " ")}`);
  return parts.join(", ") || "(nothing set)";
}

function summarizeTrigger(rule) {
  if (rule.trigger_kind === "temperature") {
    return `${(rule.channel || "bt").toUpperCase()} ≥ ${rule.threshold_c}°C`;
  }
  if (rule.trigger_kind === "time") {
    const s = Number(rule.at_time_s) || 0;
    return `at ${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  }
  return (rule.event_type || "").replace("_", " ");
}

// Bound to a saved preset's own config (see LiveRoastView.jsx's
// buildConfigFromForm/handleLoadPreset) -- modbus_live only, since
// ms6514_live has no write capability to bind a command to at all.
export default function AlarmRulesEditor({ rules, onChange }) {
  const [expanded, setExpanded] = useState(false);
  const [draftKind, setDraftKind] = useState("event");
  const [draftEventType, setDraftEventType] = useState(EVENT_TRIGGER_OPTIONS[0]);
  const [draftChannel, setDraftChannel] = useState("bt");
  const [draftThreshold, setDraftThreshold] = useState("");
  const [draftAtTime, setDraftAtTime] = useState("");
  const [draftDelay, setDraftDelay] = useState(0);
  const [draftHeater, setDraftHeater] = useState("");
  const [draftFan, setDraftFan] = useState("");
  const [draftDrum, setDraftDrum] = useState("");
  const [draftMessage, setDraftMessage] = useState("");
  const [draftMarkMilestone, setDraftMarkMilestone] = useState("");
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
    setDraftKind("event");
    setDraftEventType(EVENT_TRIGGER_OPTIONS[0]);
    setDraftChannel("bt");
    setDraftThreshold("");
    setDraftAtTime("");
    setDraftDelay(0);
    setDraftHeater("");
    setDraftFan("");
    setDraftDrum("");
    setDraftMessage("");
    setDraftMarkMilestone("");
    setBurnerConfirming(false);
  }

  const hasAnyCommand =
    draftHeater !== "" || draftFan !== "" || draftDrum !== "" || draftMessage.trim() !== "" || draftMarkMilestone !== "";
  const triggerReady =
    draftKind === "event" ? true : draftKind === "temperature" ? draftThreshold !== "" : draftAtTime !== "";

  function addRule() {
    const touchesBurner = draftHeater !== "";
    if (touchesBurner && !burnerConfirming) {
      setBurnerConfirming(true);
      return;
    }
    const rule = {
      trigger_kind: draftKind,
      event_type: draftKind === "event" ? draftEventType : null,
      channel: draftKind === "temperature" ? draftChannel : null,
      threshold_c: draftKind === "temperature" && draftThreshold !== "" ? Number(draftThreshold) : null,
      at_time_s: draftKind === "time" && draftAtTime !== "" ? Number(draftAtTime) : null,
      delay_s: Number(draftDelay) || 0,
      heater_pct: draftHeater === "" ? null : Number(draftHeater),
      fan_pct: draftFan === "" ? null : Number(draftFan),
      drum_speed_pct: draftDrum === "" ? null : Number(draftDrum),
      message: draftMessage.trim() || null,
      mark_milestone: draftMarkMilestone || null,
    };
    onChange([...rules, rule]);
    resetDraft();
  }

  function removeRule(index) {
    onChange(rules.filter((_, i) => i !== index));
  }

  return (
    <div className="alarm-rules-editor">
      <button type="button" className="advanced-toggle" onClick={() => setExpanded((v) => !v)}>
        {expanded ? "▾" : "▸"} Automation rules (optional)
      </button>
      {expanded && (
        <div className="alarm-rules-fields">
          <p className="hint">
            Fire a Burner/Air/Drum command and/or show an in-app banner automatically when a milestone is marked, a
            temperature threshold is crossed, or a set amount of roast time has elapsed — immediately, or after a
            delay. Saved as part of this configuration, so it travels with "Save this configuration as" below.
          </p>

          {rules.length > 0 && (
            <ul className="alarm-rules-list">
              {rules.map((rule, i) => (
                <li key={rule.id || i} className="alarm-rules-row">
                  <span className="alarm-rules-trigger">{summarizeTrigger(rule)}</span>
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
              Trigger type
              <select value={draftKind} onChange={(e) => draftSetter(setDraftKind)(e.target.value)}>
                {TRIGGER_KINDS.map((k) => (
                  <option key={k.value} value={k.value}>
                    {k.label}
                  </option>
                ))}
              </select>
            </label>
            {draftKind === "event" && (
              <label>
                On milestone
                <select value={draftEventType} onChange={(e) => draftSetter(setDraftEventType)(e.target.value)}>
                  {EVENT_TRIGGER_OPTIONS.map((t) => (
                    <option key={t} value={t}>
                      {t.replace("_", " ")}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {draftKind === "temperature" && (
              <>
                <label>
                  Channel
                  <select value={draftChannel} onChange={(e) => draftSetter(setDraftChannel)(e.target.value)}>
                    <option value="bt">BT</option>
                    <option value="et">ET</option>
                  </select>
                </label>
                <label>
                  At or above (°C)
                  <input
                    type="number"
                    placeholder="e.g. 200"
                    value={draftThreshold}
                    onChange={(e) => draftSetter(setDraftThreshold)(e.target.value)}
                  />
                </label>
              </>
            )}
            {draftKind === "time" && (
              <label>
                Elapsed roast time (seconds)
                <input
                  type="number"
                  min="0"
                  placeholder="e.g. 300"
                  value={draftAtTime}
                  onChange={(e) => draftSetter(setDraftAtTime)(e.target.value)}
                />
              </label>
            )}
            <label>
              Extra delay after trigger (seconds)
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
          <div className="form-row">
            <label style={{ flexGrow: 1 }}>
              Banner message (optional)
              <input
                type="text"
                maxLength={200}
                placeholder="e.g. First crack — airflow up"
                value={draftMessage}
                onChange={(e) => draftSetter(setDraftMessage)(e.target.value)}
              />
            </label>
            <label>
              Also mark milestone (optional)
              <select value={draftMarkMilestone} onChange={(e) => draftSetter(setDraftMarkMilestone)(e.target.value)}>
                <option value="">(none)</option>
                {EVENT_TRIGGER_OPTIONS.map((t) => (
                  <option key={t} value={t}>
                    {t.replace("_", " ")}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {draftMarkMilestone && (
            <p className="hint">
              Chains this rule's trigger into auto-marking {draftMarkMilestone.replace("_", " ")} — same as clicking
              that button yourself, so it still has to happen in the correct order relative to whatever's already
              been marked, or it's silently skipped.
            </p>
          )}

          {burnerConfirming ? (
            <>
              <p className="hint">
                This rule sets Burner to {draftHeater}% automatically once {summarizeTrigger({
                  trigger_kind: draftKind,
                  event_type: draftEventType,
                  channel: draftChannel,
                  threshold_c: draftThreshold,
                  at_time_s: draftAtTime,
                })}
                {Number(draftDelay) > 0 ? ` (after a ${draftDelay}s delay)` : ""} — no further confirmation once
                saved. Only add this if you're sure.
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
            <button type="button" onClick={addRule} disabled={!hasAnyCommand || !triggerReady}>
              Add rule
            </button>
          )}
        </div>
      )}
    </div>
  );
}
