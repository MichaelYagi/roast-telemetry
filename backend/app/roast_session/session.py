"""Live roast state management: orchestrates a device (simulator or
playback) over its lifecycle and streams samples out over pub/sub.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from typing import Optional

from aillio_bridge import AillioEngine
from alog_playback import (
    AlogPlayer,
    alog_dict_to_points,
    load_alog,
    roast_to_artisan_native_dict,
    save_artisan_native_alog,
)
from mock_device import MockDevice
from modbus_bridge import ModbusEngine
from ms6514_bridge import MS6514Engine
from pymodbus.client import ModbusSerialClient, ModbusTcpClient
from simulator import SimulatorEngine
from tc4_bridge import TC4Engine

from .. import storage
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
from ..ws_manager import pubsub

logger = logging.getLogger(__name__)


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
        "modbus_air_feedback_register",
        "modbus_drum_slave_id", "modbus_drum_control_register", "modbus_drum_frequency_register",
        "modbus_drum_feedback_register",
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
        raise RoastSessionError(f"{event_type.value} is always auto-detected -- it can't be edited")
    if event_type == RoastEventType.CUSTOM:
        raise RoastSessionError("CUSTOM events aren't milestones -- nothing to edit")
    return event, event_type


def _retime_milestone(events: list, profile: list, event_id: str, new_time_s: float) -> dict:
    """Validates and applies a milestone retime in place (bounds-checks
    against the roast's own recorded range and against whichever
    chronologically-adjacent milestones are already marked, then
    recomputes `value` from the profile at the new time) -- shared for
    the same reason as _find_editable_milestone above."""
    event, event_type = _find_editable_milestone(events, event_id)

    if profile and not (0.0 <= new_time_s <= profile[-1]["time_s"]):
        raise RoastSessionError(f"time_s {new_time_s} is outside this roast's recorded range (0-{profile[-1]['time_s']})")

    idx = MILESTONE_SEQUENCE.index(event_type)
    by_type = {RoastEventType(e["type"]): e for e in events if e["type"] != RoastEventType.CUSTOM.value}
    for earlier_type in reversed(MILESTONE_SEQUENCE[:idx]):
        if earlier_type in by_type:
            if new_time_s <= by_type[earlier_type]["time_s"]:
                raise RoastSessionError(f"can't move {event_type.value} before {earlier_type.value}")
            break
    for later_type in MILESTONE_SEQUENCE[idx + 1:]:
        if later_type in by_type:
            if new_time_s >= by_type[later_type]["time_s"]:
                raise RoastSessionError(f"can't move {event_type.value} past {later_type.value}")
            break

    nearest_bt = None
    if profile:
        nearest = min(profile, key=lambda p: abs(p["time_s"] - new_time_s))
        nearest_bt = nearest.get("bt")

    event["time_s"] = new_time_s
    event["value"] = nearest_bt
    return event


class RoastSession:
    def __init__(self, roast_id: str, request: RoastCreateRequest, created_by_username: Optional[str] = None):
        self.id = roast_id
        self.created_by_username = created_by_username
        self.title = request.title
        self.mode = request.mode
        self.beans = request.beans
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

        if request.mode == RoastMode.SIMULATOR:
            engine = SimulatorEngine()
        elif request.mode == RoastMode.ALOG_PLAYBACK:
            if not request.alog_path:
                raise RoastSessionError("Missing .alog file path -- enter the path to an .alog file on the server (Device tab, \".alog file path\") before connecting.")
            engine = AlogPlayer(request.alog_path, speed=request.playback_speed)
        elif request.mode == RoastMode.MODBUS_LIVE:
            is_tcp = request.modbus_transport == "tcp"
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
                        request.modbus_port,
                        transport=request.modbus_transport,
                        host=request.modbus_host,
                        tcp_port=request.modbus_tcp_port,
                        control_port=request.modbus_control_port,
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
                        request.modbus_port,
                        baudrate=request.modbus_baudrate,
                        control_port=request.modbus_control_port,
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
            try:
                engine = MS6514Engine(
                    request.ms6514_port,
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
            try:
                engine = TC4Engine(
                    request.tc4_port,
                    dry_end_c=request.dry_end_c,
                    fc_start_c=request.fc_start_c,
                    detect_milestones=request.auto_detect_milestones,
                )
            except ValueError as exc:
                raise RoastSessionError(str(exc)) from exc
        else:  # pragma: no cover - guarded by enum
            raise RoastSessionError(f"unsupported mode {request.mode}")

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
    def _persist_new_roast_row(self) -> None:
        storage.insert_roast({
            "id": self.id,
            "title": self.title,
            "mode": self.mode.value,
            "status": self.status.value,
            "created_at": self.created_at,
            "beans": self.beans,
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
                else:
                    self.profile.append(sample)
                    self.events.extend(events)
                    self._evaluate_ambient_alarms(sample)

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
            self.status = RoastStatus.ABORTED
            self._cancel_pending_alarms()
            if self._recorded:
                storage.update_roast(self.id, status=self.status.value)
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
            self._engine.close()  # release the serial port
        await pubsub.publish(self.id, {"type": "finished", "roast_id": self.id, "status": self.status.value})

    async def _finish(self, status: RoastStatus) -> None:
        self._stop_requested.set()
        self.status = status
        self.duration_s = self.profile[-1]["time_s"] if self.profile else 0.0
        await asyncio.to_thread(self.device.disconnect)
        if isinstance(self._engine, (ModbusEngine, MS6514Engine, TC4Engine)):
            self._engine.close()  # release the serial port

        # Written in real Artisan's own native shape (Python-literal syntax
        # + timeindex/computed/specialevents), not a bespoke JSON one --
        # this app used to write its own minimal JSON format, but that
        # could only round-trip through this app's own reader, not
        # actually open in Artisan itself. See roast_to_artisan_native_dict's
        # docstring for the full rationale.
        alog_dict = roast_to_artisan_native_dict(
            title=self.title,
            profile=self.profile,
            events=self.events,
            notes=self.notes,
            beans=self.beans,
            weight_green_g=self.weight_green_g,
            weight_roasted_g=self.weight_roasted_g,
            roastdate=self.created_at,
        )
        save_artisan_native_alog(self.alog_path, alog_dict)

        storage.update_roast(
            self.id,
            status=self.status.value,
            duration_s=self.duration_s,
            weight_roasted_g=self.weight_roasted_g,
            alog_path=self.alog_path,
            playback_speed=self.playback_speed,
        )
        await pubsub.publish(self.id, {"type": "finished", "roast_id": self.id, "status": self.status.value})

    # -- interaction ----------------------------------------------------
    def apply_command(self, command: ControlCommand) -> dict:
        # IDLE is included alongside ROASTING/COOLING -- for modbus_live/
        # ms6514_live specifically, it means "connected via connect(), not
        # yet recording" (see that method), not "no session at all" (which
        # isn't a status value, it's simply not having a session/roast_id
        # to call this on in the first place). Lets Air/Drum/Burner be
        # exercised -- Testing Mode's write check, or just manually -- while
        # merely armed, matching Artisan's own control-before-record model.
        if self.status not in (RoastStatus.IDLE, RoastStatus.ROASTING, RoastStatus.COOLING):
            raise RoastSessionError(f"roast {self.id} is not active (status={self.status.value})")
        payload = command.model_dump(exclude_none=True)
        if "speed" in payload:
            self.playback_speed = payload["speed"]
        return self.device.write(payload)

    def add_note(self, req: NoteCreateRequest) -> dict:
        note = {
            "id": str(uuid.uuid4()),
            "time_s": self.profile[-1]["time_s"] if self.profile else 0.0,
            "text": req.text,
            "author": req.author,
        }
        self.notes.append(note)
        return note

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
                raise RoastSessionError(f"{req.type.value} is always auto-detected -- it can't be marked manually")
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
            # a manual click -- confirmed against a real FZ-94 roast in
            # Artisan, which auto-plots Turning Point on the chart despite
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
            self.apply_command(command)
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
            alog_dict = roast_to_artisan_native_dict(
                title=self.title,
                profile=self.profile,
                events=self.events,
                notes=self.notes,
                beans=self.beans,
                weight_green_g=self.weight_green_g,
                weight_roasted_g=self.weight_roasted_g,
                roastdate=self.created_at,
            )
            save_artisan_native_alog(self.alog_path, alog_dict)

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
        # aren't an Artisan .alog concept, so there's nothing to round-trip
        # into that file. storage.set_roast_tags is the only durable copy.
        self.tags = tags
        storage.set_roast_tags(self.id, tags)

    def delete_event(self, event_id: str) -> None:
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

    def retime_event(self, event_id: str, new_time_s: float) -> dict:
        """Moves an already-marked milestone to a different point on the
        elapsed-time axis -- for correcting a click that landed too early
        or too late. See _retime_milestone for the actual validation/
        recompute logic, shared with RoastSessionManager's cold-roast
        path."""
        event = _retime_milestone(self.events, self.profile, event_id, new_time_s)
        self._rewrite_alog()
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
            if getattr(stale._engine, engine_attr, None) == port:
                stale._engine.close()
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
            # port (wrong COM number, or one Artisan is still holding open)
            # silently "succeeds" and the caller only ever discovers it by
            # noticing BT/ET never populate.
            engine_status = session._engine.status()
            if not engine_status.get("connected", True):
                session._engine.close()
                raise RoastSessionError(engine_status.get("last_error") or "failed to connect to the device")
        self.sessions[roast_id] = session
        return session

    def get(self, roast_id: str) -> Optional[RoastSession]:
        return self.sessions.get(roast_id)

    async def start(self, roast_id: str) -> RoastSession:
        session = self.get(roast_id)
        if session is None:
            raise RoastSessionError(f"unknown roast {roast_id}")
        await session.start()
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
            return session.to_roast()

        row = storage.get_roast_row(roast_id)
        if row is None or not row.get("alog_path"):
            return None
        parsed = alog_dict_to_points(load_alog(row["alog_path"]))
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
            profile=parsed["profile"],
            events=parsed["events"],
            notes=parsed["notes"],
        )

    def _cold_roast_row_and_parsed(self, roast_id: str) -> tuple[dict, dict]:
        """Loads a roast with no live session purely from storage+.alog --
        same read path get_roast_detail's own cold branch above uses.
        Raises RoastSessionError (not returning None) since callers here
        are about to *write*, where "doesn't exist" should surface as a
        real error, not a silent no-op."""
        row = storage.get_roast_row(roast_id)
        if row is None or not row.get("alog_path"):
            raise RoastSessionError(f"unknown roast {roast_id}")
        parsed = alog_dict_to_points(load_alog(row["alog_path"]))
        return row, parsed

    def _rewrite_cold_alog(self, row: dict, parsed: dict) -> None:
        alog_dict = roast_to_artisan_native_dict(
            title=row["title"],
            profile=parsed["profile"],
            events=parsed["events"],
            notes=parsed["notes"],
            beans=row["beans"],
            weight_green_g=row["weight_green_g"],
            weight_roasted_g=row["weight_roasted_g"],
            roastdate=row["created_at"],
        )
        save_artisan_native_alog(row["alog_path"], alog_dict)

    def delete_event(self, roast_id: str, event_id: str) -> None:
        session = self.get(roast_id)
        if session is not None:
            session.delete_event(event_id)
            return
        row, parsed = self._cold_roast_row_and_parsed(roast_id)
        event, _ = _find_editable_milestone(parsed["events"], event_id)
        parsed["events"].remove(event)
        self._rewrite_cold_alog(row, parsed)

    def retime_event(self, roast_id: str, event_id: str, new_time_s: float) -> dict:
        session = self.get(roast_id)
        if session is not None:
            return session.retime_event(event_id, new_time_s)
        row, parsed = self._cold_roast_row_and_parsed(roast_id)
        event = _retime_milestone(parsed["events"], parsed["profile"], event_id, new_time_s)
        self._rewrite_cold_alog(row, parsed)
        return event

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
        # the source file exactly (real Artisan's own syntax when it's a
        # real Artisan export, which is the common case for this feature).
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        shutil.copyfile(source_path, dest_path)
        parsed = alog_dict_to_points(data)
        duration_s = parsed["profile"][-1]["time_s"] if parsed["profile"] else 0.0
        summary = {
            "id": roast_id,
            "title": title or data.get("title") or "Imported roast",
            "mode": RoastMode.ALOG_PLAYBACK.value,
            "status": RoastStatus.COMPLETE.value,
            "created_at": data.get("roastdate") or datetime.now(timezone.utc).isoformat(),
            "beans": parsed["beans"],
            "weight_green_g": parsed["weight_green_g"],
            "weight_roasted_g": parsed["weight_roasted_g"],
            "duration_s": duration_s,
            "alog_path": dest_path,
            "created_by_username": created_by_username,
        }
        storage.insert_roast(summary)
        return RoastSummary(**{k: v for k, v in summary.items() if k in RoastSummary.model_fields})


session_manager = RoastSessionManager()
