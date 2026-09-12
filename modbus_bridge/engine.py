"""Direct Modbus RTU telemetry + control for a real roaster over USB --
bypasses Artisan entirely. Talks straight to the roaster's own PLC via
``pymodbus`` (the same library Artisan itself depends on for its Modbus
device support).

Register map default is Coffee-Tech Engineering's FZ94 EVO, taken
verbatim from Artisan's own machine preset
(``src/includes/Machines/Coffee-Tech/FZ94_EVO.aset``), not guessed:

- BT: holding register 100 (function code 3, read holding registers)
- DT: holding register 80 (Artisan calls this channel "ET" internally
  but relabels it "DT" for this machine -- it's the drum-mounted probe)
- Air: holding register 20, writable range 30-70 (function code 6,
  write single register)
- Drum: holding register 16, writable range 30-70
- Burner: holding register 35, writable range 30-100

The Air/Drum/Burner channel *names* are inferred, not confirmed against
the FZ94 EVO's own manual: they follow Artisan's standard slider
ordering convention (Air, Drum, Damper, Burner -- the same order seen in
a real Artisan .alog's `etypes` field elsewhere in this project) applied
to which slider slot the preset disables (slot 3 = Damper, absent here,
consistent with a burner/air/drum-only machine). Confirm which slider
moves which physical actuator on the real hardware before relying on
this for an actual roast -- swap the register numbers below if it turns
out to be wrong.

A different Modbus roaster model will have a completely different
register map. None of this is hardcoded to the FZ94 EVO specifically --
every address and range is a constructor argument; the defaults just
happen to be this machine's.

RoR and CHARGE/TURNING_POINT/DRY_END/FC_START auto-detection reuse
``roast_heuristics.LiveRoastDetector``, the same logic ``artisan_bridge``
uses, since a PLC's raw registers carry temperatures only -- no roast
events, same situation as WebLCDs.

Mutually exclusive with a running Artisan on the same connection: a
serial Modbus RTU port only accepts one client at a time. If your
operator is running Artisan against this same roaster, use
``artisan_bridge`` (mirrors Artisan, view-only, no port conflict)
instead of this engine (owns the port, but can control).

Not tested against real FZ94 EVO hardware (none available in this
environment) -- verified against a mocked pymodbus client instead. The
register addresses are real (from Artisan's own preset); the actual
wire-level RTU behavior against your specific unit is unverified.
"""
from __future__ import annotations

from typing import Optional

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException
from roast_heuristics import LiveRoastDetector


class ModbusEngineError(RuntimeError):
    pass


class _ControlChannel:
    __slots__ = ("register", "min_value", "max_value")

    def __init__(self, register: int, min_value: float, max_value: float):
        self.register = register
        self.min_value = min_value
        self.max_value = max_value


class ModbusEngine:
    def __init__(
        self,
        port: str,
        baudrate: int = 57600,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: int = 2,
        timeout: float = 0.4,
        slave_id: int = 1,
        bt_register: int = 100,
        et_register: Optional[int] = 80,
        bt_divisor: float = 1.0,
        et_divisor: float = 1.0,
        air_register: Optional[int] = 20,
        air_range: tuple[float, float] = (30, 70),
        drum_register: Optional[int] = 16,
        drum_range: tuple[float, float] = (30, 70),
        burner_register: Optional[int] = 35,
        burner_range: tuple[float, float] = (30, 100),
        dry_end_c: Optional[float] = 160.0,
        fc_start_c: Optional[float] = 196.0,
        client_cls=ModbusSerialClient,  # injectable for testing without real hardware
    ):
        if not port:
            raise ValueError("port (e.g. 'COM3') is required to connect via Modbus RTU")
        self.port = port
        self.slave_id = slave_id
        self.bt_register = bt_register
        self.et_register = et_register
        self.bt_divisor = bt_divisor or 1.0
        self.et_divisor = et_divisor or 1.0

        self._channels: dict[str, _ControlChannel] = {}
        if air_register is not None:
            self._channels["air"] = _ControlChannel(air_register, *air_range)
        if drum_register is not None:
            self._channels["drum"] = _ControlChannel(drum_register, *drum_range)
        if burner_register is not None:
            self._channels["burner"] = _ControlChannel(burner_register, *burner_range)

        self._detector = LiveRoastDetector(dry_end_c=dry_end_c, fc_start_c=fc_start_c)
        self._last_time_s = 0.0
        self._connected = False
        self._last_error: Optional[str] = None
        self._last_values: dict[str, float] = {}

        self._client = client_cls(
            port=port, baudrate=baudrate, bytesize=bytesize, parity=parity, stopbits=stopbits, timeout=timeout,
        )
        try:
            self._connected = bool(self._client.connect())
            if not self._connected:
                self._last_error = f"could not open serial port {port!r}"
        except Exception as exc:  # pragma: no cover - depends on local hardware/OS
            self._last_error = str(exc)
            self._connected = False

    # -- Modbus I/O -----------------------------------------------------
    def _read_register(self, address: int, *, is_heartbeat: bool = False) -> Optional[int]:
        """``is_heartbeat`` gates whether this read's outcome updates
        overall connected/last_error state. BT is the heartbeat (always
        required); ET is optional and shouldn't be able to mask a BT
        failure by succeeding afterward in the same tick, nor clear a
        real BT error just because it happened to work."""
        try:
            result = self._client.read_holding_registers(address, count=1, device_id=self.slave_id)
            if result.isError():
                if is_heartbeat:
                    self._last_error = str(result)
                    self._connected = False
                return None
            if is_heartbeat:
                self._last_error = None
                self._connected = True
            return result.registers[0]
        except ModbusException as exc:
            if is_heartbeat:
                self._last_error = str(exc)
                self._connected = False
            return None

    # -- engine contract (matches simulator.SimulatorEngine / AlogPlayer / ArtisanBridgeEngine) --
    def tick(self, dt: float) -> dict:
        self._last_time_s += dt
        time_s = self._last_time_s

        bt_raw = self._read_register(self.bt_register, is_heartbeat=True)
        bt = (bt_raw / self.bt_divisor) if bt_raw is not None else None

        et = None
        if self.et_register is not None:
            et_raw = self._read_register(self.et_register)
            et = (et_raw / self.et_divisor) if et_raw is not None else None

        sample = self._detector.observe(time_s, bt, et)
        sample["heater_pct"] = self._last_values.get("burner")
        sample["fan_pct"] = self._last_values.get("air")
        sample["drum_speed_pct"] = self._last_values.get("drum")
        return sample

    def get_new_events(self) -> list:
        return self._detector.get_new_events()

    def apply_command(self, cmd: dict) -> None:
        """Reuses the platform's existing heater_pct/fan_pct/drum_speed_pct
        control command shape -- mapped to burner/air/drum registers -- so
        the existing Controls UI works unmodified for this engine too."""
        mapping = {"heater_pct": "burner", "fan_pct": "air", "drum_speed_pct": "drum"}
        for cmd_key, channel_name in mapping.items():
            if cmd.get(cmd_key) is None:
                continue
            channel = self._channels.get(channel_name)
            if channel is None:
                continue
            value = max(channel.min_value, min(channel.max_value, float(cmd[cmd_key])))
            try:
                result = self._client.write_register(channel.register, int(round(value)), device_id=self.slave_id)
                if result.isError():
                    self._last_error = str(result)
                else:
                    self._last_error = None
                    self._connected = True
                    self._last_values[channel_name] = value
            except ModbusException as exc:
                self._last_error = str(exc)
                self._connected = False

    def is_finished(self) -> bool:
        return False  # no end-of-roast signal from the PLC; stop manually from the UI

    def status(self) -> dict:
        return {
            "mode": "modbus_live",
            "port": self.port,
            "connected": self._connected,
            "last_error": self._last_error,
            "channels": {
                name: {"register": c.register, "min": c.min_value, "max": c.max_value}
                for name, c in self._channels.items()
            },
        }

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
