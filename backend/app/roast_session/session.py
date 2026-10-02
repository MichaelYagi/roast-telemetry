"""Live roast state management: orchestrates a device (simulator or
playback) over its lifecycle and streams samples out over pub/sub.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from typing import Optional

from aillio_bridge import AillioEngine
from hardware_fakes import sim as simulated_devices
from alog_playback import (
    AlogPlayer,
    alog_dict_to_points,
    alog_created_at,
    assign_note_ids,
    load_alog,
    note_timestamp,
    notes_search_text,
    round_note_time,
    roast_to_native_alog_dict,
    save_native_alog,
)
from alog_playback.alog_io import TIMEINDEX_LABELS
from mock_device import MockDevice
from modbus_bridge import ModbusEngine
from ms6514_bridge import MS6514Engine
from pymodbus.client import ModbusSerialClient, ModbusTcpClient
from simulator import SimulatorEngine
from tc4_bridge import TC4Engine

from .. import storage
from ..beans_text import parse_beans_description
from .control import RoastControl, RoastControlError
from ..models import (
    ALWAYS_AUTO_EVENT_TYPES,
    MILESTONE_LABELS,
    MILESTONE_SEQUENCE,
    AlarmRule,
    AlarmTriggerKind,
    ControlCommand,
    DeviceProfile,
    EventCreateRequest,
    ModbusControlKind,
    NoteCreateRequest,
    Roast,
    RoastCreateRequest,
    RoastEventType,
    RoastMode,
    RoastStatus,
    RoastSummary,
)
from ..ws_manager import active_roast_pubsub, pubsub

logger = logging.getLogger(__name__)

# How a roast's .alog file names each milestone it stores ("First Crack
# Start") -- a milestone added to a finished roast gets this label, so it
# reads the same as one loaded back from the file.
ALOG_MILESTONE_LABELS = dict(TIMEINDEX_LABELS)


class RoastSessionError(RuntimeError):
    pass


def _modbus_register_overrides(request: RoastCreateRequest) -> dict:
    """Builds ModbusEngine kwargs from RoastCreateRequest's optional
    register-map overrides -- only includes a key when the request
    actually set it, so leaving them blank keeps ModbusEngine's own
    defaults exactly as before this override mechanism existed. The range
    pairs (air/drum %, burner SV °C) only apply when *both* halves are
    given together -- a lone min or max is ignored rather than guessing
    the other half from ModbusEngine's own default, which would silently
    duplicate (and risk drifting from) that default here."""
    overrides: dict = {}
    single_value_fields = (
        "modbus_bt_slave_id", "modbus_bt_register", "modbus_bt_divisor",
        "modbus_et_slave_id", "modbus_et_register", "modbus_et_divisor",
        "modbus_dt_slave_id", "modbus_dt_register", "modbus_dt_divisor",
        "modbus_burner_slave_id", "modbus_burner_register", "modbus_burner_divisor",
        "modbus_air_slave_id", "modbus_air_control_register", "modbus_air_frequency_register",
        "modbus_air_feedback_register", "modbus_air_frequency_scale", "modbus_air_frequency_offset",
        "modbus_drum_slave_id", "modbus_drum_control_register", "modbus_drum_frequency_register",
        "modbus_drum_feedback_register", "modbus_drum_frequency_scale", "modbus_drum_frequency_offset",
    )
    for field in single_value_fields:
        value = getattr(request, field)
        if value is not None:
            overrides[field.removeprefix("modbus_")] = value

    if request.modbus_air_min_pct is not None and request.modbus_air_max_pct is not None:
        overrides["air_range"] = (request.modbus_air_min_pct, request.modbus_air_max_pct)
    if request.modbus_drum_min_pct is not None and request.modbus_drum_max_pct is not None:
        overrides["drum_range"] = (request.modbus_drum_min_pct, request.modbus_drum_max_pct)
    if request.modbus_burner_sv_min_c is not None and request.modbus_burner_sv_max_c is not None:
        overrides["burner_sv_range_c"] = (request.modbus_burner_sv_min_c, request.modbus_burner_sv_max_c)
    return overrides


# Columns edited after the roast, straight in the database.
OUTCOME_KEYS = ("color_agtron", "cupping_score", "rating", "tasting_notes", "bean_id")


def _persistent_flags_from_row(row: dict) -> dict:
    """had_emergency_stop/reached_drop are also written straight to the
    database (RoastControl.enter_safe_state / RoastSession._finish), not
    tracked on the live in-memory session -- same "overlay from the DB
    row" reasoning as OUTCOME_KEYS above, just needing an explicit
    int->bool coercion (unlike OUTCOME_KEYS' naturally-matching types)
    since SQLite hands back a plain 0/1/None. reached_drop stays None
    ("unknown"), not False, when the column itself is NULL."""
    reached_drop = row.get("reached_drop")
    return {
        "had_emergency_stop": bool(row.get("had_emergency_stop")),
        "reached_drop": bool(reached_drop) if reached_drop is not None else None,
    }


def roast_duration_s(profile: list, events: list) -> float:
    """A roast's duration is Charge to Drop -- the same span the phase
    percentages use, and what "roast time" means when roasting. Without
    both markers (a roast stopped early, or one that never marked them) it
    falls back to the length of the recording."""
    charge = next((e for e in events if e.get("type") == "CHARGE"), None)
    drop = next((e for e in events if e.get("type") == "DROP"), None)
    if charge and drop and drop["time_s"] > charge["time_s"]:
        return round(drop["time_s"] - charge["time_s"], 1)
    return profile[-1]["time_s"] if profile else 0.0


def _clean_note_text(text: str) -> str:
    """Notes are stored one per line in the .alog file's free-text field,
    so a note is a single line: line breaks become spaces. Blank is
    rejected."""
    cleaned = " ".join(text.split())
    if not cleaned:
        raise RoastSessionError("a note can't be empty")
    return cleaned


def _find_note(notes: list, note_id: str) -> dict:
    note = next((n for n in notes if n["id"] == note_id), None)
    if note is None:
        raise RoastSessionError("That note was changed or removed elsewhere -- showing the latest notes.")
    return note


def milestone_name(event_type: "RoastEventType") -> str:
    """How a milestone is named in a message a person reads ("Dry End",
    not "DRY_END") -- error messages the apps show as they are, and the
    Activity log."""
    if event_type == RoastEventType.TURNING_POINT:
        return "Turning Point"
    return MILESTONE_LABELS.get(event_type, event_type.value)


def _find_editable_milestone(events: list, event_id: str) -> tuple[dict, "RoastEventType"]:
    """Shared by RoastSession.delete_event/retime_event (a live session's
    own self.events) and RoastSessionManager's cold-roast path (a plain
    list freshly parsed from a .alog file, no RoastSession involved at
    all) -- both need the exact same find-by-id and Turning-Point/CUSTOM
    guards, and duplicating them would just be an easy place for the two
    paths to quietly drift apart."""
    event = next((e for e in events if e["id"] == event_id), None)
    if event is None:
        raise RoastSessionError(f"event {event_id!r} not found")
    event_type = RoastEventType(event["type"])
    if event_type in ALWAYS_AUTO_EVENT_TYPES:
        raise RoastSessionError(f"{milestone_name(event_type)} is detected automatically -- it can't be edited")
    if event_type == RoastEventType.CUSTOM:
        raise RoastSessionError("Custom events aren't milestones -- nothing to edit")
    return event, event_type


def _check_milestone_order(events: list, event_type: "RoastEventType", time_s: float, verb: str) -> None:
    """A milestone has to sit strictly between whichever milestones before
    and after it in MILESTONE_SEQUENCE are already marked -- for moving
    one (verb "moved") and for adding one to a finished roast ("added")."""
    idx = MILESTONE_SEQUENCE.index(event_type)
    by_type = {RoastEventType(e["type"]): e for e in events if e["type"] != RoastEventType.CUSTOM.value}
    for earlier_type in reversed(MILESTONE_SEQUENCE[:idx]):
        if earlier_type in by_type:
            if time_s <= by_type[earlier_type]["time_s"]:
                raise RoastSessionError(f"{milestone_name(event_type)} can't be {verb} before {milestone_name(earlier_type)}")
            break
    for later_type in MILESTONE_SEQUENCE[idx + 1:]:
        if later_type in by_type:
            if time_s >= by_type[later_type]["time_s"]:
                raise RoastSessionError(f"{milestone_name(event_type)} can't be {verb} past {milestone_name(later_type)}")
            break


def _add_milestone_at(events: list, profile: list, event_type: "RoastEventType", time_s: float) -> dict:
    """Adds a milestone that was never marked to a finished roast, at a
    chosen time -- e.g. FC Start that nobody clicked. Shared by a finished
    RoastSession and RoastSessionManager's cold-roast path, like
    _retime_milestone. Lands on the nearest sample, as a retime does,
    and is labelled the way the roast's .alog file names it, so it reads
    the same before and after a restart."""
    if event_type == RoastEventType.CUSTOM:
        raise RoastSessionError("Custom events aren't milestones -- nothing to add")
    if event_type in ALWAYS_AUTO_EVENT_TYPES:
        raise RoastSessionError(f"{milestone_name(event_type)} is detected automatically -- it can't be added by hand")
    if any(e["type"] == event_type.value for e in events):
        raise RoastSessionError(f"{milestone_name(event_type)} is already marked on this roast -- move it instead")
    if not profile:
        raise RoastSessionError("this roast has no recording to put a milestone on")
    if not (0.0 <= time_s <= profile[-1]["time_s"]):
        raise RoastSessionError(f"time_s {time_s} is outside this roast's recorded range (0-{profile[-1]['time_s']})")

    nearest = min(profile, key=lambda p: abs(p["time_s"] - time_s))
    _check_milestone_order(events, event_type, nearest["time_s"], "added")

    event = {
        "id": str(uuid.uuid4()),
        "time_s": nearest["time_s"],
        "type": event_type.value,
        "label": ALOG_MILESTONE_LABELS[event_type.value],
        "value": nearest.get("bt"),
    }
    events.append(event)
    return event


def _retime_milestone(events: list, profile: list, event_id: str, new_time_s: float) -> dict:
    """Validates and applies a milestone retime in place (bounds-checks
    against the roast's own recorded range and against whichever
    chronologically-adjacent milestones are already marked, then
    recomputes `value` from the profile at the new time) -- shared for
    the same reason as _find_editable_milestone above."""
    event, event_type = _find_editable_milestone(events, event_id)

    if profile and not (0.0 <= new_time_s <= profile[-1]["time_s"]):
        raise RoastSessionError(f"time_s {new_time_s} is outside this roast's recorded range (0-{profile[-1]['time_s']})")

    # A milestone can only sit on a sample -- that's all the .alog file can
    # store (see RoastSession._match_events_to_alog) -- so snap to the
    # nearest one first, and check the order against where it will
    # actually land.
    nearest = min(profile, key=lambda p: abs(p["time_s"] - new_time_s)) if profile else None
    if nearest is not None:
        new_time_s = nearest["time_s"]

    _check_milestone_order(events, event_type, new_time_s, "moved")

    event["time_s"] = new_time_s
    event["value"] = nearest.get("bt") if nearest is not None else None
    return event


class RoastSession:
    def __init__(self, roast_id: str, request: RoastCreateRequest, created_by_username: Optional[str] = None):
        self.id = roast_id
        self.created_by_username = created_by_username
        self.title = request.title
        self.mode = request.mode
        self.beans = request.beans
        self.bean_id = request.bean_id
        saved = storage.get_bean(self.bean_id) if self.bean_id else storage.find_bean_by_name(self.beans)
        if saved is None:
            self.bean_id = None  # an unknown id, or a name that isn't a saved one yet
        else:
            self.bean_id = saved["id"]
            self.beans = saved["name"]  # the saved spelling, so roasts of the same beans group together
        self.tags = list(request.tags or [])
        self.weight_green_g = request.weight_green_g
        # Exposed via summary()/to_roast() so a client that only has the
        # roast id (e.g. LiveRoastView's reconnectActiveRoast, reattaching
        # after a page refresh) can recover the thresholds this roast was
        # actually configured with -- these previously only lived inside
        # the engine's own private state (ModbusEngine._dry_end_c etc.),
        # invisible to any API response, so a refresh silently fell back
        # to the form's mount-time defaults instead of the roast's real
        # configured values.
        self.auto_detect_milestones = request.auto_detect_milestones
        self.dry_end_c = request.dry_end_c
        self.fc_start_c = request.fc_start_c
        self.weight_roasted_g: Optional[float] = None
        self.sample_interval_s = max(0.1, request.sample_interval_s)
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.status = RoastStatus.IDLE
        self.duration_s: Optional[float] = None
        self.alog_path = storage.alog_path_for(roast_id)

        self.profile: list[dict] = []
        self.events: list[dict] = []
        self.notes: list[dict] = []

        self.source_alog_path: Optional[str] = None
        self.playback_speed: Optional[float] = None
        if request.mode == RoastMode.ALOG_PLAYBACK:
            self.source_alog_path = request.alog_path
            self.playback_speed = request.playback_speed

        # modbus_live only -- what this roast was actually connected with,
        # so it's visible later (live view, History detail, server log)
        # instead of only living in the Configure Roast form's own
        # transient state. modbus_device_profile_name is resolved once
        # here (not just the id) so it still reads correctly even if that
        # profile is later renamed or deleted -- same "freeze what was
        # configured" reasoning as source_alog_path/auto_detect_milestones
        # above, not a live pointer.
        self.modbus_transport: Optional[str] = None
        self.modbus_port: Optional[str] = None
        self.modbus_host: Optional[str] = None
        self.modbus_tcp_port: Optional[int] = None
        self.modbus_device_profile_name: Optional[str] = None
        if request.mode == RoastMode.MODBUS_LIVE:
            self.modbus_transport = request.modbus_transport
            self.modbus_port = request.modbus_port
            self.modbus_host = request.modbus_host
            self.modbus_tcp_port = request.modbus_tcp_port

        # ms6514_live only -- same reasoning as the modbus_* fields above.
        self.ms6514_port: Optional[str] = None
        if request.mode == RoastMode.MS6514_LIVE:
            self.ms6514_port = request.ms6514_port

        # aillio_live only -- same reasoning again.
        self.aillio_model: Optional[str] = None
        if request.mode == RoastMode.AILLIO_LIVE:
            self.aillio_model = request.aillio_model

        # tc4_live only -- same reasoning again.
        self.tc4_port: Optional[str] = None
        if request.mode == RoastMode.TC4_LIVE:
            self.tc4_port = request.tc4_port

        # A simulated device (a "sim://..." port or host) is started here and stopped
        # with the engine -- see _start_sim below.
        self._sim: Optional[simulated_devices.SimHandle] = None
        self._sim_value: Optional[str] = None

        # Anything that goes wrong while building the engine must not leave a
        # simulated device (see hardware_fakes/sim.py) running behind it.
        try:
            if request.mode == RoastMode.SIMULATOR:
                engine = SimulatorEngine()
            elif request.mode == RoastMode.ALOG_PLAYBACK:
                if not request.alog_path:
                    raise RoastSessionError("Missing .alog file path -- enter the path to an .alog file on the server (Device tab, \".alog file path\") before connecting.")
                engine = AlogPlayer(request.alog_path, speed=request.playback_speed)
            elif request.mode == RoastMode.MODBUS_LIVE:
                is_tcp = request.modbus_transport == "tcp"
                modbus_port, modbus_host, modbus_tcp_port = request.modbus_port, request.modbus_host, request.modbus_tcp_port
                modbus_control_port = request.modbus_control_port
                if is_tcp and simulated_devices.is_simulated(modbus_host):
                    handle = self._start_sim(modbus_host, RoastMode.MODBUS_LIVE)
                    modbus_host, modbus_tcp_port = handle.host, handle.port
                elif not is_tcp and simulated_devices.is_simulated(modbus_port):
                    handle = self._start_sim(modbus_port, RoastMode.MODBUS_LIVE)
                    modbus_port, modbus_control_port = handle.serial_url, None
                if is_tcp:
                    if not request.modbus_host:
                        raise RoastSessionError("Missing host/IP address -- enter the roaster's IP address (Device tab, \"Host / IP address\") before connecting.")
                elif not request.modbus_port:
                    raise RoastSessionError("Missing serial port -- enter the COM port (Windows, e.g. COM5) or /dev/tty... path (Linux/macOS) your roaster is connected on (Device tab, \"Serial port\") before connecting.")
                try:
                    if request.modbus_device_profile_id:
                        # Profile-driven path -- see ModbusEngine.from_profile
                        # and DeviceProfile in models.py. Takes over the whole
                        # register map *and* connection framing (baudrate/
                        # bytesize/parity/stopbits all come from the profile
                        # itself, not modbus_baudrate -- that field always has
                        # a concrete default (19200) even when the operator
                        # never touched it, so there's no reliable way to tell
                        # "explicitly overridden" from "just the form default"
                        # the way the other Optional[...] fields below can).
                        # control_port still applies -- that's about which
                        # physical wire, not the profile's own protocol
                        # details. The 26 flat modbus_* register-map fields
                        # are simply not consulted when this is set (no
                        # merging between the two -- see
                        # RoastCreateRequest.modbus_device_profile_id's own
                        # docstring for why that's the deliberate choice).
                        profile_row = storage.get_device_profile_row(request.modbus_device_profile_id)
                        if profile_row is None:
                            raise RoastSessionError(f"device profile {request.modbus_device_profile_id!r} not found")
                        profile = DeviceProfile.from_row(profile_row)
                        self.modbus_device_profile_name = profile.name
                        engine = ModbusEngine.from_profile(
                            profile,
                            modbus_port,
                            transport=request.modbus_transport,
                            host=modbus_host,
                            tcp_port=modbus_tcp_port,
                            control_port=modbus_control_port,
                            dry_end_c=request.dry_end_c,
                            fc_start_c=request.fc_start_c,
                            detect_milestones=request.auto_detect_milestones,
                            client_cls=ModbusTcpClient if is_tcp else ModbusSerialClient,
                        )
                    elif is_tcp:
                        # No sensible flat-register-override default exists for
                        # TCP the way RTU's FZ-94 defaults do -- and nothing in
                        # the Configure Roast form can produce this combination
                        # (the Ethernet Data Source option always pairs with a
                        # device profile). Guarded explicitly rather than
                        # silently falling through to the RTU-shaped flat
                        # constructor below.
                        raise RoastSessionError("Ethernet Modbus needs a Device Profile -- pick one from the \"Device profile\" dropdown (Device tab); there's no flat register-override fallback for Ethernet the way USB has.")
                    else:
                        engine = ModbusEngine(
                            modbus_port,
                            baudrate=request.modbus_baudrate,
                            control_port=modbus_control_port,
                            control_baudrate=request.modbus_control_baudrate,
                            dry_end_c=request.dry_end_c,
                            fc_start_c=request.fc_start_c,
                            detect_milestones=request.auto_detect_milestones,
                            **_modbus_register_overrides(request),
                        )
                except ValueError as exc:
                    raise RoastSessionError(str(exc)) from exc
            elif request.mode == RoastMode.MS6514_LIVE:
                if not request.ms6514_port:
                    raise RoastSessionError("Missing serial port -- enter the COM port (Windows, e.g. COM5) or /dev/tty... path (Linux/macOS) the MS6514 meter is connected on (Device tab, \"Serial port\") before connecting.")
                ms6514_port = request.ms6514_port
                if simulated_devices.is_simulated(ms6514_port):
                    ms6514_port = self._start_sim(ms6514_port, RoastMode.MS6514_LIVE).serial_url
                try:
                    engine = MS6514Engine(
                        ms6514_port,
                        dry_end_c=request.dry_end_c,
                        fc_start_c=request.fc_start_c,
                        detect_milestones=request.auto_detect_milestones,
                    )
                except ValueError as exc:
                    raise RoastSessionError(str(exc)) from exc
            elif request.mode == RoastMode.AILLIO_LIVE:
                if not request.aillio_model:
                    raise RoastSessionError("Missing Aillio model -- select which Bullet model (Device tab, \"Model\") before connecting.")
                try:
                    engine = AillioEngine(
                        request.aillio_model,
                        dry_end_c=request.dry_end_c,
                        fc_start_c=request.fc_start_c,
                        detect_milestones=request.auto_detect_milestones,
                    )
                except ValueError as exc:
                    raise RoastSessionError(str(exc)) from exc
            elif request.mode == RoastMode.TC4_LIVE:
                if not request.tc4_port:
                    raise RoastSessionError("Missing serial port -- enter the COM port (Windows, e.g. COM5) or /dev/tty... path (Linux/macOS) the TC4+ shield is connected on (Device tab, \"Serial port\") before connecting.")
                tc4_port = request.tc4_port
                if simulated_devices.is_simulated(tc4_port):
                    tc4_port = self._start_sim(tc4_port, RoastMode.TC4_LIVE).serial_url
                try:
                    engine = TC4Engine(
                        tc4_port,
                        dry_end_c=request.dry_end_c,
                        fc_start_c=request.fc_start_c,
                        detect_milestones=request.auto_detect_milestones,
                    )
                except ValueError as exc:
                    raise RoastSessionError(str(exc)) from exc
            else:  # pragma: no cover - guarded by enum
                raise RoastSessionError(f"unsupported mode {request.mode}")
        except BaseException:
            self._stop_sim()
            raise

        self._engine = engine
        if request.mode == RoastMode.MODBUS_LIVE:
            connection = (
                f"host={self.modbus_host}:{self.modbus_tcp_port}"
                if self.modbus_transport == "tcp"
                else f"port={self.modbus_port}"
            )
            status = engine.status()
            logger.info(
                "roast %s connecting: modbus_live transport=%s %s profile=%s connected=%s%s",
                self.id, self.modbus_transport, connection,
                self.modbus_device_profile_name or "(custom/flat registers)",
                status.get("connected"),
                f" error={status.get('last_error')}" if not status.get("connected") else "",
            )
        elif request.mode == RoastMode.MS6514_LIVE:
            status = engine.status()
            logger.info(
                "roast %s connecting: ms6514_live port=%s connected=%s%s",
                self.id, self.ms6514_port, status.get("connected"),
                f" error={status.get('last_error')}" if not status.get("connected") else "",
            )
        elif request.mode == RoastMode.AILLIO_LIVE:
            status = engine.status()
            logger.info(
                "roast %s connecting: aillio_live model=%s connected=%s%s",
                self.id, self.aillio_model, status.get("connected"),
                f" error={status.get('last_error')}" if not status.get("connected") else "",
            )
        elif request.mode == RoastMode.TC4_LIVE:
            status = engine.status()
            logger.info(
                "roast %s connecting: tc4_live port=%s connected=%s%s",
                self.id, self.tc4_port, status.get("connected"),
                f" error={status.get('last_error')}" if not status.get("connected") else "",
            )
        # Exposed via summary() so the frontend's vertical control panel
        # can convert heater_pct <-> burner_sv_c locally while dragging
        # (optimistic preview -- see ModbusControlChannel.sv_range_c),
        # instead of waiting on a telemetry round-trip to see the other
        # slider move. None for every mode without an SV_TEMPERATURE
        # control channel (only modbus_live can have one).
        self.burner_sv_range_c: Optional[tuple[float, float]] = None
        for ch in getattr(engine, "control_channels", []):
            if ch.kind == ModbusControlKind.SV_TEMPERATURE:
                self.burner_sv_range_c = ch.sv_range_c
                break
        self.device = MockDevice(device_id=roast_id, engine=engine)
        self.control = RoastControl(self)
        self._task: Optional[asyncio.Task] = None
        self._stop_requested = asyncio.Event()
        # modbus_live only in practice (see add_event's own mode check --
        # ms6514_live has no write capability to bind an action to), but
        # stored unconditionally same as everything else on the request.
        self._alarm_rules: list[AlarmRule] = list(request.alarms)
        self._pending_alarm_tasks: list[asyncio.Task] = []
        # TEMPERATURE/TIME rules are evaluated every tick (see
        # _evaluate_ambient_alarms) -- this is what keeps a rule from
        # re-scheduling itself on every single tick for as long as its
        # condition stays true (e.g. BT staying above threshold for the
        # rest of the roast). EVENT rules need no equivalent tracking --
        # add_event()'s own milestone-sequencing already guarantees each
        # milestone fires at most once per roast.
        self._fired_ambient_rule_ids: set[str] = set()
        # True once this session has an actual DB roast row (set by
        # _persist_new_roast_row(), called from start()/begin_recording()) --
        # modbus_live/ms6514_live can now sit connected+streaming for a
        # while (see connect()) before that ever happens, e.g. while a real
        # connection is only being verified. Everything that assumes a DB
        # row exists (writing an .alog, storage.update_roast, ...) needs to
        # check this first instead of assuming status != IDLE means recorded.
        self._recorded = False

    # -- lifecycle -----------------------------------------------------
    def _start_sim(self, value: str, mode: RoastMode) -> simulated_devices.SimHandle:
        """Starts the simulated device a ``sim://...`` port or host names, and
        tags this roast ``simulated`` so History can tell it from a real one."""
        try:
            kind = simulated_devices.kind_of(value)
        except ValueError as exc:
            raise RoastSessionError(str(exc)) from exc
        if kind.mode != mode.value:
            raise RoastSessionError(f"{kind.label} can't be used with this data source -- pick a simulated device for it, or a real port.")
        self._sim = simulated_devices.start(value)
        self._sim_value = value
        if "simulated" not in self.tags:
            self.tags.append("simulated")
        return self._sim

    def _stop_sim(self) -> None:
        if self._sim is not None:
            self._sim.stop()
            self._sim = None

    def _close_engine(self) -> None:
        """Closes the live engine and, if there is one, the simulated device behind it."""
        try:
            self._engine.close()
        finally:
            self._stop_sim()

    def _persist_new_roast_row(self) -> None:
        if self.beans and not self.bean_id:
            # A name typed on the roast joins the Beans list.
            created = storage.ensure_bean(self.beans)
            if created:
                self.bean_id, self.beans = created["id"], created["name"]
        storage.insert_roast({
            "id": self.id,
            "title": self.title,
            "mode": self.mode.value,
            "status": self.status.value,
            "created_at": self.created_at,
            "beans": self.beans,
            "bean_id": self.bean_id,
            "weight_green_g": self.weight_green_g,
            "weight_roasted_g": None,
            "duration_s": None,
            "alog_path": None,
            "source_alog_path": self.source_alog_path,
            "playback_speed": self.playback_speed,
            "created_by_username": self.created_by_username,
            "modbus_transport": self.modbus_transport,
            "modbus_port": self.modbus_port,
            "modbus_host": self.modbus_host,
            "modbus_tcp_port": self.modbus_tcp_port,
            "modbus_device_profile_name": self.modbus_device_profile_name,
            "ms6514_port": self.ms6514_port,
            "aillio_model": self.aillio_model,
            "tc4_port": self.tc4_port,
        })
        if self.tags:
            storage.set_roast_tags(self.id, self.tags)
        self._recorded = True

    async def start(self) -> None:
        """simulator/alog_playback only -- connect, start recording, and
        create the DB row all in one atomic step, same as every mode used
        to work before modbus_live/ms6514_live got the connect()/
        begin_recording() split below. Neither engine tolerates sitting
        "connected but idle" for any length of time before recording
        starts: SimulatorEngine.start() (below) fires CHARGE and resets its
        clock synchronously, and AlogPlayer's clock/event pointer starts
        advancing on its very first tick() with no way to pause it -- so for
        these two modes, "connect" and "begin recording" have to stay the
        same moment."""
        self._persist_new_roast_row()
        await asyncio.to_thread(self.device.connect)
        if isinstance(self._engine, SimulatorEngine):
            self._engine.start()
        self.status = RoastStatus.ROASTING
        storage.update_roast(self.id, status=self.status.value)
        self._task = asyncio.create_task(self._run_loop())

    async def connect(self) -> None:
        """modbus_live/ms6514_live only -- the ON action. Opens the device
        and starts the read loop immediately (status stays IDLE, so
        _run_loop() below runs it in preview mode: samples stream out live
        but nothing is recorded), without creating a DB row or touching
        storage at all. See begin_recording() for the separate START
        action that actually starts recording what's already flowing."""
        await asyncio.to_thread(self.device.connect)
        self._task = asyncio.create_task(self._run_loop())

    async def begin_recording(self) -> None:
        """modbus_live/ms6514_live only -- the START action, once already
        connect()ed. Flips status to ROASTING; _run_loop() (already
        running) picks that up on its own next iteration and starts
        actually persisting samples from this point forward. Discards
        whatever the milestone detector observed during the preview
        window first -- see reset_detection()'s own docstring for why
        that's required, not optional."""
        self._persist_new_roast_row()
        self.status = RoastStatus.ROASTING
        storage.update_roast(self.id, status=self.status.value)
        if hasattr(self._engine, "reset_detection"):
            self._engine.reset_detection()
        if self._sim is not None:
            # A simulated device waits at Charge while merely connected; its
            # roast begins now, so the recording starts at Charge, not partway in.
            self._sim.begin_roast()

    async def _run_loop(self) -> None:
        try:
            while not self._stop_requested.is_set():
                # `speed` (only meaningful for AlogPlayer) multiplies the dt we
                # hand to engine.tick(). We divide the tick step by it here so
                # every recorded sample still advances roast-time by exactly
                # sample_interval_s regardless of speed -- speed only changes
                # how fast that roast-time elapses in real (wall-clock) time,
                # not how densely the profile gets sampled. Without this, a
                # faster speed silently coarsened the recorded/charted profile.
                speed = getattr(self._engine, "speed", 1.0)
                if speed is not None and speed <= 0:
                    await asyncio.sleep(min(self.sample_interval_s, 0.5))
                    continue
                tick_dt = self.sample_interval_s / speed if speed else self.sample_interval_s

                # Snapshotted once, before the blocking read -- begin_recording()
                # can flip self.status concurrently while this iteration's
                # device.read() is still in flight (it runs in a worker thread
                # via to_thread). Using a single snapshot for the whole
                # iteration, instead of re-reading self.status further down,
                # is what keeps that iteration entirely on one side of the
                # preview/recording line rather than torn across both.
                status_snapshot = self.status
                sample = await asyncio.to_thread(self.device.read, tick_dt)
                events = sample.pop("events", [])
                finished = sample.pop("finished", False)

                if status_snapshot == RoastStatus.IDLE:
                    # Preview mode (armed, not yet recording) -- publish the
                    # live reading for the UI's meters, but never append to
                    # self.profile/self.events and never touch storage. Any
                    # milestone events the detector fired off `events` here
                    # are deliberately dropped, not queued -- see
                    # reset_detection()'s docstring for why that's safe.
                    await pubsub.publish(self.id, {
                        "type": "preview",
                        "roast_id": self.id,
                        "sample": sample,
                    })
                    await self.control.tick(sample, recording=False)
                else:
                    self.profile.append(sample)
                    self.events.extend(events)
                    self._evaluate_ambient_alarms(sample)
                    await self.control.tick(sample, recording=True)

                    await pubsub.publish(self.id, {
                        "type": "sample",
                        "roast_id": self.id,
                        "sample": sample,
                        "events": events,
                        # Without this, the frontend's roast.status only ever
                        # gets set once, from the WS "snapshot" message sent
                        # right when the connection opens -- for modbus_live/
                        # ms6514_live that's always IDLE (connect() happens
                        # before START), and nothing else ever updated it
                        # afterward. Real bug found live: after START, the UI
                        # kept showing "idle" forever (elapsed time stuck at
                        # 0:00, every milestone button permanently disabled)
                        # even though the backend was genuinely ROASTING and
                        # profile was genuinely filling in -- the chart (which
                        # reads roast.profile directly) looked correct while
                        # everything gated on roast.status was stuck.
                        "status": status_snapshot.value,
                    })

                    if finished:
                        await self._finish(RoastStatus.COMPLETE)
                        break

                await asyncio.sleep(tick_dt)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            # Heater off before anything else: the app is about to stop
            # looking at this roaster.
            await self.control.enter_safe_state("the roast stopped because of an error")
            self.status = RoastStatus.ABORTED
            self._cancel_pending_alarms()
            if self._recorded:
                reached_drop = any(e["type"] == RoastEventType.DROP.value for e in self.events)
                storage.update_roast(self.id, status=self.status.value, reached_drop=reached_drop)
            await pubsub.publish(self.id, {"type": "error", "roast_id": self.id, "message": str(exc)})

    def _cancel_pending_alarms(self) -> None:
        """Without this, a delayed automation (e.g. "on DROP, wait 60s,
        then burner off") could still fire after OFF/STOP has already
        disconnected the device, or after a real error ended the roast --
        writing to a connection that's already gone, or just plain
        surprising the operator well after they thought everything had
        stopped."""
        for task in self._pending_alarm_tasks:
            task.cancel()

    async def abort(self) -> None:
        """Despite the name (kept for API/method-name stability), this is
        the operator's deliberate OFF/stop action, not an error -- it
        finishes the roast as STOPPED. Real failures (an exception in the
        read/tick loop, above) are what actually produce ABORTED."""
        self._stop_requested.set()
        self._cancel_pending_alarms()
        if self._task is not None and self._task is not asyncio.current_task():
            await self._task
        if self.status not in (RoastStatus.COMPLETE, RoastStatus.STOPPED, RoastStatus.ABORTED):
            # to_thread, not a direct call -- release() can write to the
            # real device (heater off) if one's still on, and this runs on
            # the shared event loop -- see RoastControl.enter_safe_state's
            # own comment on why a blocking device write here freezes the
            # whole server, not just this one roast.
            await asyncio.to_thread(self.control.release)
            if self._recorded:
                await self._finish(RoastStatus.STOPPED)
            else:
                # OFF while merely connected/previewing (see connect()) --
                # never became a real roast, so there's no DB row, no
                # profile worth an .alog file, nothing to persist. Still
                # has to release the real serial port and tell any open
                # WebSocket the session is gone (RoastSessionManager.abort()
                # is what actually drops it from .sessions, once this
                # returns).
                await self._finish_unrecorded()

    async def _finish_unrecorded(self) -> None:
        self._stop_requested.set()
        self.status = RoastStatus.IDLE  # back to idle, not "stopped" -- nothing was ever actually recording
        await asyncio.to_thread(self.device.disconnect)
        if isinstance(self._engine, (ModbusEngine, MS6514Engine, TC4Engine)):
            self._close_engine()  # release the serial port
        await pubsub.publish(self.id, {"type": "finished", "roast_id": self.id, "status": self.status.value})

    async def _finish(self, status: RoastStatus) -> None:
        self._stop_requested.set()
        self.status = status
        self.control.stop_automation("the roast finished")
        await asyncio.to_thread(self.device.disconnect)
        if isinstance(self._engine, (ModbusEngine, MS6514Engine, TC4Engine)):
            self._close_engine()  # release the serial port

        # Written in the native .alog shape (Python-literal syntax
        # + timeindex/computed/specialevents), not a bespoke JSON one --
        # this app used to write its own minimal JSON format, but that
        # could only round-trip through this app's own reader, not
        # open in other software. See roast_to_native_alog_dict's
        # docstring for the full rationale.
        alog_dict = roast_to_native_alog_dict(
            title=self.title,
            profile=self.profile,
            events=self.events,
            notes=self.notes,
            beans=self.beans,
            weight_green_g=self.weight_green_g,
            weight_roasted_g=self.weight_roasted_g,
            roastdate=self.created_at,
        )
        save_native_alog(self.alog_path, alog_dict)
        self._match_events_to_alog()
        self.duration_s = roast_duration_s(self.profile, self.events)

        # Whether Drop ever got marked before this roast ended -- a
        # persistent, post-hoc "ended before all milestones" signal for
        # History/roast detail. Explicit True/False from here on, not just
        # the NULL every roast finished before this column existed stays
        # at (see storage.py's own migration comment on why that matters).
        reached_drop = any(e["type"] == RoastEventType.DROP.value for e in self.events)
        storage.update_roast(
            self.id,
            status=self.status.value,
            duration_s=self.duration_s,
            weight_roasted_g=self.weight_roasted_g,
            alog_path=self.alog_path,
            playback_speed=self.playback_speed,
            reached_drop=reached_drop,
        )
        await pubsub.publish(self.id, {"type": "finished", "roast_id": self.id, "status": self.status.value})

    # -- interaction ----------------------------------------------------
    def apply_command(self, command: ControlCommand, *, source: str = "operator") -> dict:
        # IDLE is included alongside ROASTING/COOLING -- for modbus_live/
        # ms6514_live specifically, it means "connected via connect(), not
        # yet recording" (see that method), not "no session at all" (which
        # isn't a status value, it's simply not having a session/roast_id
        # to call this on in the first place). Lets Air/Drum/Burner be
        # exercised -- Testing Mode's write check, or just manually -- while
        # merely armed (controls work before recording starts).
        if self.status not in (RoastStatus.IDLE, RoastStatus.ROASTING, RoastStatus.COOLING):
            raise RoastSessionError(f"roast {self.id} is not active (status={self.status.value})")
        payload = command.model_dump(exclude_none=True)
        if "speed" in payload:
            self.playback_speed = payload["speed"]
        try:
            if source == "operator":
                return self.control.operator_command(payload)
            return self.control.write(payload, source=source)
        except RoastControlError as exc:
            raise RoastSessionError(str(exc)) from exc

    def add_note(self, req: NoteCreateRequest) -> dict:
        note = {
            "time_s": round_note_time(self.profile[-1]["time_s"] if self.profile else 0.0),
            "text": _clean_note_text(req.text),
            "author": req.author,
            "created_at": note_timestamp(),
        }
        self.notes.append(note)
        assign_note_ids(self.notes)
        # notes_text is kept in sync here too, not just in _rewrite_alog --
        # unlike the .alog file (no-op until the roast finishes, see below),
        # search needs to find a note the moment it's added, mid-roast or not.
        storage.update_roast(self.id, notes_text=notes_search_text(self.notes))
        # A no-op while the roast is still recording (no .alog yet -- it's
        # written once at the end); after that it keeps the file in step.
        self._rewrite_alog()
        return note

    def update_note(self, note_id: str, text: str) -> dict:
        note = _find_note(self.notes, note_id)
        note["text"] = _clean_note_text(text)
        assign_note_ids(self.notes)
        storage.update_roast(self.id, notes_text=notes_search_text(self.notes))
        self._rewrite_alog()
        return note

    def delete_note(self, note_id: str) -> None:
        self.notes.remove(_find_note(self.notes, note_id))
        assign_note_ids(self.notes)
        storage.update_roast(self.id, notes_text=notes_search_text(self.notes))
        self._rewrite_alog()

    def add_event(self, req: EventCreateRequest) -> dict:
        if req.type in MILESTONE_SEQUENCE:
            # Guards against marking a milestone while merely connected/
            # previewing (status IDLE -- see connect()), not yet recording.
            # Matters now that CHARGE itself can be manual (real hardware,
            # detect_milestones=False): profile is empty during preview,
            # so a click there would get time_s=0.0 and then silently
            # survive into the real recording once begin_recording() flips
            # status, landing as a phantom event at the very start of the
            # timeline instead of being rejected outright.
            if self.status not in (RoastStatus.ROASTING, RoastStatus.COOLING):
                raise RoastSessionError(f"roast {self.id} isn't recording yet (status={self.status.value}) -- press START first")
            if req.type in ALWAYS_AUTO_EVENT_TYPES:
                raise RoastSessionError(f"{milestone_name(req.type)} is detected automatically -- it can't be marked by hand")
            existing_types = {RoastEventType(e["type"]) for e in self.events if e["type"] != RoastEventType.CUSTOM.value}
            if req.type in existing_types:
                raise RoastSessionError(f"{req.type.value} has already been marked for this roast")
            idx = MILESTONE_SEQUENCE.index(req.type)
            later_types = set(MILESTONE_SEQUENCE[idx + 1:])
            if existing_types & later_types:
                raise RoastSessionError(f"can't mark {req.type.value} -- a later milestone is already recorded")
        event = {
            "id": str(uuid.uuid4()),
            "time_s": self.profile[-1]["time_s"] if self.profile else 0.0,
            "type": req.type.value,
            "label": req.label,
            "value": req.value,
        }
        self.events.append(event)
        if req.type == RoastEventType.CHARGE:
            # Turning Point stays auto-detected even when CHARGE itself is
            # a manual click -- confirmed against a real FZ-94 roast,
            # where Turning Point is auto-plotted on the chart despite
            # every other milestone being marked by hand. Both live-hardware
            # engines expose this; simulator/alog_playback don't (and
            # don't need to -- CHARGE is never manual there).
            #
            # charge_bt prefers the client-supplied value but falls back to
            # this session's own last-known BT reading -- the frontend
            # sends whatever its own local `latest` websocket state
            # happens to hold at click time, which can still be null if
            # the very first live sample hasn't reached it yet even
            # though the backend's own profile already has one (a real
            # race, not hypothetical: clicking CHARGE right as a roast
            # starts is the normal workflow, not an edge case). Without
            # this fallback, that race permanently and silently disables
            # Turning Point detection for the rest of the roast.
            charge_bt = event["value"] if event["value"] is not None else (self.profile[-1]["bt"] if self.profile else None)
            if charge_bt is not None:
                notify = getattr(self._engine, "notify_manual_charge", None)
                if notify is not None:
                    notify(event["time_s"], charge_bt)
        elif req.type in (RoastEventType.DRY_END, RoastEventType.FC_START):
            # Only matters when auto_detect_milestones is on (opt-in) --
            # tells the detector this was already marked, so it doesn't
            # also independently fire its own copy once BT crosses the
            # configured threshold. Without this, that auto-fired copy
            # would land as a genuine duplicate: it merges straight into
            # self.events via _run_loop's get_new_events(), bypassing this
            # method's own "already marked" check entirely (see
            # MILESTONE_SEQUENCE handling above). Harmless no-op when
            # auto-detection is off -- the detector would never have fired
            # this on its own anyway.
            mark_fired = getattr(self._engine, "mark_milestone_fired", None)
            if mark_fired is not None:
                mark_fired(req.type.value)
        if self.mode == RoastMode.MODBUS_LIVE:
            for rule in self._alarm_rules:
                if rule.enabled and rule.trigger_kind == AlarmTriggerKind.EVENT and rule.event_type == req.type:
                    self._schedule_rule(rule)
        return event

    def _evaluate_ambient_alarms(self, sample: dict) -> None:
        """TEMPERATURE/TIME rules -- checked every tick while actually
        recording (see _run_loop's call site, ROASTING/COOLING only,
        never during the armed/preview window). One-shot per roast via
        _fired_ambient_rule_ids -- without it a rule would reschedule
        itself on every single tick for as long as its condition stays
        true (e.g. BT sitting above threshold for the rest of the
        roast)."""
        if self.mode != RoastMode.MODBUS_LIVE:
            return
        for rule in self._alarm_rules:
            if not rule.enabled or rule.trigger_kind == AlarmTriggerKind.EVENT or rule.id in self._fired_ambient_rule_ids:
                continue
            crossed = False
            if rule.trigger_kind == AlarmTriggerKind.TEMPERATURE and rule.threshold_c is not None:
                value = sample.get(rule.channel)
                crossed = value is not None and value >= rule.threshold_c
            elif rule.trigger_kind == AlarmTriggerKind.TIME and rule.at_time_s is not None:
                crossed = sample.get("time_s", 0.0) >= rule.at_time_s
            if crossed:
                self._fired_ambient_rule_ids.add(rule.id)
                self._schedule_rule(rule)

    def _trigger_label(self, rule: AlarmRule) -> str:
        if rule.trigger_kind == AlarmTriggerKind.TEMPERATURE:
            return f"{(rule.channel or '?').upper()}>={rule.threshold_c}°C"
        if rule.trigger_kind == AlarmTriggerKind.TIME:
            return f"t>={rule.at_time_s}s"
        return rule.event_type.value if rule.event_type else "?"

    def _schedule_rule(self, rule: AlarmRule) -> None:
        task = asyncio.create_task(self._fire_rule(rule))
        self._pending_alarm_tasks.append(task)

    def _fire_alarm_command(self, rule: AlarmRule) -> None:
        """Applies a bound automation's command. Deliberately swallows any
        failure (a device write error, or the roast having already ended
        by the time a delayed rule's timer elapses) -- the milestone this
        was bound to was already successfully recorded by the time this
        runs, so a failed automation must never turn a successful
        add_event() call into an error for the caller. Errors just don't
        get applied; nothing else reports on them today (no pubsub
        "alarm_failed" message -- the Controls panel already reflects
        live device state every tick, so a failed write is visible there
        as "didn't change" rather than needing a separate channel)."""
        command = ControlCommand(heater_pct=rule.heater_pct, fan_pct=rule.fan_pct, drum_speed_pct=rule.drum_speed_pct)
        try:
            self.apply_command(command, source="rule")
        except RoastSessionError:
            pass

    def _fire_milestone_mark(self, rule: AlarmRule) -> Optional[dict]:
        """Lets a rule chain one milestone into auto-marking another --
        e.g. "30s after Turning Point, mark FC End" -- by calling the
        real add_event() internally, the exact same path a manual button
        click uses. Inherits every existing safety check for free
        (sequencing, no re-marking, notify_manual_charge/
        mark_milestone_fired bookkeeping) instead of needing its own
        copy of any of it. Swallows RoastSessionError the same way
        _fire_alarm_command does -- e.g. the target is already marked,
        or out of sequence relative to what's happened since this rule
        was configured -- a failed auto-mark must never crash the firing
        task. Returns the created event (for the caller to publish) or
        None if it was swallowed."""
        label = MILESTONE_LABELS.get(rule.mark_milestone, rule.mark_milestone.value if rule.mark_milestone else "")
        value = self.profile[-1]["bt"] if self.profile else None
        try:
            return self.add_event(EventCreateRequest(type=rule.mark_milestone, label=label, value=value))
        except RoastSessionError:
            return None

    async def _fire_rule(self, rule: AlarmRule) -> None:
        try:
            if rule.delay_s > 0:
                # Published before the sleep, not after -- so the frontend
                # shows "pending" for the actual full delay window, not
                # just however much of it is left once this task starts
                # running.
                await pubsub.publish(self.id, {
                    "type": "alarm_scheduled",
                    "roast_id": self.id,
                    "rule_id": rule.id,
                    "trigger": self._trigger_label(rule),
                    "delay_s": rule.delay_s,
                })
                await asyncio.sleep(rule.delay_s)
                if self._stop_requested.is_set():
                    # abort()/the _run_loop exception handler already
                    # cancel every pending task -- this is a defensive
                    # check for the race between the sleep above
                    # completing and that cancellation actually landing,
                    # not the primary guard.
                    return
            self._fire_alarm_command(rule)
            if rule.mark_milestone is not None:
                created = self._fire_milestone_mark(rule)
                if created is not None:
                    await pubsub.publish(self.id, {"type": "event", "roast_id": self.id, "event": created})
            # Always published, delay_s==0 included -- a rule's optional
            # `message` needs a way to reach the frontend regardless of
            # timing, so immediate rules are no longer silent the way
            # they were before notifications existed.
            await pubsub.publish(self.id, {"type": "alarm_fired", "roast_id": self.id, "rule_id": rule.id, "message": rule.message})
            # Autonomous -- no HTTP request in flight, so no username (same
            # as the fail-safe trips in control.py's enter_safe_state).
            storage.log_activity(
                "safety", "automation_rule_fired", platform=storage.AUTOMATIC_PLATFORM, roast_id=self.id, roast_title=self.title,
                message=f'Automation rule fired on "{self.title}" ({self._trigger_label(rule)})'
                + (f": {rule.message}" if rule.message else ""),
            )
        finally:
            # Needed on the normal-completion path too, not just when
            # abort()/the exception handler cancel it -- otherwise a long
            # roast with many delayed rules leaks a finished task
            # reference per rule forever.
            current = asyncio.current_task()
            if current in self._pending_alarm_tasks:
                self._pending_alarm_tasks.remove(current)

    def _rewrite_alog(self) -> None:
        """Re-serializes this session's current profile/events/notes/
        weights into its .alog file, if one already exists -- shared by
        every edit that can happen after _finish() already wrote it once
        (set_weight_roasted, delete_event, retime_event), so the exported
        file never silently drifts from what the app itself shows."""
        if self.alog_path and os.path.exists(self.alog_path):
            alog_dict = roast_to_native_alog_dict(
                title=self.title,
                profile=self.profile,
                events=self.events,
                notes=self.notes,
                beans=self.beans,
                weight_green_g=self.weight_green_g,
                weight_roasted_g=self.weight_roasted_g,
                roastdate=self.created_at,
            )
            save_native_alog(self.alog_path, alog_dict)
            self._match_events_to_alog()

    def _match_events_to_alog(self) -> None:
        """Makes this session's milestones exactly what its .alog file now
        holds. The file stores each milestone as an index into its samples,
        not as a time, so reading it back lands a milestone on the nearest
        sample and takes that sample's BT -- and a simulator or live bridge
        can fire Charge at 0.0 s, before its first sample exists, which the
        file can only store as that first sample (1.0 s). Without this, the
        roast showed one set of times until the server restarted and another
        after (read from the file), and its duration changed with them."""
        try:
            stored = alog_dict_to_points(load_alog(self.alog_path))["events"]
        except Exception:  # noqa: BLE001 -- the file was just written; leave the session as it is
            logger.exception("couldn't read back %s", self.alog_path)
            return
        by_type = {e["type"]: e for e in stored if e["type"] != RoastEventType.CUSTOM.value}
        for event in self.events:
            match = by_type.get(event["type"]) if event["type"] != RoastEventType.CUSTOM.value else None
            if match is not None:
                event["time_s"] = match["time_s"]
                event["value"] = match["value"]

    def _refresh_duration(self) -> None:
        """Moving or removing Charge/Drop changes the roast's duration --
        but only once it's finished and saved (a live roast's duration is
        set when it ends)."""
        if self.status in (RoastStatus.COMPLETE, RoastStatus.STOPPED, RoastStatus.ABORTED):
            self.duration_s = roast_duration_s(self.profile, self.events)
            storage.update_roast(self.id, duration_s=self.duration_s)

    def set_weight_roasted(self, grams: Optional[float]) -> None:
        # The natural workflow is: roast finishes, beans cool, *then* get
        # weighed -- by that point _finish() has already run and this
        # session's own in-memory state is the only thing DB reads/exports
        # would otherwise reflect. Without persisting here too, the weight
        # would silently vanish on a backend restart (still-running-process
        # reads/the AI review pick it up fine either way, since both go
        # through this same in-memory session first). grams=None clears
        # it (DELETE /roasts/{id}/weight) -- same method either way, no
        # separate delete path needed here.
        self.weight_roasted_g = grams
        storage.update_roast(self.id, weight_roasted_g=grams)
        self._rewrite_alog()

    def set_weight_green(self, grams: Optional[float]) -> None:
        # Green weight is usually entered up front (New Roast form), but a
        # typo, a forgotten scale, or a re-weigh after the fact should all
        # be fixable the same way weight_roasted_g already is -- see that
        # method's own comment, same reasoning applies here unchanged,
        # including grams=None clearing it.
        self.weight_green_g = grams
        storage.update_roast(self.id, weight_green_g=grams)
        self._rewrite_alog()

    def set_tags(self, tags: list[str]) -> None:
        # No _rewrite_alog() call here, unlike the weight setters -- tags
        # aren't part of the .alog format, so there's nothing to round-trip
        # into that file. storage.set_roast_tags is the only durable copy.
        self.tags = tags
        storage.set_roast_tags(self.id, tags)

    def delete_event(self, event_id: str) -> dict:
        """Removes an already-marked milestone entirely, so it can be
        re-marked fresh via the normal add_event() flow (its own
        already-marked check only looks at what's currently in
        self.events, so a deleted one is simply absent again). Doesn't
        rewind anything else -- deleting CHARGE, for instance, doesn't
        reset the elapsed-time clock or Turning Point detection state,
        it only removes this one event marker."""
        event, _ = _find_editable_milestone(self.events, event_id)
        self.events.remove(event)
        self._rewrite_alog()
        self._refresh_duration()
        return event

    def add_milestone_at(self, event_type: RoastEventType, time_s: float) -> dict:
        """Adds a never-marked milestone to this roast after it finished --
        see _add_milestone_at. While it's still recording, milestones come
        from the buttons (add_event), stamped at the current time."""
        if self.status not in (RoastStatus.COMPLETE, RoastStatus.STOPPED, RoastStatus.ABORTED):
            raise RoastSessionError("this roast is still recording -- mark milestones with the buttons")
        event = _add_milestone_at(self.events, self.profile, event_type, time_s)
        self._rewrite_alog()
        self._refresh_duration()
        return event

    def retime_event(self, event_id: str, new_time_s: float) -> dict:
        """Moves an already-marked milestone to a different point on the
        elapsed-time axis -- for correcting a click that landed too early
        or too late. See _retime_milestone for the actual validation/
        recompute logic, shared with RoastSessionManager's cold-roast
        path."""
        event = _retime_milestone(self.events, self.profile, event_id, new_time_s)
        self._rewrite_alog()
        self._refresh_duration()
        return event

    # -- serialization ----------------------------------------------------
    def summary(self) -> RoastSummary:
        return RoastSummary(
            id=self.id,
            title=self.title,
            mode=self.mode,
            status=self.status,
            created_at=self.created_at,
            beans=self.beans,
            bean_id=self.bean_id,
            tags=self.tags,
            weight_green_g=self.weight_green_g,
            weight_roasted_g=self.weight_roasted_g,
            duration_s=self.duration_s,
            alog_path=self.alog_path if self.status in (RoastStatus.COMPLETE, RoastStatus.STOPPED, RoastStatus.ABORTED) else None,
            created_by_username=self.created_by_username,
            source_alog_path=self.source_alog_path,
            playback_speed=self.playback_speed,
            auto_detect_milestones=self.auto_detect_milestones,
            dry_end_c=self.dry_end_c,
            fc_start_c=self.fc_start_c,
            burner_sv_range_c=self.burner_sv_range_c,
            modbus_transport=self.modbus_transport,
            modbus_port=self.modbus_port,
            modbus_host=self.modbus_host,
            modbus_tcp_port=self.modbus_tcp_port,
            modbus_device_profile_name=self.modbus_device_profile_name,
            ms6514_port=self.ms6514_port,
            aillio_model=self.aillio_model,
            tc4_port=self.tc4_port,
        )

    def to_roast(self) -> Roast:
        return Roast(**self.summary().model_dump(), profile=self.profile, events=self.events, notes=self.notes)


class RoastSessionManager:
    def __init__(self) -> None:
        self.sessions: dict[str, RoastSession] = {}

    def _release_stale_same_port_session(self, request: RoastCreateRequest) -> None:
        """A modbus_live/ms6514_live session that was merely connected (ON)
        but never started recording has no DB row (see create()'s own
        comment) and isn't found by the frontend's reconnectActiveRoast()
        (which only looks for roasting/cooling roasts) -- if the operator
        navigates away without clicking OFF first, it sits forever holding
        the real serial port with no way to discover or release it short
        of restarting the backend. A serial port only accepts one client
        at a time (see ModbusEngine's own module docstring), so a new
        connect attempt to the exact same port is treated as implicit
        proof the old one was abandoned, not a deliberate second
        connection -- release it first instead of letting the new attempt
        fail against it. Deliberately scoped to IDLE-and-unrecorded only:
        an actually active (roasting/cooling) session on the same port is
        never touched here, even if that's also technically a collision --
        this is about cleaning up a leak, not tearing down a real roast."""
        # aillio_live has no port/host at all (a raw USB device, not a
        # serial port -- see aillio_bridge/engine.py) but the exact same
        # leak risk applies to its claimed USB interface, so it's keyed
        # on the engine's own `model` attribute instead of `port` below.
        engine_attr = "port"
        if request.mode == RoastMode.MODBUS_LIVE:
            port = f"{request.modbus_host}:{request.modbus_tcp_port}" if request.modbus_transport == "tcp" else request.modbus_port
        elif request.mode == RoastMode.MS6514_LIVE:
            port = request.ms6514_port
        elif request.mode == RoastMode.AILLIO_LIVE:
            port = request.aillio_model
            engine_attr = "model"
        elif request.mode == RoastMode.TC4_LIVE:
            port = request.tc4_port
        else:
            return
        if not port:
            return
        for stale_id, stale in list(self.sessions.items()):
            if stale.mode != request.mode or stale.status != RoastStatus.IDLE or stale._recorded:
                continue
            if getattr(stale._engine, engine_attr, None) == port or (stale._sim_value is not None and stale._sim_value == port):
                stale._close_engine()
                del self.sessions[stale_id]

    def create(self, request: RoastCreateRequest, created_by_username: Optional[str] = None) -> RoastSession:
        """Builds the session and, for modbus_live/ms6514_live, makes the
        real synchronous connect attempt (inside the engine's own
        __init__) -- but no longer inserts a DB row itself; that now only
        happens once a roast actually starts recording (see
        RoastSession.start()/begin_recording()'s _persist_new_roast_row()
        calls), since a modbus_live/ms6514_live session can now sit
        connected-but-not-recording for a while first (see connect())."""
        if request.mode in (RoastMode.MODBUS_LIVE, RoastMode.MS6514_LIVE, RoastMode.AILLIO_LIVE, RoastMode.TC4_LIVE):
            self._release_stale_same_port_session(request)
        roast_id = str(uuid.uuid4())
        session = RoastSession(roast_id, request, created_by_username=created_by_username)
        if request.mode in (RoastMode.MODBUS_LIVE, RoastMode.MS6514_LIVE, RoastMode.AILLIO_LIVE, RoastMode.TC4_LIVE):
            # ModbusEngine/MS6514Engine/AillioEngine/TC4Engine's __init__ already made the real
            # connect attempt above and caught/swallowed any failure into
            # last_error rather than raising -- without this check, a bad
            # port (wrong COM number, or one another program is still holding open)
            # silently "succeeds" and the caller only ever discovers it by
            # noticing BT/ET never populate.
            engine_status = session._engine.status()
            if not engine_status.get("connected", True):
                session._close_engine()
                raise RoastSessionError(engine_status.get("last_error") or "failed to connect to the device")
        self.sessions[roast_id] = session
        return session

    def get(self, roast_id: str) -> Optional[RoastSession]:
        return self.sessions.get(roast_id)

    def active_roast_info(self) -> Optional[dict]:
        """{"id", "title"} for whichever session is ROASTING/COOLING right
        now, or None -- the one shared definition of "active roast," used
        by both main.py's health() poll and the /roasts/active/stream SSE
        push (ws_manager.active_roast_pubsub) below, so the two can't
        silently drift apart."""
        for s in self.sessions.values():
            if s.status in (RoastStatus.ROASTING, RoastStatus.COOLING):
                return {"id": s.id, "title": s.title}
        return None

    async def start(self, roast_id: str) -> RoastSession:
        session = self.get(roast_id)
        if session is None:
            raise RoastSessionError(f"unknown roast {roast_id}")
        await session.start()
        await active_roast_pubsub.publish(self.active_roast_info())
        return session

    async def connect(self, roast_id: str) -> RoastSession:
        session = self.get(roast_id)
        if session is None:
            raise RoastSessionError(f"unknown roast {roast_id}")
        await session.connect()
        return session

    async def begin_recording(self, roast_id: str) -> RoastSession:
        session = self.get(roast_id)
        if session is None:
            raise RoastSessionError(f"unknown roast {roast_id}")
        await session.begin_recording()
        await active_roast_pubsub.publish(self.active_roast_info())
        return session

    async def abort(self, roast_id: str) -> RoastSession:
        session = self.get(roast_id)
        if session is None:
            raise RoastSessionError(f"unknown roast {roast_id}")
        was_recorded = session._recorded
        await session.abort()
        if not was_recorded:
            # Never became a real roast -- nothing in storage references
            # it, so nothing else will ever clean it up. A recorded
            # session's own bookkeeping (DB row, .alog file) already
            # outlives this dict entry the same way it does today; this
            # is only about the connected-but-never-recorded case, which
            # has no such backing and would otherwise leak here forever.
            self.sessions.pop(roast_id, None)
        return session

    def get_roast_detail(self, roast_id: str) -> Optional[Roast]:
        session = self.get(roast_id)
        if session is not None:
            roast = session.to_roast()
            # Outcomes and the beans link are edited straight in the database.
            row = storage.get_roast_row(roast_id)
            if row is not None:
                for key in OUTCOME_KEYS:
                    setattr(roast, key, row.get(key))
                for key, value in _persistent_flags_from_row(row).items():
                    setattr(roast, key, value)
            return roast

        row = storage.get_roast_row(roast_id)
        if row is None or not row.get("alog_path"):
            return None
        parsed = self._read_recording(row)
        return Roast(
            id=row["id"],
            title=row["title"],
            mode=RoastMode(row["mode"]),
            status=RoastStatus(row["status"]),
            created_at=row["created_at"],
            beans=row["beans"],
            tags=storage.get_tags_for_roasts([roast_id]).get(roast_id, []),
            weight_green_g=row["weight_green_g"],
            weight_roasted_g=row["weight_roasted_g"],
            duration_s=row["duration_s"],
            alog_path=row["alog_path"],
            created_by_username=row.get("created_by_username"),
            **{key: row.get(key) for key in OUTCOME_KEYS},
            source_alog_path=row.get("source_alog_path"),
            playback_speed=row.get("playback_speed"),
            modbus_transport=row.get("modbus_transport"),
            modbus_port=row.get("modbus_port"),
            modbus_host=row.get("modbus_host"),
            modbus_tcp_port=row.get("modbus_tcp_port"),
            modbus_device_profile_name=row.get("modbus_device_profile_name"),
            ms6514_port=row.get("ms6514_port"),
            aillio_model=row.get("aillio_model"),
            tc4_port=row.get("tc4_port"),
            **_persistent_flags_from_row(row),
            profile=parsed["profile"],
            events=parsed["events"],
            notes=parsed["notes"],
        )

    @staticmethod
    def _read_recording(row: dict) -> dict:
        """Reads a saved roast's .alog file, turning a missing or unreadable
        file into a RoastSessionError that says what's wrong."""
        path = row["alog_path"]
        if not os.path.exists(path):
            raise RoastSessionError(
                f"the recording file for this roast is missing from the data folder ({os.path.basename(path)})"
            )
        try:
            return alog_dict_to_points(load_alog(path))
        except Exception as exc:  # noqa: BLE001 -- any parse failure should read as a message, not a crash
            raise RoastSessionError(f"the recording file for this roast couldn't be read ({exc})") from exc

    def _cold_roast_row_and_parsed(self, roast_id: str) -> tuple[dict, dict]:
        """Loads a roast with no live session purely from storage+.alog --
        same read path get_roast_detail's own cold branch above uses.
        Raises RoastSessionError (not returning None) since callers here
        are about to *write*, where "doesn't exist" should surface as a
        real error, not a silent no-op."""
        row = storage.get_roast_row(roast_id)
        if row is None or not row.get("alog_path"):
            raise RoastSessionError(f"unknown roast {roast_id}")
        return row, self._read_recording(row)

    def _rewrite_cold_alog(self, row: dict, parsed: dict) -> None:
        alog_dict = roast_to_native_alog_dict(
            title=row["title"],
            profile=parsed["profile"],
            events=parsed["events"],
            notes=parsed["notes"],
            beans=row["beans"],
            weight_green_g=row["weight_green_g"],
            weight_roasted_g=row["weight_roasted_g"],
            roastdate=row["created_at"],
        )
        save_native_alog(row["alog_path"], alog_dict)

    def delete_event(self, roast_id: str, event_id: str) -> dict:
        session = self.get(roast_id)
        if session is not None:
            return session.delete_event(event_id)
        row, parsed = self._cold_roast_row_and_parsed(roast_id)
        event, _ = _find_editable_milestone(parsed["events"], event_id)
        parsed["events"].remove(event)
        storage.update_roast(roast_id, duration_s=roast_duration_s(parsed["profile"], parsed["events"]))
        self._rewrite_cold_alog(row, parsed)
        return event

    def event_time_s(self, roast_id: str, event_id: str) -> Optional[float]:
        """Where an event sits right now, before an edit moves it -- for the
        Activity log's "from" time. None when there's no such event (the
        edit itself then says why)."""
        session = self.get(roast_id)
        if session is not None:
            events = session.events
        else:
            _, parsed = self._cold_roast_row_and_parsed(roast_id)
            events = parsed["events"]
        return next((e["time_s"] for e in events if e["id"] == event_id), None)

    def add_milestone_at(self, roast_id: str, event_type: RoastEventType, time_s: float) -> dict:
        session = self.get(roast_id)
        if session is not None:
            return session.add_milestone_at(event_type, time_s)
        row, parsed = self._cold_roast_row_and_parsed(roast_id)
        event = _add_milestone_at(parsed["events"], parsed["profile"], event_type, time_s)
        storage.update_roast(roast_id, duration_s=roast_duration_s(parsed["profile"], parsed["events"]))
        self._rewrite_cold_alog(row, parsed)
        # The id a client will see when it next loads this cold roast
        # (see alog_io._extract_named_milestones), not a throwaway one.
        event["id"] = f"milestone-{event_type.value}"
        return event

    def retime_event(self, roast_id: str, event_id: str, new_time_s: float) -> dict:
        session = self.get(roast_id)
        if session is not None:
            return session.retime_event(event_id, new_time_s)
        row, parsed = self._cold_roast_row_and_parsed(roast_id)
        event = _retime_milestone(parsed["events"], parsed["profile"], event_id, new_time_s)
        storage.update_roast(roast_id, duration_s=roast_duration_s(parsed["profile"], parsed["events"]))
        self._rewrite_cold_alog(row, parsed)
        return event

    def add_note(self, roast_id: str, req: NoteCreateRequest) -> dict:
        session = self.get(roast_id)
        if session is not None:
            return session.add_note(req)
        row, parsed = self._cold_roast_row_and_parsed(roast_id)
        note = {
            "time_s": round_note_time(parsed["profile"][-1]["time_s"] if parsed["profile"] else 0.0),
            "text": _clean_note_text(req.text),
            "author": req.author,
            "created_at": note_timestamp(),
        }
        parsed["notes"].append(note)
        assign_note_ids(parsed["notes"])
        storage.update_roast(roast_id, notes_text=notes_search_text(parsed["notes"]))
        self._rewrite_cold_alog(row, parsed)
        return note

    def update_note(self, roast_id: str, note_id: str, text: str) -> dict:
        session = self.get(roast_id)
        if session is not None:
            return session.update_note(note_id, text)
        row, parsed = self._cold_roast_row_and_parsed(roast_id)
        note = _find_note(parsed["notes"], note_id)
        note["text"] = _clean_note_text(text)
        assign_note_ids(parsed["notes"])
        storage.update_roast(roast_id, notes_text=notes_search_text(parsed["notes"]))
        self._rewrite_cold_alog(row, parsed)
        return note

    def delete_note(self, roast_id: str, note_id: str) -> None:
        session = self.get(roast_id)
        if session is not None:
            session.delete_note(note_id)
            return
        row, parsed = self._cold_roast_row_and_parsed(roast_id)
        parsed["notes"].remove(_find_note(parsed["notes"], note_id))
        assign_note_ids(parsed["notes"])
        storage.update_roast(roast_id, notes_text=notes_search_text(parsed["notes"]))
        self._rewrite_cold_alog(row, parsed)

    def set_weight_roasted(self, roast_id: str, grams: Optional[float]) -> None:
        session = self.get(roast_id)
        if session is not None:
            session.set_weight_roasted(grams)
            return
        row, parsed = self._cold_roast_row_and_parsed(roast_id)
        row["weight_roasted_g"] = grams
        storage.update_roast(roast_id, weight_roasted_g=grams)
        self._rewrite_cold_alog(row, parsed)

    def set_weight_green(self, roast_id: str, grams: Optional[float]) -> None:
        session = self.get(roast_id)
        if session is not None:
            session.set_weight_green(grams)
            return
        row, parsed = self._cold_roast_row_and_parsed(roast_id)
        row["weight_green_g"] = grams
        storage.update_roast(roast_id, weight_green_g=grams)
        self._rewrite_cold_alog(row, parsed)

    def set_tags(self, roast_id: str, tags: list[str]) -> None:
        session = self.get(roast_id)
        if session is not None:
            session.set_tags(tags)
            return
        # No _cold_roast_row_and_parsed/_rewrite_cold_alog here -- unlike
        # weight, tags never touch the .alog file, so this doesn't need
        # an alog_path (a roast the alog cold-read helpers would reject).
        # Just confirms the roast row itself exists.
        if storage.get_roast_row(roast_id) is None:
            raise RoastSessionError(f"unknown roast {roast_id}")
        storage.set_roast_tags(roast_id, tags)

    def delete(self, roast_id: str) -> None:
        session = self.get(roast_id)
        if session is not None and session.status in (RoastStatus.ROASTING, RoastStatus.COOLING):
            raise RoastSessionError(f"roast {roast_id} is still active -- stop it before deleting")
        self.sessions.pop(roast_id, None)

        row = storage.get_roast_row(roast_id)
        if row is None:
            raise RoastSessionError(f"unknown roast {roast_id}")
        if row.get("alog_path"):
            try:
                os.remove(row["alog_path"])
            except OSError:
                pass  # already gone, or never written (e.g. a stale/orphaned row) -- fine either way
        storage.delete_roast_row(roast_id)

    def backfill_durations(self) -> None:
        """One-time pass over roasts saved before duration meant Charge to
        Drop: recompute it from each roast's own .alog. Runs once (tracked
        with SQLite's user_version) and skips any file it can't read."""
        if storage.get_schema_version() >= 1:
            return
        for row in storage.list_roast_rows(limit=100000):
            if not row.get("alog_path") or not os.path.exists(row["alog_path"]):
                continue
            try:
                parsed = alog_dict_to_points(load_alog(row["alog_path"]))
                new_duration = roast_duration_s(parsed["profile"], parsed["events"])
            except Exception:
                continue
            if row.get("duration_s") is None or abs(new_duration - row["duration_s"]) > 0.05:
                storage.update_roast(row["id"], duration_s=new_duration)
        storage.set_schema_version(1)

    def resync_durations(self) -> None:
        """One-time: a roast that finished before its milestones were
        matched to its saved file (see RoastSession._match_events_to_alog)
        may have stored a duration up to one sample off from what its own
        file gives -- recompute it from the file, as backfill_durations
        did once before."""
        if storage.get_schema_version() >= 5:
            return
        for row in storage.list_roast_rows(limit=100000):
            if not row.get("alog_path") or not os.path.exists(row["alog_path"]):
                continue
            try:
                parsed = alog_dict_to_points(load_alog(row["alog_path"]))
                new_duration = roast_duration_s(parsed["profile"], parsed["events"])
            except Exception:
                continue
            if row.get("duration_s") is None or abs(new_duration - row["duration_s"]) > 0.05:
                storage.update_roast(row["id"], duration_s=new_duration)
        storage.set_schema_version(5)

    def backfill_beans(self) -> None:
        """One-time: roasts that have a beans name typed on them but no Beans
        record get one (matched by name, ignoring case), so the Beans list
        includes every name already in use."""
        if storage.get_schema_version() >= 3:
            return
        for row in storage.list_roast_rows(limit=100000):
            if row.get("bean_id") or not (row.get("beans") or "").strip():
                continue
            bean = storage.ensure_bean(row["beans"])
            if bean:
                storage.update_roast(row["id"], bean_id=bean["id"], beans=bean["name"])
        storage.set_schema_version(3)

    def clean_bean_records(self) -> None:
        """One-time: beans records whose name is really a whole description, or
        carries escape codes for line breaks and accents (from an uploaded log),
        are split into a proper name and details."""
        if storage.get_schema_version() >= 4:
            return
        for bean in storage.list_beans():
            parsed = parse_beans_description(bean["name"])
            if parsed["name"] and (parsed["name"] != bean["name"] or "notes" in parsed):
                storage.rewrite_bean_from_description(bean["id"], parsed)
        storage.set_schema_version(4)

    def backfill_dates(self) -> None:
        """One-time repair for roasts imported before dates were read
        properly: their created_at held text like "Sun Mar 01 2026", which
        sorts and filters wrongly. Re-reads it from each roast's own file
        and skips any it can't."""
        if storage.get_schema_version() >= 2:
            return
        for row in storage.list_roast_rows(limit=100000):
            created = row.get("created_at") or ""
            if re.match(r"^\d{4}-\d{2}-\d{2}", created):
                continue
            if not row.get("alog_path") or not os.path.exists(row["alog_path"]):
                continue
            try:
                fixed = alog_created_at(load_alog(row["alog_path"]))
            except Exception:
                continue
            if fixed:
                storage.update_roast(row["id"], created_at=fixed)
        storage.set_schema_version(2)

    def list_summaries(self, **filters) -> list[RoastSummary]:
        rows = storage.list_roast_rows(**filters)
        tag_map = storage.get_tags_for_roasts([r["id"] for r in rows])
        return [
            RoastSummary(
                id=r["id"], title=r["title"], mode=RoastMode(r["mode"]),
                status=RoastStatus(r["status"]), created_at=r["created_at"], beans=r["beans"],
                tags=tag_map.get(r["id"], []),
                weight_green_g=r["weight_green_g"], weight_roasted_g=r["weight_roasted_g"],
                duration_s=r["duration_s"], alog_path=r["alog_path"],
                created_by_username=r.get("created_by_username"),
                **{key: r.get(key) for key in OUTCOME_KEYS},
                **_persistent_flags_from_row(r),
            )
            for r in rows
        ]

    def count_summaries(self, **filters) -> int:
        return storage.count_roast_rows(**filters)

    def import_alog(self, source_path: str, title: Optional[str] = None, created_by_username: Optional[str] = None) -> RoastSummary:
        roast_id = str(uuid.uuid4())
        dest_path = storage.alog_path_for(roast_id)
        data = load_alog(source_path)
        # A plain file copy, not a parse-then-reserialize round trip --
        # there's nothing to transform on import, and copying preserves
        # the source file exactly (the native syntax when it's a
        # real .alog export, which is the common case for this feature).
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        shutil.copyfile(source_path, dest_path)
        parsed = alog_dict_to_points(data)
        duration_s = roast_duration_s(parsed["profile"], parsed["events"])
        bean = storage.ensure_bean(parsed["beans"])
        summary = {
            "id": roast_id,
            "title": title or data.get("title") or "Imported roast",
            "mode": RoastMode.ALOG_PLAYBACK.value,
            "status": RoastStatus.COMPLETE.value,
            "created_at": alog_created_at(data) or datetime.now(timezone.utc).isoformat(),
            "beans": (bean or {}).get("name") or parsed["beans"],
            "bean_id": (bean or {}).get("id"),
            "weight_green_g": parsed["weight_green_g"],
            "weight_roasted_g": parsed["weight_roasted_g"],
            "duration_s": duration_s,
            "alog_path": dest_path,
            "created_by_username": created_by_username,
        }
        storage.insert_roast({**summary, "notes_text": notes_search_text(parsed["notes"])})
        return RoastSummary(**{k: v for k, v in summary.items() if k in RoastSummary.model_fields})

    def import_table(
        self, *, parsed: dict, title: Optional[str] = None, created_by_username: Optional[str] = None
    ) -> RoastSummary:
        """Imports a roast log table (a parsed CSV/TSV or Excel file -- see
        alog_playback/roastlog.py) that has no title, beans or weights of its
        own -- unlike import_alog, this builds a fresh .alog file (via
        roast_to_native_alog_dict) rather than copying the source, since the
        source isn't that shape in the first place.

        roast_to_native_alog_dict assumes its caller's first profile sample is
        essentially at Charge (true for every roast this app records itself --
        see its own Charge-index comment), which a real-world log's genuine
        pre-charge history is not. So Charge, when the file has one, is made
        the new zero here first: earlier samples are dropped and every
        remaining time is shifted, matching how every roast recorded by this
        app already starts -- a real-world file's own pre-charge minutes have
        nowhere else to go in this app's model."""
        roast_id = str(uuid.uuid4())
        dest_path = storage.alog_path_for(roast_id)
        profile, events = parsed["profile"], parsed["events"]
        charge = next((e for e in events if e["type"] == "CHARGE"), None)
        if charge is not None:
            charge_t = charge["time_s"]
            profile = [p for p in profile if p["time_s"] >= charge_t]
            profile = [{**p, "time_s": round(p["time_s"] - charge_t, 2)} for p in profile]
            events = [{**e, "time_s": round(e["time_s"] - charge_t, 2)} for e in events]
        alog_dict = roast_to_native_alog_dict(
            title=title or "Imported roast", profile=profile, events=events, notes=[], beans=None,
            weight_green_g=None, weight_roasted_g=None, roastdate=datetime.now(timezone.utc).isoformat(),
        )
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        save_native_alog(dest_path, alog_dict)
        summary = {
            "id": roast_id,
            "title": title or "Imported roast",
            "mode": RoastMode.ALOG_PLAYBACK.value,
            "status": RoastStatus.COMPLETE.value,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "beans": None,
            "bean_id": None,
            "weight_green_g": None,
            "weight_roasted_g": None,
            "duration_s": roast_duration_s(profile, events),
            "alog_path": dest_path,
            "created_by_username": created_by_username,
        }
        storage.insert_roast(summary)
        return RoastSummary(**{k: v for k, v in summary.items() if k in RoastSummary.model_fields})


session_manager = RoastSessionManager()
