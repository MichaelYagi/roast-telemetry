"""Pydantic data models shared across the API."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RoastMode(str, Enum):
    SIMULATOR = "simulator"
    ALOG_PLAYBACK = "alog_playback"
    MODBUS_LIVE = "modbus_live"
    MS6514_LIVE = "ms6514_live"


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

# These two are never manually markable -- always auto-detected (a sharp
# BT drop, and the BT minimum right after), no human judgment involved.
ALWAYS_AUTO_EVENT_TYPES = {RoastEventType.CHARGE, RoastEventType.TURNING_POINT}


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


class ControlCommand(BaseModel):
    heater_pct: Optional[float] = Field(default=None, ge=0, le=100)
    fan_pct: Optional[float] = Field(default=None, ge=0, le=100)
    drum_speed_pct: Optional[float] = Field(default=None, ge=0, le=100)
    speed: Optional[float] = Field(default=None, ge=0, description="Playback speed multiplier (alog_playback mode only)")


class RoastCreateRequest(BaseModel):
    title: str
    mode: RoastMode
    beans: Optional[str] = None
    weight_green_g: Optional[float] = None
    alog_path: Optional[str] = Field(default=None, description="Required when mode=alog_playback")
    playback_speed: float = 1.0
    modbus_port: Optional[str] = Field(default=None, description="Required when mode=modbus_live: serial port the roaster is on, e.g. 'COM3'. One connection handles BT/ET/DT/Burner and Air/Drum together, matching Artisan's own shipped Coffee-Tech FZ-94 preset.")
    modbus_baudrate: int = Field(default=19200, description="modbus_live mode only; default matches Artisan's own shipped Coffee-Tech FZ-94 preset (19200/8N2)")
    modbus_control_port: Optional[str] = Field(default=None, description="modbus_live mode only, optional: only set this if your own wiring genuinely needs a *separate* connection for Air/Drum drive control (uncommon) -- e.g. 'COM4'. Leave blank (the normal case) to send Air/Drum over modbus_port along with everything else.")
    modbus_control_baudrate: int = Field(default=19200, description="modbus_live mode only; baud rate for modbus_control_port, if that's set")
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
    dry_end_c: Optional[float] = Field(default=160.0, description="BT threshold for auto-detecting Dry End; live-bridge modes only. Null disables it.")
    fc_start_c: Optional[float] = Field(default=196.0, description="BT threshold for auto-detecting FC Start; live-bridge modes only. Null disables it.")
    sample_interval_s: float = 1.0


class RoastSummary(BaseModel):
    id: str
    title: str
    mode: RoastMode
    status: RoastStatus
    created_at: str
    beans: Optional[str] = None
    weight_green_g: Optional[float] = None
    weight_roasted_g: Optional[float] = None
    duration_s: Optional[float] = None
    alog_path: Optional[str] = None
    # alog_playback mode only: the server-side file being replayed (distinct
    # from `alog_path`, which is where *this* roast's own recording gets
    # saved) and the speed it was started at.
    source_alog_path: Optional[str] = None
    playback_speed: Optional[float] = None


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


class RoastPreset(BaseModel):
    id: str
    name: str
    created_at: str
    config: RoastCreateRequest
    heater_pct: Optional[float] = None
    fan_pct: Optional[float] = None
    drum_speed_pct: Optional[float] = None


# Valid keys for AppSettings.broken_out_panels -- which live readouts the
# Live Roast view should also render large in the optional breakout panel
# (frontend/src/components/BreakoutPanel.jsx), in addition to (not instead
# of) their normal small display elsewhere on the page.
BREAKOUT_PANEL_KEYS = {
    "bt", "et", "dt", "ror_bt", "ror_et", "time",
    "dry_pct", "maillard_pct", "dev_pct", "to_dry", "to_fcs",
    "heater", "fan", "drum", "burner_sv", "playback_speed",
}


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


class OllamaStatus(BaseModel):
    connected: bool
    models: list[str] = []
    error: Optional[str] = None


class ReviewStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class RoastReview(BaseModel):
    roast_id: str
    status: ReviewStatus
    review_text: Optional[str] = None
    error: Optional[str] = None
    model: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


class NoteCreateRequest(BaseModel):
    text: str
    author: Optional[str] = None


class EventCreateRequest(BaseModel):
    type: RoastEventType
    label: str
    value: Optional[float] = None
