import { useState } from "react";
import { useTranslation } from "react-i18next";
import { EVENT_BUTTONS } from "./EventButtonRow.jsx";

// Every markable milestone (CHARGE..COOL_END) -- TURNING_POINT/CUSTOM
// aren't valid triggers (TURNING_POINT is never manually markable; CUSTOM
// isn't a milestone), and neither has a row in EVENT_BUTTONS to begin with.
const EVENT_TRIGGER_OPTIONS = EVENT_BUTTONS.map((b) => b.type);

function summarizeCommand(rule, t) {
  const parts = [];
  if (rule.heater_pct != null) parts.push(t("common.alarmRulesEditor.burnerArrow", { pct: rule.heater_pct }));
  if (rule.fan_pct != null) parts.push(t("common.alarmRulesEditor.airArrow", { pct: rule.fan_pct }));
  if (rule.drum_speed_pct != null) parts.push(t("common.alarmRulesEditor.drumArrow", { pct: rule.drum_speed_pct }));
  if (rule.message) parts.push(t("common.alarmRulesEditor.bannerLabel", { message: rule.message }));
  if (rule.mark_milestone) parts.push(t("common.alarmRulesEditor.markMilestone", { milestone: rule.mark_milestone.replace("_", " ") }));
  return parts.join(", ") || t("common.alarmRulesEditor.nothingSet");
}

function summarizeTrigger(rule, t) {
  if (rule.trigger_kind === "temperature") {
    return `${(rule.channel || "bt").toUpperCase()} ≥ ${rule.threshold_c}°C`;
  }
  if (rule.trigger_kind === "time") {
    const s = Number(rule.at_time_s) || 0;
    return t("common.alarmRulesEditor.atTime", { time: `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` });
  }
  return (rule.event_type || "").replace("_", " ");
}

// Bound to a saved preset's own config (see LiveRoastView.jsx's
// buildConfigFromForm/handleLoadPreset) -- modbus_live only, since
// ms6514_live has no write capability to bind a command to at all.
export default function AlarmRulesEditor({ rules, onChange }) {
  const { t } = useTranslation();
  const TRIGGER_KINDS = [
    { value: "event", label: t("common.alarmRulesEditor.triggerKinds.event") },
    { value: "temperature", label: t("common.alarmRulesEditor.triggerKinds.temperature") },
    { value: "time", label: t("common.alarmRulesEditor.triggerKinds.time") },
  ];
  // null while adding a new rule; the array index of the rule currently
  // loaded into the draft fields below while editing an existing one.
  const [editingIndex, setEditingIndex] = useState(null);
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
    setEditingIndex(null);
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

  function startEdit(index) {
    const rule = rules[index];
    setEditingIndex(index);
    setDraftKind(rule.trigger_kind);
    setDraftEventType(rule.event_type || EVENT_TRIGGER_OPTIONS[0]);
    setDraftChannel(rule.channel || "bt");
    setDraftThreshold(rule.threshold_c ?? "");
    setDraftAtTime(rule.at_time_s ?? "");
    setDraftDelay(rule.delay_s ?? 0);
    setDraftHeater(rule.heater_pct ?? "");
    setDraftFan(rule.fan_pct ?? "");
    setDraftDrum(rule.drum_speed_pct ?? "");
    setDraftMessage(rule.message || "");
    setDraftMarkMilestone(rule.mark_milestone || "");
    setBurnerConfirming(false);
  }

  const hasAnyCommand =
    draftHeater !== "" || draftFan !== "" || draftDrum !== "" || draftMessage.trim() !== "" || draftMarkMilestone !== "";
  const triggerReady =
    draftKind === "event" ? true : draftKind === "temperature" ? draftThreshold !== "" : draftAtTime !== "";

  function saveRule() {
    const touchesBurner = draftHeater !== "";
    if (touchesBurner && !burnerConfirming) {
      setBurnerConfirming(true);
      return;
    }
    // enabled carries over unchanged from the rule being edited (default
    // true for a brand new one) -- editing a rule's trigger/action isn't
    // the same gesture as flipping its own enabled toggle in the list.
    const rule = {
      enabled: editingIndex != null ? rules[editingIndex].enabled : true,
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
    if (editingIndex != null) {
      onChange(rules.map((r, i) => (i === editingIndex ? { ...rules[editingIndex], ...rule } : r)));
    } else {
      onChange([...rules, rule]);
    }
    resetDraft();
  }

  function removeRule(index) {
    if (editingIndex === index) resetDraft();
    onChange(rules.filter((_, i) => i !== index));
  }

  function toggleEnabled(index) {
    onChange(rules.map((r, i) => (i === index ? { ...r, enabled: r.enabled === false } : r)));
  }

  return (
    <div className="alarm-rules-editor">
      <h4>{t("common.alarmRulesEditor.heading")}</h4>
      <div className="alarm-rules-fields">
          <p className="hint">{t("common.alarmRulesEditor.intro")}</p>

          {rules.length > 0 && (
            <ul className="alarm-rules-list">
              {rules.map((rule, i) => {
                const enabled = rule.enabled !== false;
                return (
                  <li key={rule.id || i} className={`alarm-rules-row${enabled ? "" : " alarm-rules-row-disabled"}`}>
                    <label
                      className="checkbox-label alarm-rules-enabled-toggle"
                      title={enabled ? t("common.alarmRulesEditor.disableRule") : t("common.alarmRulesEditor.enableRule")}
                    >
                      <input type="checkbox" checked={enabled} onChange={() => toggleEnabled(i)} />
                    </label>
                    <span className="alarm-rules-trigger">{summarizeTrigger(rule, t)}</span>
                    <span className="alarm-rules-detail">
                      {rule.delay_s > 0 ? t("common.alarmRulesEditor.delayLabel", { seconds: rule.delay_s }) : t("common.alarmRulesEditor.immediately")}{" "}
                      → {summarizeCommand(rule, t)}
                    </span>
                    <button type="button" onClick={() => startEdit(i)} disabled={editingIndex === i}>
                      {editingIndex === i ? t("common.alarmRulesEditor.editing") : t("common.alarmRulesEditor.edit")}
                    </button>
                    <button type="button" className="danger" onClick={() => removeRule(i)}>
                      {t("common.alarmRulesEditor.remove")}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          <div className="form-row">
            <label>
              {t("common.alarmRulesEditor.triggerType")}
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
                {t("common.alarmRulesEditor.onMilestone")}
                <select value={draftEventType} onChange={(e) => draftSetter(setDraftEventType)(e.target.value)}>
                  {EVENT_TRIGGER_OPTIONS.map((eventType) => (
                    <option key={eventType} value={eventType}>
                      {eventType.replace("_", " ")}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {draftKind === "temperature" && (
              <>
                <label>
                  {t("common.alarmRulesEditor.channel")}
                  <select value={draftChannel} onChange={(e) => draftSetter(setDraftChannel)(e.target.value)}>
                    <option value="bt">BT</option>
                    <option value="et">ET</option>
                  </select>
                </label>
                <label>
                  {t("common.alarmRulesEditor.atOrAbove")}
                  <input
                    type="number"
                    placeholder={t("common.alarmRulesEditor.thresholdPlaceholder")}
                    value={draftThreshold}
                    onChange={(e) => draftSetter(setDraftThreshold)(e.target.value)}
                  />
                </label>
              </>
            )}
            {draftKind === "time" && (
              <label>
                {t("common.alarmRulesEditor.elapsedRoastTime")}
                <input
                  type="number"
                  min="0"
                  placeholder={t("common.alarmRulesEditor.elapsedTimePlaceholder")}
                  value={draftAtTime}
                  onChange={(e) => draftSetter(setDraftAtTime)(e.target.value)}
                />
              </label>
            )}
            <label>
              {t("common.alarmRulesEditor.extraDelay")}
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
              {t("common.alarmRulesEditor.burnerPct")}
              <input
                type="number"
                min="0"
                max="100"
                placeholder={t("common.alarmRulesEditor.unsetPlaceholder")}
                value={draftHeater}
                onChange={(e) => draftSetter(setDraftHeater)(e.target.value)}
              />
            </label>
            <label>
              {t("common.alarmRulesEditor.airRpm")}
              <input
                type="number"
                min="0"
                max="100"
                placeholder={t("common.alarmRulesEditor.unsetPlaceholder")}
                value={draftFan}
                onChange={(e) => draftSetter(setDraftFan)(e.target.value)}
              />
            </label>
            <label>
              {t("common.alarmRulesEditor.drumRpm")}
              <input
                type="number"
                min="0"
                max="100"
                placeholder={t("common.alarmRulesEditor.unsetPlaceholder")}
                value={draftDrum}
                onChange={(e) => draftSetter(setDraftDrum)(e.target.value)}
              />
            </label>
          </div>
          <div className="form-row">
            <label style={{ flexGrow: 1 }}>
              {t("common.alarmRulesEditor.bannerMessage")}
              <input
                type="text"
                maxLength={200}
                placeholder={t("common.alarmRulesEditor.bannerPlaceholder")}
                value={draftMessage}
                onChange={(e) => draftSetter(setDraftMessage)(e.target.value)}
              />
            </label>
            <label>
              {t("common.alarmRulesEditor.alsoMarkMilestone")}
              <select value={draftMarkMilestone} onChange={(e) => draftSetter(setDraftMarkMilestone)(e.target.value)}>
                <option value="">{t("common.alarmRulesEditor.none")}</option>
                {EVENT_TRIGGER_OPTIONS.map((eventType) => (
                  <option key={eventType} value={eventType}>
                    {eventType.replace("_", " ")}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {draftMarkMilestone && (
            <p className="hint">
              {t("common.alarmRulesEditor.chainsIntoMarking", { milestone: draftMarkMilestone.replace("_", " ") })}
            </p>
          )}

          {editingIndex != null && (
            <p className="hint">{t("common.alarmRulesEditor.editingRuleAbove", { n: editingIndex + 1 })}</p>
          )}
          {burnerConfirming ? (
            <>
              <p className="hint">
                {t("common.alarmRulesEditor.burnerConfirmIntro", {
                  pct: draftHeater,
                  trigger: summarizeTrigger(
                    {
                      trigger_kind: draftKind,
                      event_type: draftEventType,
                      channel: draftChannel,
                      threshold_c: draftThreshold,
                      at_time_s: draftAtTime,
                    },
                    t
                  ),
                })}
                {Number(draftDelay) > 0 ? t("common.alarmRulesEditor.burnerConfirmDelay", { seconds: draftDelay }) : ""}
                {t("common.alarmRulesEditor.burnerConfirmTail", {
                  action: editingIndex != null ? t("common.alarmRulesEditor.save") : t("common.alarmRulesEditor.add"),
                })}
              </p>
              <div className="event-button-row">
                <button type="button" onClick={saveRule}>
                  {editingIndex != null
                    ? t("common.alarmRulesEditor.confirmSaveBurnerRule")
                    : t("common.alarmRulesEditor.confirmAddBurnerRule")}
                </button>
                <button type="button" className="danger" onClick={() => setBurnerConfirming(false)}>
                  {t("common.alarmRulesEditor.cancel")}
                </button>
              </div>
            </>
          ) : (
            <div className="event-button-row">
              <button type="button" onClick={saveRule} disabled={!hasAnyCommand || !triggerReady}>
                {editingIndex != null ? t("common.alarmRulesEditor.saveChanges") : t("common.alarmRulesEditor.addRule")}
              </button>
              {editingIndex != null && (
                <button type="button" className="danger" onClick={resetDraft}>
                  {t("common.alarmRulesEditor.cancel")}
                </button>
              )}
            </div>
          )}
      </div>
    </div>
  );
}
