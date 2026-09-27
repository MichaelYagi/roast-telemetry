import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client.js";
import { useConfirm } from "./DialogProvider.jsx";

const TEMP_ROLES = ["bt", "et", "dt", "extra"];
const CONTROL_SLOTS = [
  { value: "heater_pct", label: "Burner" },
  { value: "fan_pct", label: "Fan" },
  { value: "drum_speed_pct", label: "Drum" },
];

function summarizeTempChannel(ch, t) {
  const name = ch.role === "extra" ? ch.label || t("common.deviceProfileEditor.extra") : ch.role.toUpperCase();
  return t("common.deviceProfileEditor.tempSummary", { name, slave: ch.slave_id, register: ch.register_address, divisor: ch.divisor });
}

function summarizeControlChannel(ch, t) {
  const slot = CONTROL_SLOTS.find((s) => s.value === ch.maps_to)?.label || ch.maps_to;
  if (ch.kind === "sv_temperature") {
    return t("common.deviceProfileEditor.svSummary", {
      slot, slave: ch.slave_id, register: ch.register_address, lo: ch.sv_range_c?.[0], hi: ch.sv_range_c?.[1],
    });
  }
  if (ch.kind === "vfd_drive") {
    return t("common.deviceProfileEditor.vfdSummary", {
      slot, slave: ch.slave_id, ctrl: ch.control_register, freq: ch.frequency_register, lo: ch.value_range?.[0], hi: ch.value_range?.[1],
    });
  }
  return t("common.deviceProfileEditor.directSummary", {
    slot, slave: ch.slave_id, write: ch.write_register, lo: ch.value_range?.[0], hi: ch.value_range?.[1],
  });
}

// Builds and saves a new, custom DeviceProfile -- a named Modbus register
// map for a roaster brand/model this app doesn't ship a built-in profile
// for. Once saved it shows up in LiveRoastView's "Device profile"
// dropdown alongside the built-in ones. Not itself the place a roast
// picks a profile from -- that's the dropdown; this only creates/deletes
// the custom ones (built-in profiles aren't editable here or anywhere,
// see api/device_profiles.py).
export default function DeviceProfileEditor({ onChange }) {
  const { t } = useTranslation();
  const CONTROL_KINDS = [
    { value: "sv_temperature", label: t("common.deviceProfileEditor.controlKinds.svTemperature") },
    { value: "vfd_drive", label: t("common.deviceProfileEditor.controlKinds.vfdDrive") },
    { value: "direct_register", label: t("common.deviceProfileEditor.controlKinds.directRegister") },
  ];
  const confirm = useConfirm();
  const [profiles, setProfiles] = useState([]);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  const [name, setName] = useState("");
  const [baudrate, setBaudrate] = useState(19200);
  const [bytesize, setBytesize] = useState(8);
  const [parity, setParity] = useState("N");
  const [stopbits, setStopbits] = useState(2);
  const [tempChannels, setTempChannels] = useState([]);
  const [controlChannels, setControlChannels] = useState([]);

  const [draftRole, setDraftRole] = useState("bt");
  const [draftLabel, setDraftLabel] = useState("");
  const [draftSlaveId, setDraftSlaveId] = useState("");
  const [draftRegister, setDraftRegister] = useState("");
  const [draftDivisor, setDraftDivisor] = useState(10.0);

  const [draftMapsTo, setDraftMapsTo] = useState("heater_pct");
  const [draftKind, setDraftKind] = useState("sv_temperature");
  const [draftCtrlSlaveId, setDraftCtrlSlaveId] = useState("");
  const [draftCtrlRegister, setDraftCtrlRegister] = useState("");
  const [draftControlRegister, setDraftControlRegister] = useState("");
  const [draftFrequencyRegister, setDraftFrequencyRegister] = useState("");
  const [draftWriteRegister, setDraftWriteRegister] = useState("");
  const [draftFeedbackRegister, setDraftFeedbackRegister] = useState("");
  const [draftRangeLo, setDraftRangeLo] = useState(0);
  const [draftRangeHi, setDraftRangeHi] = useState(100);

  function loadProfiles() {
    api
      .listDeviceProfiles()
      .then(setProfiles)
      .catch((err) => setError(err.message));
  }

  useEffect(loadProfiles, []);

  function resetDraftProfile() {
    setName("");
    setBaudrate(19200);
    setBytesize(8);
    setParity("N");
    setStopbits(2);
    setTempChannels([]);
    setControlChannels([]);
  }

  const tempRoleTaken = (role) => role !== "extra" && tempChannels.some((c) => c.role === role);

  function addTempChannel() {
    if (draftSlaveId === "" || draftRegister === "") return;
    if (draftRole === "extra" && !draftLabel.trim()) return;
    setTempChannels((prev) => [
      ...prev,
      {
        role: draftRole,
        label: draftRole === "extra" ? draftLabel.trim() : null,
        slave_id: Number(draftSlaveId),
        register_address: Number(draftRegister),
        divisor: Number(draftDivisor) || 1.0,
      },
    ]);
    setDraftLabel("");
    setDraftSlaveId("");
    setDraftRegister("");
    setDraftDivisor(10.0);
  }

  const controlSlotTaken = (slot) => controlChannels.some((c) => c.maps_to === slot);

  function addControlChannel() {
    if (draftCtrlSlaveId === "") return;
    const base = { maps_to: draftMapsTo, kind: draftKind, slave_id: Number(draftCtrlSlaveId), value_range: [Number(draftRangeLo), Number(draftRangeHi)] };
    let ch;
    if (draftKind === "sv_temperature") {
      if (draftCtrlRegister === "") return;
      ch = { ...base, register_address: Number(draftCtrlRegister), divisor: 10.0, sv_range_c: [Number(draftRangeLo), Number(draftRangeHi)] };
    } else if (draftKind === "vfd_drive") {
      if (draftControlRegister === "" || draftFrequencyRegister === "") return;
      ch = {
        ...base,
        control_register: Number(draftControlRegister),
        frequency_register: Number(draftFrequencyRegister),
        frequency_scale: 100.0,
        feedback_register: draftFeedbackRegister === "" ? null : Number(draftFeedbackRegister),
        feedback_divisor: 100.0,
      };
    } else {
      if (draftWriteRegister === "") return;
      ch = {
        ...base,
        write_register: Number(draftWriteRegister),
        write_scale: 1.0,
        feedback_register: draftFeedbackRegister === "" ? null : Number(draftFeedbackRegister),
        feedback_divisor: 1.0,
      };
    }
    setControlChannels((prev) => [...prev, ch]);
    setDraftCtrlSlaveId("");
    setDraftCtrlRegister("");
    setDraftControlRegister("");
    setDraftFrequencyRegister("");
    setDraftWriteRegister("");
    setDraftFeedbackRegister("");
    setDraftRangeLo(0);
    setDraftRangeHi(100);
  }

  async function saveProfile() {
    if (!name.trim() || tempChannels.every((c) => c.role !== "bt")) return;
    setSaving(true);
    setError(null);
    try {
      await api.createDeviceProfile({
        name: name.trim(), baudrate: Number(baudrate), bytesize: Number(bytesize), parity, stopbits: Number(stopbits),
        temp_channels: tempChannels, control_channels: controlChannels,
      });
      resetDraftProfile();
      loadProfiles();
      onChange?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function deleteProfile(id) {
    if (!(await confirm(t("common.deviceProfileEditor.confirmDelete")))) return;
    try {
      await api.deleteDeviceProfile(id);
      loadProfiles();
      onChange?.();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="device-profile-editor">
      <h4>{t("common.deviceProfileEditor.heading")}</h4>
      <div className="device-profile-editor-fields">
          <p className="hint">{t("common.deviceProfileEditor.intro")}</p>
          {error && <p className="error">{error}</p>}

          {profiles.filter((p) => !p.built_in).length > 0 && (
            <ul className="device-profile-list">
              {profiles
                .filter((p) => !p.built_in)
                .map((p) => (
                  <li key={p.id} className="device-profile-row">
                    <span>{p.name}</span>
                    <span className="hint">
                      {t("common.deviceProfileEditor.tempChannels", { count: p.temp_channels.length })},{" "}
                      {t("common.deviceProfileEditor.controlChannels", { count: p.control_channels.length })}
                    </span>
                    <button type="button" className="danger" onClick={() => deleteProfile(p.id)}>
                      {t("common.deviceProfileEditor.delete")}
                    </button>
                  </li>
                ))}
            </ul>
          )}

          <h4>{t("common.deviceProfileEditor.newProfile")}</h4>
          <div className="form-row">
            <label>
              {t("common.deviceProfileEditor.name")}
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder={t("common.deviceProfileEditor.namePlaceholder")} />
            </label>
            <label>
              {t("common.deviceProfileEditor.baudRate")}
              <input type="number" value={baudrate} onChange={(e) => setBaudrate(e.target.value)} />
            </label>
            <label>
              {t("common.deviceProfileEditor.bytesize")}
              <input type="number" value={bytesize} onChange={(e) => setBytesize(e.target.value)} />
            </label>
            <label>
              {t("common.deviceProfileEditor.parity")}
              <select value={parity} onChange={(e) => setParity(e.target.value)}>
                <option value="N">{t("common.deviceProfileEditor.parityNone")}</option>
                <option value="E">{t("common.deviceProfileEditor.parityEven")}</option>
                <option value="O">{t("common.deviceProfileEditor.parityOdd")}</option>
              </select>
            </label>
            <label>
              {t("common.deviceProfileEditor.stopBits")}
              <input type="number" value={stopbits} onChange={(e) => setStopbits(e.target.value)} />
            </label>
          </div>

          <h5>{t("common.deviceProfileEditor.tempChannelsHeading")}</h5>
          {tempChannels.length > 0 && (
            <ul className="device-profile-channel-list">
              {tempChannels.map((ch, i) => (
                <li key={i}>
                  {summarizeTempChannel(ch, t)}
                  <button type="button" className="danger" onClick={() => setTempChannels((prev) => prev.filter((_, j) => j !== i))}>
                    {t("common.deviceProfileEditor.remove")}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="form-row">
            <label>
              {t("common.deviceProfileEditor.role")}
              <select value={draftRole} onChange={(e) => setDraftRole(e.target.value)}>
                {TEMP_ROLES.map((r) => (
                  <option key={r} value={r} disabled={tempRoleTaken(r)}>
                    {r.toUpperCase()}
                    {tempRoleTaken(r) ? t("common.deviceProfileEditor.alreadyAdded") : ""}
                  </option>
                ))}
              </select>
            </label>
            {draftRole === "extra" && (
              <label>
                {t("common.deviceProfileEditor.label")}
                <input type="text" value={draftLabel} onChange={(e) => setDraftLabel(e.target.value)} placeholder={t("common.deviceProfileEditor.labelPlaceholder")} />
              </label>
            )}
            <label>
              {t("common.deviceProfileEditor.slaveId")}
              <input type="number" value={draftSlaveId} onChange={(e) => setDraftSlaveId(e.target.value)} />
            </label>
            <label>
              {t("common.deviceProfileEditor.register")}
              <input type="number" value={draftRegister} onChange={(e) => setDraftRegister(e.target.value)} />
            </label>
            <label>
              {t("common.deviceProfileEditor.divisor")}
              <input type="number" value={draftDivisor} onChange={(e) => setDraftDivisor(e.target.value)} />
            </label>
            <button type="button" onClick={addTempChannel} disabled={tempRoleTaken(draftRole)}>
              {t("common.deviceProfileEditor.addChannel")}
            </button>
          </div>

          <h5>{t("common.deviceProfileEditor.controlChannelsHeading")}</h5>
          {controlChannels.length > 0 && (
            <ul className="device-profile-channel-list">
              {controlChannels.map((ch, i) => (
                <li key={i}>
                  {summarizeControlChannel(ch, t)}
                  <button type="button" className="danger" onClick={() => setControlChannels((prev) => prev.filter((_, j) => j !== i))}>
                    {t("common.deviceProfileEditor.remove")}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="form-row">
            <label>
              {t("common.deviceProfileEditor.controls")}
              <select value={draftMapsTo} onChange={(e) => setDraftMapsTo(e.target.value)}>
                {CONTROL_SLOTS.map((s) => (
                  <option key={s.value} value={s.value} disabled={controlSlotTaken(s.value)}>
                    {s.label}
                    {controlSlotTaken(s.value) ? t("common.deviceProfileEditor.alreadyAdded") : ""}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("common.deviceProfileEditor.mechanism")}
              <select value={draftKind} onChange={(e) => setDraftKind(e.target.value)}>
                {CONTROL_KINDS.map((k) => (
                  <option key={k.value} value={k.value}>
                    {k.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("common.deviceProfileEditor.slaveId")}
              <input type="number" value={draftCtrlSlaveId} onChange={(e) => setDraftCtrlSlaveId(e.target.value)} />
            </label>
          </div>
          <div className="form-row">
            {draftKind === "sv_temperature" && (
              <label>
                {t("common.deviceProfileEditor.setpointRegister")}
                <input type="number" value={draftCtrlRegister} onChange={(e) => setDraftCtrlRegister(e.target.value)} />
              </label>
            )}
            {draftKind === "vfd_drive" && (
              <>
                <label>
                  {t("common.deviceProfileEditor.runStopRegister")}
                  <input type="number" value={draftControlRegister} onChange={(e) => setDraftControlRegister(e.target.value)} />
                </label>
                <label>
                  {t("common.deviceProfileEditor.frequencyRegister")}
                  <input type="number" value={draftFrequencyRegister} onChange={(e) => setDraftFrequencyRegister(e.target.value)} />
                </label>
                <label>
                  {t("common.deviceProfileEditor.feedbackRegisterOptional")}
                  <input type="number" value={draftFeedbackRegister} onChange={(e) => setDraftFeedbackRegister(e.target.value)} />
                </label>
              </>
            )}
            {draftKind === "direct_register" && (
              <>
                <label>
                  {t("common.deviceProfileEditor.writeRegister")}
                  <input type="number" value={draftWriteRegister} onChange={(e) => setDraftWriteRegister(e.target.value)} />
                </label>
                <label>
                  {t("common.deviceProfileEditor.feedbackRegisterOptional")}
                  <input type="number" value={draftFeedbackRegister} onChange={(e) => setDraftFeedbackRegister(e.target.value)} />
                </label>
              </>
            )}
            <label>
              {draftKind === "sv_temperature" ? t("common.deviceProfileEditor.setpointRangeC") : t("common.deviceProfileEditor.valueRangePct")}
              <span className="device-profile-range-inputs">
                <input type="number" value={draftRangeLo} onChange={(e) => setDraftRangeLo(e.target.value)} />
                <input type="number" value={draftRangeHi} onChange={(e) => setDraftRangeHi(e.target.value)} />
              </span>
            </label>
            <button type="button" onClick={addControlChannel} disabled={controlSlotTaken(draftMapsTo)}>
              {t("common.deviceProfileEditor.addChannel")}
            </button>
          </div>

          <button
            type="button"
            onClick={saveProfile}
            disabled={saving || !name.trim() || !tempChannels.some((c) => c.role === "bt")}
            title={!tempChannels.some((c) => c.role === "bt") ? t("common.deviceProfileEditor.btRequired") : undefined}
          >
            {t("common.deviceProfileEditor.saveDeviceProfile")}
          </button>
      </div>
    </div>
  );
}
