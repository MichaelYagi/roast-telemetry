"""Live roast state management: orchestrates a device (simulator or
playback) over its lifecycle and streams samples out over pub/sub.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from alog_playback import AlogPlayer, alog_dict_to_points, load_alog, roast_to_alog_dict, save_alog
from artisan_bridge import ArtisanBridgeEngine
from mock_device import MockDevice
from modbus_bridge import ModbusEngine
from ms6514_bridge import MS6514Engine
from simulator import SimulatorEngine

from .. import storage
from ..models import (
    ControlCommand,
    EventCreateRequest,
    NoteCreateRequest,
    Roast,
    RoastCreateRequest,
    RoastMode,
    RoastStatus,
    RoastSummary,
)
from ..ws_manager import pubsub


class RoastSessionError(RuntimeError):
    pass


def _format_machine_label(machine: Optional[dict]) -> Optional[str]:
    if not machine:
        return None
    label = " ".join(part for part in (machine.get("brand"), machine.get("model")) if part)
    return label or None


class RoastSession:
    def __init__(self, roast_id: str, request: RoastCreateRequest, machine: Optional[dict]):
        self.id = roast_id
        self.title = request.title
        self.mode = request.mode
        self.machine_id = request.machine_id
        self.machine = machine
        self.machine_label = _format_machine_label(machine)
        self.beans = request.beans
        self.weight_green_g = request.weight_green_g
        self.weight_roasted_g: Optional[float] = None
        self.sample_interval_s = max(0.1, request.sample_interval_s)
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.status = RoastStatus.IDLE
        self.duration_s: Optional[float] = None
        self.alog_path = storage.alog_path_for(roast_id)

        self.profile: list[dict] = []
        self.events: list[dict] = []
        self.notes: list[dict] = []

        if request.mode == RoastMode.SIMULATOR:
            engine = SimulatorEngine()
        elif request.mode == RoastMode.ALOG_PLAYBACK:
            if not request.alog_path:
                raise RoastSessionError("alog_path is required for alog_playback mode")
            engine = AlogPlayer(request.alog_path, speed=request.playback_speed)
        elif request.mode == RoastMode.ARTISAN_LIVE:
            if not request.artisan_host:
                raise RoastSessionError("artisan_host is required for artisan_live mode")
            try:
                engine = ArtisanBridgeEngine(
                    request.artisan_host,
                    request.artisan_port,
                    dry_end_c=request.dry_end_c,
                    fc_start_c=request.fc_start_c,
                )
            except ValueError as exc:
                raise RoastSessionError(str(exc)) from exc
        elif request.mode == RoastMode.MODBUS_LIVE:
            if not request.modbus_port:
                raise RoastSessionError("modbus_port is required for modbus_live mode")
            try:
                engine = ModbusEngine(
                    request.modbus_port,
                    baudrate=request.modbus_baudrate,
                    dry_end_c=request.dry_end_c,
                    fc_start_c=request.fc_start_c,
                )
            except ValueError as exc:
                raise RoastSessionError(str(exc)) from exc
        elif request.mode == RoastMode.MS6514_LIVE:
            if not request.ms6514_port:
                raise RoastSessionError("ms6514_port is required for ms6514_live mode")
            try:
                engine = MS6514Engine(
                    request.ms6514_port,
                    dry_end_c=request.dry_end_c,
                    fc_start_c=request.fc_start_c,
                )
            except ValueError as exc:
                raise RoastSessionError(str(exc)) from exc
        else:  # pragma: no cover - guarded by enum
            raise RoastSessionError(f"unsupported mode {request.mode}")

        self._engine = engine
        self.device = MockDevice(device_id=roast_id, engine=engine)
        self._task: Optional[asyncio.Task] = None
        self._stop_requested = asyncio.Event()

    # -- lifecycle -----------------------------------------------------
    async def start(self) -> None:
        await asyncio.to_thread(self.device.connect)
        if isinstance(self._engine, SimulatorEngine):
            self._engine.start()
        self.status = RoastStatus.ROASTING
        storage.update_roast(self.id, status=self.status.value)
        self._task = asyncio.create_task(self._run_loop())

    async def _run_loop(self) -> None:
        try:
            while not self._stop_requested.is_set():
                sample = await asyncio.to_thread(self.device.read, self.sample_interval_s)
                events = sample.pop("events", [])
                finished = sample.pop("finished", False)

                self.profile.append(sample)
                self.events.extend(events)

                await pubsub.publish(self.id, {
                    "type": "sample",
                    "roast_id": self.id,
                    "sample": sample,
                    "events": events,
                })

                if finished:
                    await self._finish(RoastStatus.COMPLETE)
                    break

                await asyncio.sleep(self.sample_interval_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            self.status = RoastStatus.ABORTED
            storage.update_roast(self.id, status=self.status.value)
            await pubsub.publish(self.id, {"type": "error", "roast_id": self.id, "message": str(exc)})

    async def abort(self) -> None:
        self._stop_requested.set()
        if self._task is not None and self._task is not asyncio.current_task():
            await self._task
        if self.status not in (RoastStatus.COMPLETE, RoastStatus.ABORTED):
            await self._finish(RoastStatus.ABORTED)

    async def _finish(self, status: RoastStatus) -> None:
        self._stop_requested.set()
        self.status = status
        self.duration_s = self.profile[-1]["time_s"] if self.profile else 0.0
        await asyncio.to_thread(self.device.disconnect)
        if isinstance(self._engine, ArtisanBridgeEngine):
            self._engine.close()  # stop its background WebSocket thread
        elif isinstance(self._engine, (ModbusEngine, MS6514Engine)):
            self._engine.close()  # release the serial port

        alog_dict = roast_to_alog_dict(
            title=self.title,
            profile=self.profile,
            events=self.events,
            notes=self.notes,
            beans=self.beans,
            weight_green_g=self.weight_green_g,
            weight_roasted_g=self.weight_roasted_g,
            machine_brand=(self.machine or {}).get("brand"),
            machine_model=(self.machine or {}).get("model"),
            roastdate=self.created_at,
        )
        save_alog(self.alog_path, alog_dict)

        storage.update_roast(
            self.id,
            status=self.status.value,
            duration_s=self.duration_s,
            weight_roasted_g=self.weight_roasted_g,
            alog_path=self.alog_path,
        )
        await pubsub.publish(self.id, {"type": "finished", "roast_id": self.id, "status": self.status.value})

    # -- interaction ----------------------------------------------------
    def apply_command(self, command: ControlCommand) -> dict:
        if self.status not in (RoastStatus.ROASTING, RoastStatus.COOLING):
            raise RoastSessionError(f"roast {self.id} is not active (status={self.status.value})")
        payload = command.model_dump(exclude_none=True)
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
        event = {
            "id": str(uuid.uuid4()),
            "time_s": self.profile[-1]["time_s"] if self.profile else 0.0,
            "type": req.type.value,
            "label": req.label,
            "value": req.value,
        }
        self.events.append(event)
        return event

    def set_weight_roasted(self, grams: float) -> None:
        self.weight_roasted_g = grams

    # -- serialization ----------------------------------------------------
    def summary(self) -> RoastSummary:
        return RoastSummary(
            id=self.id,
            title=self.title,
            mode=self.mode,
            machine_id=self.machine_id,
            machine_label=self.machine_label,
            status=self.status,
            created_at=self.created_at,
            beans=self.beans,
            weight_green_g=self.weight_green_g,
            weight_roasted_g=self.weight_roasted_g,
            duration_s=self.duration_s,
            alog_path=self.alog_path if self.status in (RoastStatus.COMPLETE, RoastStatus.ABORTED) else None,
        )

    def to_roast(self) -> Roast:
        return Roast(**self.summary().model_dump(), profile=self.profile, events=self.events, notes=self.notes)


class RoastSessionManager:
    def __init__(self) -> None:
        self.sessions: dict[str, RoastSession] = {}

    def create(self, request: RoastCreateRequest, machine: Optional[dict]) -> RoastSession:
        roast_id = str(uuid.uuid4())
        session = RoastSession(roast_id, request, machine)
        self.sessions[roast_id] = session
        storage.insert_roast({
            "id": session.id,
            "title": session.title,
            "mode": session.mode.value,
            "machine_id": session.machine_id,
            "machine_label": session.machine_label,
            "status": session.status.value,
            "created_at": session.created_at,
            "beans": session.beans,
            "weight_green_g": session.weight_green_g,
            "weight_roasted_g": None,
            "duration_s": None,
            "alog_path": None,
        })
        return session

    def get(self, roast_id: str) -> Optional[RoastSession]:
        return self.sessions.get(roast_id)

    async def start(self, roast_id: str) -> RoastSession:
        session = self.get(roast_id)
        if session is None:
            raise RoastSessionError(f"unknown roast {roast_id}")
        await session.start()
        return session

    async def abort(self, roast_id: str) -> RoastSession:
        session = self.get(roast_id)
        if session is None:
            raise RoastSessionError(f"unknown roast {roast_id}")
        await session.abort()
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
            machine_id=row["machine_id"],
            machine_label=row["machine_label"],
            status=RoastStatus(row["status"]),
            created_at=row["created_at"],
            beans=row["beans"],
            weight_green_g=row["weight_green_g"],
            weight_roasted_g=row["weight_roasted_g"],
            duration_s=row["duration_s"],
            alog_path=row["alog_path"],
            profile=parsed["profile"],
            events=parsed["events"],
            notes=parsed["notes"],
        )

    def list_summaries(self, **filters) -> list[RoastSummary]:
        rows = storage.list_roast_rows(**filters)
        return [
            RoastSummary(
                id=r["id"], title=r["title"], mode=RoastMode(r["mode"]), machine_id=r["machine_id"],
                machine_label=r["machine_label"],
                status=RoastStatus(r["status"]), created_at=r["created_at"], beans=r["beans"],
                weight_green_g=r["weight_green_g"], weight_roasted_g=r["weight_roasted_g"],
                duration_s=r["duration_s"], alog_path=r["alog_path"],
            )
            for r in rows
        ]

    def import_alog(self, source_path: str, title: Optional[str] = None) -> RoastSummary:
        roast_id = str(uuid.uuid4())
        dest_path = storage.alog_path_for(roast_id)
        data = load_alog(source_path)
        save_alog(dest_path, data)
        parsed = alog_dict_to_points(data)
        duration_s = parsed["profile"][-1]["time_s"] if parsed["profile"] else 0.0
        summary = {
            "id": roast_id,
            "title": title or data.get("title") or "Imported roast",
            "mode": RoastMode.ALOG_PLAYBACK.value,
            "machine_id": None,
            "machine_label": _format_machine_label(parsed["machine"]),
            "status": RoastStatus.COMPLETE.value,
            "created_at": data.get("roastdate") or datetime.now(timezone.utc).isoformat(),
            "beans": parsed["beans"],
            "weight_green_g": parsed["weight_green_g"],
            "weight_roasted_g": parsed["weight_roasted_g"],
            "duration_s": duration_s,
            "alog_path": dest_path,
        }
        storage.insert_roast(summary)
        return RoastSummary(**{k: v for k, v in summary.items() if k in RoastSummary.model_fields})


session_manager = RoastSessionManager()
