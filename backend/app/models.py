"""Pydantic data models shared across the API."""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class RoastMode(str, Enum):
    SIMULATOR = "simulator"
    ALOG_PLAYBACK = "alog_playback"
    MODBUS_LIVE = "modbus_live"
    MS6514_LIVE = "ms6514_live"
    # Genuinely different protocol family from modbus_live (raw USB, not
    # Modbus/serial at all -- see aillio_bridge/engine.py), unlike the
    # FZ-94 Evo's TCP transport, which stayed under modbus_live via
    # modbus_transport since it shares the same register/channel
    # abstraction. R1 vs R2 share this one mode via aillio_model instead,
    # same reasoning as modbus_transport -- same engine contract, only
    # the byte protocol underneath differs.
    AILLIO_LIVE = "aillio_live"
    # TC4+ shield running the aArtisanQ (PID) firmware -- plain ASCII
    # serial commands (READ/OT1/DCFAN), not Modbus/raw-USB -- see
    # tc4_bridge/engine.py's own docstring for the full protocol
    # citation. Its own mode (not folded into modbus_live/ms6514_live)
    # for the same reason aillio_live is separate: a genuinely different
    # protocol family, not just a different transport of an existing one.
    TC4_LIVE = "tc4_live"


class RoastStatus(str, Enum):
    IDLE = "idle"
    ROASTING = "roasting"
    COOLING = "cooling"
    COMPLETE = "complete"  # the engine itself signaled it's done (is_finished())
    STOPPED = "stopped"  # the operator deliberately ended it (OFF/stop) -- the
    # only way any live-hardware mode ever ends, since none of them have an
    # automatic "done" signal; not an error, distinct from ABORTED
    ABORTED = "aborted"  # ended abnormally (an exception in the read/tick loop)


class DeviceStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STREAMING = "streaming"
    ERROR = "error"


class RoastEventType(str, Enum):
    CHARGE = "CHARGE"
    TURNING_POINT = "TURNING_POINT"
    DRY_END = "DRY_END"
    FC_START = "FC_START"
    FC_END = "FC_END"
    SC_START = "SC_START"
    SC_END = "SC_END"
    DROP = "DROP"
    COOL_END = "COOL_END"
    CUSTOM = "CUSTOM"


# Canonical roast-milestone order, matching real Artisan's own button
# behavior: a milestone can only be marked if nothing *later* in this
# sequence has fired yet (skipping ahead is fine -- e.g. Drop without
# ever marking SC Start/SC End -- but going back is not), and once any
# milestone fires (auto or manual), it's permanently set -- no re-marking
# it, and marking a later one locks out any earlier ones that were
# skipped. CUSTOM isn't part of this -- imported real-Artisan manual
# control-channel events are legitimately repeatable, not milestones.
MILESTONE_SEQUENCE = [
    RoastEventType.CHARGE,
    RoastEventType.TURNING_POINT,
    RoastEventType.DRY_END,
    RoastEventType.FC_START,
    RoastEventType.FC_END,
    RoastEventType.SC_START,
    RoastEventType.SC_END,
    RoastEventType.DROP,
    RoastEventType.COOL_END,
]

# Human-readable labels for milestones an AlarmRule auto-marks (see
# AlarmRule.mark_milestone) -- mirrors the labels a manual click would
# use, since as far as add_event() and the roast record are concerned
# an auto-mark IS a manual mark, just triggered by a rule instead of a
# button press.
MILESTONE_LABELS = {
    RoastEventType.CHARGE: "Charge",
    RoastEventType.DRY_END: "Dry End",
    RoastEventType.FC_START: "FC Start",
    RoastEventType.FC_END: "FC End",
    RoastEventType.SC_START: "SC Start",
    RoastEventType.SC_END: "SC End",
    RoastEventType.DROP: "Drop",
    RoastEventType.COOL_END: "Cool End",
}

# TURNING_POINT is never manually markable -- it's a pure observation
# (the BT minimum right after Charge), no human judgment involved, so
# it stays auto-detected for every mode. For modbus_live/ms6514_live,
# where CHARGE itself is a manual click (detect_milestones=False --
# see modbus_bridge/ms6514_bridge), RoastSession.add_event() forwards
# a manual CHARGE to the engine's notify_manual_charge(), which starts
# Turning Point tracking exactly as if CHARGE had auto-fired (see
# roast_heuristics/detector.py) -- confirmed against a real FZ-94 roast
# in Artisan, which auto-plots Turning Point the same way despite every
# other milestone being marked by hand there too. It has no button in
# the UI for any mode (EventButtonRow.jsx), so there's nothing to
# unblock regardless.
ALWAYS_AUTO_EVENT_TYPES = {RoastEventType.TURNING_POINT}


class Device(BaseModel):
    id: str
    name: str
    mode: RoastMode
    status: DeviceStatus
    connected_at: Optional[float] = None
    last_error: Optional[str] = None


class RoastProfilePoint(BaseModel):
    time_s: float
    bt: Optional[float] = None
    et: Optional[float] = None
    # Drum space temperature -- a genuine third probe on some machines
    # (e.g. the Coffee-Tech FZ-94, its own Modbus slave ID; see
    # modbus_bridge/engine.py), not a control value. None for every mode
    # that doesn't have one.
    dt: Optional[float] = None
    ror_bt: Optional[float] = None
    ror_et: Optional[float] = None
    heater_pct: Optional[float] = None
    fan_pct: Optional[float] = None
    drum_speed_pct: Optional[float] = None
    # Burner's raw setpoint in its native unit (°C) -- heater_pct is that
    # same value mapped onto burner_sv_range_c for a 0-100% UI slider;
    # this is what the roaster's own PID controller actually holds.
    # modbus_live only, None for every other mode.
    burner_sv_c: Optional[float] = None
    # Any role=EXTRA temperature channels from a DeviceProfile (see
    # ModbusTempChannel), keyed by that channel's own label -- e.g. a
    # roaster with a flue probe beyond BT/ET/DT. Empty for every mode/
    # profile that doesn't declare one. Only the first two (by the
    # profile's own declared order) round-trip through a real .alog
    # export (alog_playback/alog_io.py's extraname2/extratemp2 bank has
    # exactly 2 free slots) -- more can still be recorded and charted
    # live, they just won't survive an .alog export/reimport beyond that.
    extra: dict[str, float] = {}


class RoastEvent(BaseModel):
    id: str
    time_s: float
    type: RoastEventType
    label: str
    value: Optional[float] = None
    # Control channel this event adjusted ("Air"/"Drum"/"Damper"/"Burner"),
    # when it came from a real Artisan file's manual control-channel log.
    # Absent for auto-detected/simulator/manually-added events.
    channel: Optional[str] = None


class RoastNote(BaseModel):
    id: str
    time_s: float
    text: str
    author: Optional[str] = None


class ModbusChannelRole(str, Enum):
    BT = "bt"
    ET = "et"
    DT = "dt"
    EXTRA = "extra"


class ModbusTempChannel(BaseModel):
    """One temperature probe's register mapping. BT is required on every
    profile (it's the heartbeat -- see ModbusEngine.tick()); ET/DT/EXTRA
    are all optional. Multiple EXTRA channels are allowed (each needs its
    own `label`, e.g. "Flue") -- see RoastProfilePoint.extra for where
    those readings end up, and its docstring for the .alog export cap."""

    role: ModbusChannelRole
    label: Optional[str] = Field(default=None, description="Required for role=extra (e.g. 'Flue'); ignored otherwise (bt/et/dt already have fixed names).")
    slave_id: int
    register_address: int
    divisor: float = 10.0


class ModbusControlKind(str, Enum):
    # Today's Burner: a bang-bang PID setpoint write+readback (see
    # ModbusEngine's module docstring for the FZ-94's own mechanism).
    SV_TEMPERATURE = "sv_temperature"
    # Today's Air/Drum: a VFD run/stop word + separate frequency-command
    # register, optional feedback readback.
    VFD_DRIVE = "vfd_drive"
    # A single register directly holding a plain 0-100(-ish) percentage,
    # one write, no run/stop word, no separate frequency step -- the
    # simpler mechanism plenty of non-FZ-94 roasters actually use.
    DIRECT_REGISTER = "direct_register"


class ModbusControlChannel(BaseModel):
    """One writable channel (Burner/Air/Drum-equivalent), mapped onto one
    of this app's existing three control slots via `maps_to` -- the
    Controls UI sliders and Automation Rules stay heater_pct/fan_pct/
    drum_speed_pct regardless of which physical channel/mechanism backs
    each one. Only the fields relevant to `kind` need to be set; the rest
    are ignored (same tolerant-unused-field convention as AlarmRule's own
    per-trigger-kind fields)."""

    maps_to: Literal["heater_pct", "fan_pct", "drum_speed_pct"]
    kind: ModbusControlKind
    slave_id: int
    # sv_temperature
    register_address: Optional[int] = None
    sv_range_c: Optional[tuple[float, float]] = None
    divisor: float = 10.0
    # vfd_drive
    control_register: Optional[int] = None
    frequency_register: Optional[int] = None
    frequency_scale: float = Field(default=100.0, description="raw = pct * frequency_scale; FZ-94's Delta VFD-L uses 100.")
    # direct_register
    write_register: Optional[int] = None
    write_scale: float = 1.0
    # vfd_drive + direct_register feedback readback (optional either way)
    feedback_register: Optional[int] = None
    feedback_divisor: float = 1.0
    value_range: tuple[float, float] = (0.0, 100.0)


class DeviceProfile(BaseModel):
    """A named, reusable Modbus register map for one roaster brand/model
    -- what used to require new Python code (a new ModbusEngine subclass
    or constructor default) is now data, selected per roast via
    RoastCreateRequest.modbus_device_profile_id instead of the 26 flat
    modbus_* override fields (which still work exactly as before when no
    profile is selected -- this is purely additive, not a replacement).
    `built_in` profiles ship with the app and can't be edited/deleted via
    the API (see api/device_profiles.py)."""

    id: str
    name: str
    created_at: str
    baudrate: int = 19200
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 2
    temp_channels: list[ModbusTempChannel]
    control_channels: list[ModbusControlChannel] = []
    built_in: bool = False

    @classmethod
    def from_row(cls, row: dict) -> "DeviceProfile":
        """Shared by api/device_profiles.py and RoastSession's engine
        construction -- combines a device_profiles table row's own
        id/created_at/built_in with the channel-map fields serialized
        into its config_json (which also redundantly carries `name`,
        kept in sync with the row's own name column by every write)."""
        return cls(
            id=row["id"],
            created_at=row["created_at"],
            built_in=bool(row["built_in"]),
            **DeviceProfileCreateRequest.model_validate_json(row["config_json"]).model_dump(),
        )


class DeviceProfileCreateRequest(BaseModel):
    name: str
    baudrate: int = 19200
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 2
    temp_channels: list[ModbusTempChannel]
    control_channels: list[ModbusControlChannel] = []


class ControlCommand(BaseModel):
    heater_pct: Optional[float] = Field(default=None, ge=0, le=100)
    fan_pct: Optional[float] = Field(default=None, ge=0, le=100)
    drum_speed_pct: Optional[float] = Field(default=None, ge=0, le=100)
    # Alternate write path for the same burner setpoint heater_pct already
    # writes -- native °C instead of a 0-100% mapping, for a second slider
    # that moves the same underlying value (see ModbusEngine.apply_command).
    # No ge/le: valid range is per-DeviceProfile (sv_range_c), not fixed.
    burner_sv_c: Optional[float] = Field(default=None, description="modbus_live only: burner setpoint in native °C, an alternate unit to heater_pct for the same underlying value.")
    speed: Optional[float] = Field(default=None, ge=0, description="Playback speed multiplier (alog_playback mode only)")


class AlarmTriggerKind(str, Enum):
    EVENT = "event"
    TEMPERATURE = "temperature"
    TIME = "time"


class AlarmRule(BaseModel):
    """An automation -- Artisan-style "Alarms": when the trigger condition
    is met, fire a bound command (and/or show a banner message) after
    `delay_s` (0 = as soon as the condition is met, no extra wait).
    modbus_live only -- ms6514_live has no write capability at all.

    Three trigger kinds, one field set each (no cross-field validation --
    an internal form always sends a well-formed payload; a rule missing
    its own kind's field, e.g. a TEMPERATURE rule with threshold_c=None,
    simply never crosses, same tolerance as settings.py's
    _filter_colors/_filter_panels):
    - EVENT: `event_type` is a milestone (see RoastSession.add_event).
      TURNING_POINT/CUSTOM aren't valid (TURNING_POINT is never manually
      markable; CUSTOM isn't a milestone).
    - TEMPERATURE: `channel` ("bt"/"et") crosses `threshold_c`, checked
      every tick while actually recording (RoastSession._evaluate_ambient_alarms).
      One-shot per roast, same as EVENT rules effectively are (a
      milestone can only be marked once) -- doesn't re-fire every tick
      the condition stays true.
    - TIME: elapsed roast time crosses `at_time_s`. Same one-shot
      evaluation as TEMPERATURE.

    Actions are independent of trigger kind and each other -- a rule can
    set any combination of a command, a message, and `mark_milestone`
    (chaining one milestone into auto-marking another, e.g. "30s after
    Turning Point, mark FC End" -- not possible before this field
    existed, since a rule's action could only send a command or show a
    banner, never touch the roast's own event record). `mark_milestone`
    goes through RoastSession.add_event() internally, the exact same
    path a manual button click uses -- inherits every existing safety
    check for free (sequencing, no re-marking, the notify_manual_charge/
    mark_milestone_fired bookkeeping) rather than needing its own copy
    of any of that. TURNING_POINT isn't a valid value (never manually
    markable, auto-only -- see ALWAYS_AUTO_EVENT_TYPES); a value that's
    out of sequence by the time the rule actually fires (e.g. the
    target's already marked, or something later already happened) just
    silently fails to apply, same tolerant failure as a command hitting
    a device error.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    enabled: bool = Field(default=True, description="Uncheck to keep a rule saved but stop it from firing, without deleting it. A rule missing this field (an older saved config) defaults to enabled, same as always.")
    trigger_kind: AlarmTriggerKind = AlarmTriggerKind.EVENT
    event_type: Optional[RoastEventType] = None
    channel: Optional[str] = Field(default=None, description='"bt" or "et" -- TEMPERATURE trigger_kind only')
    threshold_c: Optional[float] = Field(default=None, description="TEMPERATURE trigger_kind only")
    at_time_s: Optional[float] = Field(default=None, ge=0, description="Absolute elapsed roast time in seconds -- TIME trigger_kind only")
    delay_s: float = Field(default=0.0, ge=0, description="Extra grace period after the trigger condition is met, any kind")
    heater_pct: Optional[float] = Field(default=None, ge=0, le=100)
    fan_pct: Optional[float] = Field(default=None, ge=0, le=100)
    drum_speed_pct: Optional[float] = Field(default=None, ge=0, le=100)
    message: Optional[str] = Field(default=None, max_length=200, description="Optional in-app banner text shown when this rule fires")
    mark_milestone: Optional[RoastEventType] = Field(default=None, description="Optional: auto-mark this milestone when the rule fires, chaining one milestone into another. Never TURNING_POINT/CUSTOM.")


class RoastCreateRequest(BaseModel):
    title: str
    mode: RoastMode
    beans: Optional[str] = None
    tags: list[str] = Field(default=[], description="Optional tags, freely editable later via PUT /roasts/{id}/tags -- see RoastDetailView.jsx.")
    weight_green_g: Optional[float] = None
    alog_path: Optional[str] = Field(default=None, description="Required when mode=alog_playback")
    playback_speed: float = 1.0
    modbus_port: Optional[str] = Field(default=None, description="Required when mode=modbus_live: serial port the roaster is on, e.g. 'COM3'. One connection handles BT/ET/DT/Burner and Air/Drum together, matching Artisan's own shipped Coffee-Tech FZ-94 preset.")
    modbus_baudrate: int = Field(default=19200, description="modbus_live mode only; default matches Artisan's own shipped Coffee-Tech FZ-94 preset (19200/8N2)")
    modbus_control_port: Optional[str] = Field(default=None, description="modbus_live mode only, optional: only set this if your own wiring genuinely needs a *separate* connection for Air/Drum drive control (uncommon) -- e.g. 'COM4'. Leave blank (the normal case) to send Air/Drum over modbus_port along with everything else.")
    modbus_control_baudrate: int = Field(default=19200, description="modbus_live mode only; baud rate for modbus_control_port, if that's set")
    modbus_device_profile_id: Optional[str] = Field(default=None, description="modbus_live, optional: use a saved/built-in DeviceProfile's full channel map instead of the individual modbus_* override fields below. When set, those flat fields are ignored (a profile fully replaces them, no merging) -- see api/device_profiles.py. Leave unset (the default) for exactly today's behavior.")
    modbus_transport: str = Field(default="serial", description="modbus_live only: 'serial' (USB/RTU, the default -- uses modbus_port) or 'tcp' (Modbus TCP/Ethernet, e.g. the Coffee-Tech FZ-94 Evo -- uses modbus_host/modbus_tcp_port instead). A genuinely different wire protocol, not just a different port string -- see modbus_bridge/engine.py.")
    modbus_host: Optional[str] = Field(default=None, description="Required when mode=modbus_live and modbus_transport='tcp': the roaster's IP/hostname, e.g. '192.168.1.2'.")
    modbus_tcp_port: int = Field(default=502, description="modbus_live + modbus_transport='tcp' only: TCP port, default 502 (the standard Modbus TCP port).")
    # Full ModbusEngine register-map override set -- all optional and None
    # by default, meaning "use ModbusEngine's own (FZ-94) default"; only
    # set what your own unit actually needs overridden. BT/ET/DT/Burner's
    # own slave/register/divisor are confirmed against Artisan's shipped
    # FZ94.aset (see modbus_bridge/engine.py's docstring) -- exposed here
    # anyway for a genuinely different Modbus roaster, not because they're
    # expected to need changing for an actual FZ-94. Air/Drum and
    # burner_sv_range_c are that engine's least-confirmed defaults
    # (blog-sourced only, one person's own installation). Every *_min_c
    # and *_min_pct/*_max_pct pair requires both halves together to take
    # effect (a lone one is ignored rather than guessing the other half).
    modbus_bt_slave_id: Optional[int] = Field(default=None, description="modbus_live, advanced: BT probe Modbus slave ID. Default 11.")
    modbus_bt_register: Optional[int] = Field(default=None, description="modbus_live, advanced: BT probe register. Default 0.")
    modbus_bt_divisor: Optional[float] = Field(default=None, description="modbus_live, advanced: BT raw-value divisor (raw/divisor = °C). Default 10.")
    modbus_et_slave_id: Optional[int] = Field(default=None, description="modbus_live, advanced: ET probe Modbus slave ID. Default 13.")
    modbus_et_register: Optional[int] = Field(default=None, description="modbus_live, advanced: ET probe register. Default 0.")
    modbus_et_divisor: Optional[float] = Field(default=None, description="modbus_live, advanced: ET raw-value divisor (raw/divisor = °C). Default 10.")
    modbus_dt_slave_id: Optional[int] = Field(default=None, description="modbus_live, advanced: DT probe Modbus slave ID. Default 12.")
    modbus_dt_register: Optional[int] = Field(default=None, description="modbus_live, advanced: DT probe register. Default 0.")
    modbus_dt_divisor: Optional[float] = Field(default=None, description="modbus_live, advanced: DT raw-value divisor (raw/divisor = °C). Default 10.")
    modbus_burner_slave_id: Optional[int] = Field(default=None, description="modbus_live, advanced: Burner PID Modbus slave ID. Default 12 (same PID device as DT).")
    modbus_burner_register: Optional[int] = Field(default=None, description="modbus_live, advanced: Burner SV register (read + write). Default 5.")
    modbus_burner_divisor: Optional[float] = Field(default=None, description="modbus_live, advanced: Burner SV raw-value divisor (raw/divisor = °C). Default 10.")
    modbus_air_slave_id: Optional[int] = Field(default=None, description="modbus_live, advanced: Air VFD Modbus slave ID. Default 1.")
    modbus_air_control_register: Optional[int] = Field(default=None, description="modbus_live, advanced: Air VFD run/stop register. Default 8192.")
    modbus_air_frequency_register: Optional[int] = Field(default=None, description="modbus_live, advanced: Air VFD frequency-command register. Default 8193.")
    modbus_air_feedback_register: Optional[int] = Field(default=None, description="modbus_live, advanced: Air VFD actual-speed readback register. Default 8451.")
    modbus_air_min_pct: Optional[float] = Field(default=None, description="modbus_live, advanced: Air minimum %, paired with modbus_air_max_pct (both required together). Default 0.")
    modbus_air_max_pct: Optional[float] = Field(default=None, description="modbus_live, advanced: Air maximum %, paired with modbus_air_min_pct (both required together). Default 100.")
    modbus_drum_slave_id: Optional[int] = Field(default=None, description="modbus_live, advanced: Drum VFD Modbus slave ID. Default 2.")
    modbus_drum_control_register: Optional[int] = Field(default=None, description="modbus_live, advanced: Drum VFD run/stop register. Default 8192.")
    modbus_drum_frequency_register: Optional[int] = Field(default=None, description="modbus_live, advanced: Drum VFD frequency-command register. Default 8193.")
    modbus_drum_feedback_register: Optional[int] = Field(default=None, description="modbus_live, advanced: Drum VFD actual-speed readback register. Default 8451.")
    modbus_drum_min_pct: Optional[float] = Field(default=None, description="modbus_live, advanced: Drum minimum %, paired with modbus_drum_max_pct (both required together). Default 0.")
    modbus_drum_max_pct: Optional[float] = Field(default=None, description="modbus_live, advanced: Drum maximum %, paired with modbus_drum_min_pct (both required together). Default 70.")
    modbus_burner_sv_min_c: Optional[float] = Field(default=None, description="modbus_live, advanced: low end of the heater_pct(0%)->SV-temperature mapping, paired with modbus_burner_sv_max_c (both required together). Default 100.")
    modbus_burner_sv_max_c: Optional[float] = Field(default=None, description="modbus_live, advanced: high end of the heater_pct(100%)->SV-temperature mapping, paired with modbus_burner_sv_min_c (both required together). Default 250.")
    ms6514_port: Optional[str] = Field(default=None, description="Required when mode=ms6514_live: serial port the Mastech MS6514 is on, e.g. 'COM5'")
    aillio_model: Optional[str] = Field(default=None, description="Required when mode=aillio_live: which Aillio Bullet model, e.g. 'r1' (see aillio_bridge.engine.PROTOCOLS for the known set). A raw USB device, not a port/host -- there's nothing else to configure per-install.")
    tc4_port: Optional[str] = Field(default=None, description="Required when mode=tc4_live: serial port the TC4+ shield is on, e.g. 'COM5'")
    auto_detect_milestones: bool = Field(default=False, description="live-bridge modes only, opt-in: auto-fire Charge/Dry End/FC Start from the BT curve instead of manual clicks only (Turning Point stays automatic either way -- see roast_heuristics.LiveRoastDetector). Off by default -- real hardware means a real operator, not an algorithm guessing, unless explicitly turned on. Manual clicks still work as an override even when on.")
    dry_end_c: Optional[float] = Field(default=160.0, description="BT threshold for auto-detecting Dry End when auto_detect_milestones is on; live-bridge modes only. Null disables it.")
    fc_start_c: Optional[float] = Field(default=196.0, description="BT threshold for auto-detecting FC Start when auto_detect_milestones is on; live-bridge modes only. Null disables it.")
    sample_interval_s: float = 1.0
    alarms: list[AlarmRule] = Field(default=[], description="modbus_live only: event/temperature/time-triggered automations (Artisan-style Alarms). Part of the roast's own config, not a runtime command -- rides through saved-preset config_json for free.")


class RoastSummary(BaseModel):
    id: str
    title: str
    mode: RoastMode
    status: RoastStatus
    created_at: str
    beans: Optional[str] = None
    tags: list[str] = []
    weight_green_g: Optional[float] = None
    weight_roasted_g: Optional[float] = None
    duration_s: Optional[float] = None
    alog_path: Optional[str] = None
    # Whoever was logged in when this roast was created/imported -- a
    # username, not a user_id, so it stays meaningful even if that account
    # is later deleted (see storage.py's migration comment). None for any
    # roast recorded before this field existed.
    created_by_username: Optional[str] = None
    # alog_playback mode only: the server-side file being replayed (distinct
    # from `alog_path`, which is where *this* roast's own recording gets
    # saved) and the speed it was started at.
    source_alog_path: Optional[str] = None
    playback_speed: Optional[float] = None
    # live-bridge modes only: whatever this roast was actually configured
    # with at creation time -- lets a client that only has the roast id
    # (a reconnect after a page refresh, say) recover the real thresholds
    # instead of falling back to a form's own mount-time defaults.
    auto_detect_milestones: bool = False
    dry_end_c: Optional[float] = None
    fc_start_c: Optional[float] = None
    # modbus_live only, when the connected profile/register-map has an
    # SV_TEMPERATURE burner channel: the °C bounds heater_pct's 0-100% is
    # mapped onto (see ModbusControlChannel.sv_range_c). Lets the frontend's
    # vertical control panel convert heater_pct<->burner_sv_c locally while
    # dragging either slider, instead of waiting on a telemetry round trip.
    burner_sv_range_c: Optional[tuple[float, float]] = None
    # modbus_live only: what this roast was actually connected with, frozen
    # at connect time -- shown in the live view, History detail, and the
    # server log (see RoastSession.__init__) so "which config was this"
    # is always answerable, not just visible transiently in the Configure
    # Roast form's own state. modbus_device_profile_name is the resolved
    # name (not just modbus_device_profile_id), so it still reads
    # correctly even if that profile is later renamed or deleted.
    modbus_transport: Optional[str] = None
    modbus_port: Optional[str] = None
    modbus_host: Optional[str] = None
    modbus_tcp_port: Optional[int] = None
    modbus_device_profile_name: Optional[str] = None
    # ms6514_live only, same "freeze what was configured" reasoning as
    # the modbus_* fields above.
    ms6514_port: Optional[str] = None
    # aillio_live only, same reasoning again.
    aillio_model: Optional[str] = None
    # tc4_live only, same reasoning again.
    tc4_port: Optional[str] = None


class Roast(RoastSummary):
    profile: list[RoastProfilePoint] = []
    events: list[RoastEvent] = []
    notes: list[RoastNote] = []


class RoastPresetCreateRequest(BaseModel):
    name: str
    config: RoastCreateRequest
    # Control-channel starting point (simulator/modbus_live only). Kept
    # separate from `config` since these are runtime commands sent after
    # a roast starts, not part of RoastCreateRequest -- but a preset still
    # wants to remember and replay them as the roast's first command.
    heater_pct: Optional[float] = Field(default=None, ge=0, le=100)
    fan_pct: Optional[float] = Field(default=None, ge=0, le=100)
    drum_speed_pct: Optional[float] = Field(default=None, ge=0, le=100)
    manufacturer: Optional[str] = Field(default=None, description="Optional grouping label for the Load saved config dropdown, e.g. 'Coffee-Tech'. Mainly used by built-in presets (see main.py's _DEFAULT_PRESETS) but settable on any preset.")


class RoastPreset(BaseModel):
    """`built_in` presets ship with the app and can't be deleted via the
    API (see api/presets.py) -- same precedent as DeviceProfile.built_in."""

    id: str
    name: str
    created_at: str
    config: RoastCreateRequest
    heater_pct: Optional[float] = None
    fan_pct: Optional[float] = None
    drum_speed_pct: Optional[float] = None
    manufacturer: Optional[str] = None
    built_in: bool = False


# Valid keys for AppSettings.vertical_control_layout/vertical_control_arrows
# -- the four channels the vertical control panel (left of the live chart,
# see frontend/src/components/VerticalControlPanel.jsx) can show.
# drum_speed_pct/fan_pct are always shown regardless of settings (there's
# no Controls panel fallback anymore -- see VerticalControlPanel's own
# comment); heater_pct/burner_sv_c are the two configurable ones, and at
# least one of those two must always be present (enforced by the settings
# editor UI, not here -- this is just the valid-key allowlist).
VERTICAL_CONTROL_KEYS = {"drum_speed_pct", "fan_pct", "heater_pct", "burner_sv_c"}

# Valid keys for AppSettings.broken_out_panels -- which live readouts the
# Live Roast view should also render large in the optional breakout panel
# (frontend/src/components/BreakoutPanel.jsx), in addition to (not instead
# of) their normal small display elsewhere on the page.
BREAKOUT_PANEL_KEYS = {
    "bt", "et", "dt", "ror_bt", "ror_et", "time",
    "dry_pct", "maillard_pct", "dev_pct", "to_dry", "to_fcs", "to_dev",
    "heater", "fan", "drum", "burner_sv", "playback_speed",
}

# Valid keys for AppSettings.chart_series_visible -- the live chart's own
# legend checkboxes (frontend/src/components/RoastChart.jsx's SERIES_DEFS).
# Only the static, always-present curves -- background-roast overlay
# (BG_BT/BG_ET) and per-roast extra temperature channels (EXTRA_<label>)
# are built dynamically there and never meant to be toggled persistently.
CHART_SERIES_KEYS = {"BT", "ET", "DT", "ROR_BT", "ROR_ET", "Burner", "Air", "Drum", "Damper"}


class AppSettings(BaseModel):
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    broken_out_panels: list[str] = []
    # BREAKOUT_PANEL_KEYS -> hex color, overriding that item's own default
    # in frontend/src/breakoutPanels.js's BREAKOUT_PANEL_ITEMS. A key
    # absent here just means "use the built-in default" -- this only
    # needs to hold actual overrides, not a full copy of every item.
    # Shared by broken_out_panels AND small_readout_panels below (colors
    # are a property of the item -- e.g. "bt" is the same blue everywhere
    # it's shown -- not something that should drift between two panels
    # showing the same value).
    breakout_panel_colors: dict[str, str] = {}
    # Independent from broken_out_panels above -- same BREAKOUT_PANEL_ITEMS
    # registry and the same breakout_panel_colors, but its own ordered
    # enabled list, rendered as a compact scaled column beside the chart at
    # any width (not gated to broken_out_panels' >=1400px split-layout
    # threshold). storage.get_settings() seeds this to
    # ["et", "bt", "dt", "ror_bt"] only the first time (key never saved
    # before) -- matches the fixed ET/BT/DT/deltaBT legend this replaced,
    # so existing installs see no visual change until they actually touch
    # Settings > Small Readout.
    small_readout_panels: list[str] = []
    # Display-only, "c" or "f" -- same idea as Artisan's own Config >
    # Temperature > Fahrenheit/Celsius Mode toggle. Only affects how
    # already-Celsius values are *shown* (readouts, chart, event history);
    # every stored value, every config input field (thresholds, SV
    # ranges, alarm rule temperatures), and everything sent to/from the
    # API stays Celsius always -- converting those bidirectionally was
    # judged more confusing than useful for a feature nobody asked for
    # beyond "let me see the numbers in my preferred unit".
    temperature_unit: str = "c"
    # Vertical control panel (left of the live chart, replaces the old
    # always-visible horizontal Controls panel entirely -- see
    # VerticalControlPanel.jsx). Ordered list of "lanes" (left to right),
    # each lane an ordered list of 1+ VERTICAL_CONTROL_KEYS -- a lane with
    # one key renders as its own siloed, full-height slider; a lane with
    # multiple keys splits that lane's height between them, stacked one
    # above the other top to bottom (confirmed against a real Artisan
    # screenshot: Drum sits in the top half of its lane, Fan in the bottom
    # half, same x-position throughout) -- each still its own independent
    # draggable control, just sharing width with its lane-mates instead of
    # getting a lane of its own. storage.get_settings() seeds this to
    # [["drum_speed_pct"], ["fan_pct"], ["heater_pct"]] only the first time
    # (key never saved before), matching what the old Controls panel always
    # showed -- SV stays opt-in.
    vertical_control_layout: list[list[str]] = []
    # Per-channel: show +/- increment/decrement buttons above and below
    # that slider, and how far each press moves it. Missing key (the
    # default -- "default to all off") or a non-positive value means off,
    # same tolerant-missing-key convention as breakout_panel_colors; a
    # positive number means on, moving that far per press. Used to be a
    # plain bool (arrows shown or not, always stepping by a hardcoded 1) --
    # see storage.get_settings()'s own migration for old True/False rows.
    vertical_control_arrows: dict[str, float] = {}
    # The live chart's own legend checkboxes (RoastChart.jsx's SERIES_DEFS
    # -- BT/ET/DT/RoR BT/RoR ET/Burner/Air/Drum/Damper), so re-checking the
    # same boxes every time a roast page loads isn't required. Missing key
    # means "use that series' own defaultOn", not force-off -- same
    # tolerant-missing-key convention as vertical_control_arrows/
    # breakout_panel_colors above, resolved client-side in RoastChart.jsx
    # (this dict only ever needs to hold actual overrides).
    chart_series_visible: dict[str, bool] = {}
    # How many roasts HistoryDashboard.jsx fetches per page (GET
    # /roasts' own limit/offset). Clamped server-side to 10-500 in
    # api/settings.py's update_settings -- 500 matches GET /roasts'
    # own existing `limit` query param cap.
    history_page_size: int = 100


class OllamaStatus(BaseModel):
    connected: bool
    models: list[str] = []
    error: Optional[str] = None


class ReviewStatus(str, Enum):
    # No review row exists yet -- the default, common state for most
    # roasts (nobody's clicked "Generate review"). GET /review returns
    # this with a 200, not a 404 -- see that endpoint's own comment for
    # why: 404 collapsed "no review yet" (routine, happens on every
    # unreviewed roast's detail page) into the same status as a genuinely
    # broken request, showing up as a failed network request in the
    # browser console on ordinary navigation, not just when something's
    # actually wrong.
    NONE = "none"
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class RoastReview(BaseModel):
    roast_id: str
    status: ReviewStatus
    review_text: Optional[str] = None
    error: Optional[str] = None
    model: Optional[str] = None
    # Optional -- genuinely absent for status=none (no row exists to have
    # a created_at at all), present for every other status.
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class RoastPhaseStat(BaseModel):
    """One PHASE_DEFS entry from roast_stats.py -- Dry (CHARGE->DRY_END),
    Maillard (DRY_END->FC_START), or Development (FC_START->DROP).
    pct_of_roast is DRY%/DTR respectively, the same numbers Artisan's own
    phase breakdown shows -- absent entirely (not just null fields) when
    the roast is missing the milestones a phase needs."""

    phase: str
    duration_s: float
    pct_of_roast: Optional[float] = None


class RoastRorFlags(BaseModel):
    """Crash/flatline/flick windows detected on the RoR(BT) curve after
    Turning Point -- see roast_stats.py's own threshold constants for
    what counts as each. Each list entry is a small dict (time_s +
    supporting values), capped at 5 per category."""

    crashes: list[dict] = []
    flatlines: list[dict] = []
    flicks: list[dict] = []


class RoastStats(BaseModel):
    """GET /roasts/{id}/stats -- derived metrics computed purely from a
    roast's own profile/events/weights, no new data collected. dry_pct/
    dtr_pct are convenience pulls from `phases` (the Dry/Development
    entries' own pct_of_roast) so a caller that only wants the headline
    numbers doesn't have to search the phases list itself."""

    weight_loss_pct: Optional[float] = None
    duration_s: Optional[float] = None
    phases: list[RoastPhaseStat] = []
    dry_pct: Optional[float] = None
    dtr_pct: Optional[float] = None
    ror_flags: RoastRorFlags = RoastRorFlags()


class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"


class UserStatus(str, Enum):
    # PENDING -> ALLOWED (Allow) or DENIED (Deny); ALLOWED -> DENIED (Deny,
    # revokes access immediately -- see api/auth.py's deny_user, which also
    # kills that user's active sessions); DENIED -> PENDING only (Reset to
    # pending -- an admin reconsidering a denial goes back through the
    # normal approval step rather than being allowed directly). Delete is
    # separate and always available, from any status.
    PENDING = "pending"
    ALLOWED = "allowed"
    DENIED = "denied"


class RegisterRequest(BaseModel):
    """The very first account ever created becomes the admin and is
    auto-approved (see api/auth.py's register()); every account after
    that starts PENDING and can't log in until the admin allows it."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=200)


class ChangePasswordRequest(BaseModel):
    """Self-service -- see api/auth.py's change_password. Requires the
    current password (not just a valid session) so a browser left signed
    in somewhere doesn't let anyone who walks up to it lock the real
    owner out by setting a new one."""

    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


class LoginRequest(BaseModel):
    username: str
    password: str
    # Unchecked (the default) -- a plain session cookie, gone as soon as
    # the browser closes. Checked -- stays signed in until an explicit
    # Log out, not just until the browser happens to close (see
    # auth.start_session).
    remember_me: bool = False


class UserPublic(BaseModel):
    """A user row with password_hash/api_key_hash stripped -- the only
    shape ever sent to a client, whether that's the logged-in user's own
    /auth/me or the admin-only /auth/users list. has_api_key says whether
    one is currently active, never the key itself -- see ApiKeyIssued,
    the one-time response that actually carries it."""

    id: str
    username: str
    role: UserRole
    status: UserStatus
    created_at: str
    has_api_key: bool = False


class ApiKeyIssued(BaseModel):
    """Returned exactly once, by POST /auth/api-key -- the plaintext key
    is never recoverable after this response; only its hash is stored
    (see auth.hash_api_key), so even this app's own admin/DB access can't
    show it again. Generating or regenerating both return this same
    shape (regenerating just means the previous key stops working)."""

    api_key: str


class SerialPortInfo(BaseModel):
    device: str  # what actually goes in the form's Serial port field, e.g. "COM3" or "/dev/ttyUSB0"
    description: Optional[str] = None  # driver-reported label, e.g. "USB-SERIAL CH340 (COM3)" -- None if the OS has nothing better than the bare device name


class FileEntry(BaseModel):
    name: str
    path: str  # full server-side path -- what goes in the import/playback path field
    kind: Literal["dir", "file"]
    size: Optional[int] = None  # bytes; files only


class FileShortcut(BaseModel):
    label: str
    path: str


class FileListing(BaseModel):
    path: str  # the folder that was listed (normalized)
    parent: Optional[str] = None  # None at a filesystem root
    entries: list[FileEntry]
    shortcuts: list[FileShortcut]
    truncated: bool = False  # True if the folder had more entries than were returned


class NoteCreateRequest(BaseModel):
    text: str
    author: Optional[str] = None


class EventCreateRequest(BaseModel):
    type: RoastEventType
    label: str
    value: Optional[float] = None


class EventUpdateRequest(BaseModel):
    """Retime an already-marked milestone -- see RoastSession.retime_event.
    Only time_s is client-supplied; `value` (the temperature at that
    instant) is always recomputed server-side from the profile, never
    trusted from the client, since a stale/wrong value would silently
    mismatch the marker's new position on the chart."""

    time_s: float


class TagsUpdateRequest(BaseModel):
    """PUT /roasts/{id}/tags body -- replace-the-whole-set semantics, see
    storage.set_roast_tags. A list doesn't fit the plain-query-param
    convention the weight endpoints use, hence a real body model here."""

    tags: list[str]
