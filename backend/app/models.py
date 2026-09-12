"""Pydantic data models shared across the API."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RoastMode(str, Enum):
    SIMULATOR = "simulator"
    ALOG_PLAYBACK = "alog_playback"
    ARTISAN_LIVE = "artisan_live"
    MODBUS_LIVE = "modbus_live"
    MS6514_LIVE = "ms6514_live"


class RoastStatus(str, Enum):
    IDLE = "idle"
    ROASTING = "roasting"
    COOLING = "cooling"
    COMPLETE = "complete"
    ABORTED = "aborted"


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


class Machine(BaseModel):
    id: str
    brand: str
    model: str
    capabilities: list[str]
    control_capable: bool
    connection_type: str
    simulated: bool = True
    notes: Optional[str] = None


class Device(BaseModel):
    id: str
    name: str
    mode: RoastMode
    machine_id: Optional[str] = None
    status: DeviceStatus
    connected_at: Optional[float] = None
    last_error: Optional[str] = None


class RoastProfilePoint(BaseModel):
    time_s: float
    bt: Optional[float] = None
    et: Optional[float] = None
    ror_bt: Optional[float] = None
    ror_et: Optional[float] = None
    heater_pct: Optional[float] = None
    fan_pct: Optional[float] = None
    drum_speed_pct: Optional[float] = None


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
    machine_id: Optional[str] = None
    beans: Optional[str] = None
    weight_green_g: Optional[float] = None
    alog_path: Optional[str] = Field(default=None, description="Required when mode=alog_playback")
    playback_speed: float = 1.0
    artisan_host: Optional[str] = Field(default=None, description="Required when mode=artisan_live: host/IP running Artisan with WebLCDs enabled")
    artisan_port: int = Field(default=8080, description="Artisan's WebLCDs port (Config > Curves > UI tab); artisan_live mode only")
    modbus_port: Optional[str] = Field(default=None, description="Required when mode=modbus_live: serial port the roaster is on, e.g. 'COM3'")
    modbus_baudrate: int = Field(default=57600, description="modbus_live mode only; default matches Coffee-Tech FZ94 EVO")
    ms6514_port: Optional[str] = Field(default=None, description="Required when mode=ms6514_live: serial port the Mastech MS6514 is on, e.g. 'COM5'")
    dry_end_c: Optional[float] = Field(default=160.0, description="BT threshold for auto-detecting Dry End; live-bridge modes only. Null disables it.")
    fc_start_c: Optional[float] = Field(default=196.0, description="BT threshold for auto-detecting FC Start; live-bridge modes only. Null disables it.")
    sample_interval_s: float = 1.0


class RoastSummary(BaseModel):
    id: str
    title: str
    mode: RoastMode
    machine_id: Optional[str] = None
    # Freeform roaster name/model, e.g. from a real .alog's `roastertype`
    # field, for roasts with no catalog `machine_id` to look up.
    machine_label: Optional[str] = None
    status: RoastStatus
    created_at: str
    beans: Optional[str] = None
    weight_green_g: Optional[float] = None
    weight_roasted_g: Optional[float] = None
    duration_s: Optional[float] = None
    alog_path: Optional[str] = None


class Roast(RoastSummary):
    profile: list[RoastProfilePoint] = []
    events: list[RoastEvent] = []
    notes: list[RoastNote] = []


class NoteCreateRequest(BaseModel):
    text: str
    author: Optional[str] = None


class EventCreateRequest(BaseModel):
    type: RoastEventType
    label: str
    value: Optional[float] = None
