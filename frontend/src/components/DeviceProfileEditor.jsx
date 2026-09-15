import { useEffect, useState } from "react";
import { api } from "../api/client.js";

const TEMP_ROLES = ["bt", "et", "dt", "extra"];
const CONTROL_KINDS = [
  { value: "sv_temperature", label: "Setpoint temperature (bang-bang PID)" },
  { value: "vfd_drive", label: "VFD drive (run/stop + frequency)" },
  { value: "direct_register", label: "Direct register (plain 0-100 write)" },
];
const CONTROL_SLOTS = [
  { value: "heater_pct", label: "Burner" },
  { value: "fan_pct", label: "Air" },
  { value: "drum_speed_pct", label: "Drum" },
];

function summarizeTempChannel(ch) {
  const name = ch.role === "extra" ? ch.label || "extra" : ch.role.toUpperCase();
  return `${name}: slave ${ch.slave_id}, register ${ch.register_address}, ÷${ch.divisor}`;
}

function summarizeControlChannel(ch) {
  const slot = CONTROL_SLOTS.find((s) => s.value === ch.maps_to)?.label || ch.maps_to;
  if (ch.kind === "sv_temperature") {
    return `${slot}: setpoint slave ${ch.slave_id} reg ${ch.register_address}, range ${ch.sv_range_c?.[0]}-${ch.sv_range_c?.[1]}°C`;
  }
  if (ch.kind === "vfd_drive") {
    return `${slot}: VFD slave ${ch.slave_id}, ctrl ${ch.control_register}/freq ${ch.frequency_register}, range ${ch.value_range?.[0]}-${ch.value_range?.[1]}%`;
  }
  return `${slot}: direct register slave ${ch.slave_id}, write ${ch.write_register}, range ${ch.value_range?.[0]}-${ch.value_range?.[1]}%`;
}

// Builds and saves a new, custom DeviceProfile -- a named Modbus register
// map for a roaster brand/model this app doesn't ship a built-in profile
// for. Once saved it shows up in LiveRoastView's "Device profile"
// dropdown alongside the built-in ones. Not itself the place a roast
// picks a profile from -- that's the dropdown; this only creates/deletes
// the custom ones (built-in profiles aren't editable here or anywhere,
// see api/device_profiles.py).
export default function DeviceProfileEditor({ onChange }) {
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
    if (!window.confirm("Delete this device profile? Any preset referencing it will fall back to no profile selected.")) return;
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
      <h4>Device profiles (build a config for a different roaster brand)</h4>
      <div className="device-profile-editor-fields">
          <p className="hint">
            A device profile is a full Modbus register map for one roaster brand/model, saved once and reusable
            across roasts (see the "Device profile" dropdown above). Built-in profiles can't be edited here. At
            most 2 "extra" temperature channels round-trip through a saved .alog export (a fixed limit in real
            Artisan's own file format) -- more can still be recorded and charted live.
          </p>
          {error && <p className="error">{error}</p>}

          {profiles.filter((p) => !p.built_in).length > 0 && (
            <ul className="device-profile-list">
              {profiles
                .filter((p) => !p.built_in)
                .map((p) => (
                  <li key={p.id} className="device-profile-row">
                    <span>{p.name}</span>
                    <span className="hint">
                      {p.temp_channels.length} temp channel{p.temp_channels.length === 1 ? "" : "s"},{" "}
                      {p.control_channels.length} control channel{p.control_channels.length === 1 ? "" : "s"}
                    </span>
                    <button type="button" className="danger" onClick={() => deleteProfile(p.id)}>
                      Delete
                    </button>
                  </li>
                ))}
            </ul>
          )}

          <h4>New profile</h4>
          <div className="form-row">
            <label>
              Name
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Acme Roastmaster 5000" />
            </label>
            <label>
              Baud rate
              <input type="number" value={baudrate} onChange={(e) => setBaudrate(e.target.value)} />
            </label>
            <label>
              Bytesize
              <input type="number" value={bytesize} onChange={(e) => setBytesize(e.target.value)} />
            </label>
            <label>
              Parity
              <select value={parity} onChange={(e) => setParity(e.target.value)}>
                <option value="N">None</option>
                <option value="E">Even</option>
                <option value="O">Odd</option>
              </select>
            </label>
            <label>
              Stop bits
              <input type="number" value={stopbits} onChange={(e) => setStopbits(e.target.value)} />
            </label>
          </div>

          <h5>Temperature channels</h5>
          {tempChannels.length > 0 && (
            <ul className="device-profile-channel-list">
              {tempChannels.map((ch, i) => (
                <li key={i}>
                  {summarizeTempChannel(ch)}
                  <button type="button" className="danger" onClick={() => setTempChannels((prev) => prev.filter((_, j) => j !== i))}>
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="form-row">
            <label>
              Role
              <select value={draftRole} onChange={(e) => setDraftRole(e.target.value)}>
                {TEMP_ROLES.map((r) => (
                  <option key={r} value={r} disabled={tempRoleTaken(r)}>
                    {r.toUpperCase()}
                    {tempRoleTaken(r) ? " (already added)" : ""}
                  </option>
                ))}
              </select>
            </label>
            {draftRole === "extra" && (
              <label>
                Label
                <input type="text" value={draftLabel} onChange={(e) => setDraftLabel(e.target.value)} placeholder="e.g. Flue" />
              </label>
            )}
            <label>
              Slave ID
              <input type="number" value={draftSlaveId} onChange={(e) => setDraftSlaveId(e.target.value)} />
            </label>
            <label>
              Register
              <input type="number" value={draftRegister} onChange={(e) => setDraftRegister(e.target.value)} />
            </label>
            <label>
              Divisor
              <input type="number" value={draftDivisor} onChange={(e) => setDraftDivisor(e.target.value)} />
            </label>
            <button type="button" onClick={addTempChannel} disabled={tempRoleTaken(draftRole)}>
              Add channel
            </button>
          </div>

          <h5>Control channels</h5>
          {controlChannels.length > 0 && (
            <ul className="device-profile-channel-list">
              {controlChannels.map((ch, i) => (
                <li key={i}>
                  {summarizeControlChannel(ch)}
                  <button type="button" className="danger" onClick={() => setControlChannels((prev) => prev.filter((_, j) => j !== i))}>
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="form-row">
            <label>
              Controls
              <select value={draftMapsTo} onChange={(e) => setDraftMapsTo(e.target.value)}>
                {CONTROL_SLOTS.map((s) => (
                  <option key={s.value} value={s.value} disabled={controlSlotTaken(s.value)}>
                    {s.label}
                    {controlSlotTaken(s.value) ? " (already added)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Mechanism
              <select value={draftKind} onChange={(e) => setDraftKind(e.target.value)}>
                {CONTROL_KINDS.map((k) => (
                  <option key={k.value} value={k.value}>
                    {k.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Slave ID
              <input type="number" value={draftCtrlSlaveId} onChange={(e) => setDraftCtrlSlaveId(e.target.value)} />
            </label>
          </div>
          <div className="form-row">
            {draftKind === "sv_temperature" && (
              <label>
                Setpoint register
                <input type="number" value={draftCtrlRegister} onChange={(e) => setDraftCtrlRegister(e.target.value)} />
              </label>
            )}
            {draftKind === "vfd_drive" && (
              <>
                <label>
                  Run/stop register
                  <input type="number" value={draftControlRegister} onChange={(e) => setDraftControlRegister(e.target.value)} />
                </label>
                <label>
                  Frequency register
                  <input type="number" value={draftFrequencyRegister} onChange={(e) => setDraftFrequencyRegister(e.target.value)} />
                </label>
                <label>
                  Feedback register (optional)
                  <input type="number" value={draftFeedbackRegister} onChange={(e) => setDraftFeedbackRegister(e.target.value)} />
                </label>
              </>
            )}
            {draftKind === "direct_register" && (
              <>
                <label>
                  Write register
                  <input type="number" value={draftWriteRegister} onChange={(e) => setDraftWriteRegister(e.target.value)} />
                </label>
                <label>
                  Feedback register (optional)
                  <input type="number" value={draftFeedbackRegister} onChange={(e) => setDraftFeedbackRegister(e.target.value)} />
                </label>
              </>
            )}
            <label>
              {draftKind === "sv_temperature" ? "Setpoint range (°C)" : "Value range (%)"}
              <span className="device-profile-range-inputs">
                <input type="number" value={draftRangeLo} onChange={(e) => setDraftRangeLo(e.target.value)} />
                <input type="number" value={draftRangeHi} onChange={(e) => setDraftRangeHi(e.target.value)} />
              </span>
            </label>
            <button type="button" onClick={addControlChannel} disabled={controlSlotTaken(draftMapsTo)}>
              Add channel
            </button>
          </div>

          <button
            type="button"
            onClick={saveProfile}
            disabled={saving || !name.trim() || !tempChannels.some((c) => c.role === "bt")}
            title={!tempChannels.some((c) => c.role === "bt") ? "A BT temperature channel is required" : undefined}
          >
            Save device profile
          </button>
      </div>
    </div>
  );
}
