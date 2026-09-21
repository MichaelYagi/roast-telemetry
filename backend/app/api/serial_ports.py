"""Lists the serial ports the OS currently sees -- lets the Configure
Roast form's Serial port / drive-port fields offer a dropdown of what's
actually plugged in (e.g. "COM3 -- USB-SERIAL CH340"), instead of requiring a trip to Device Manager
to find the right COM number. Free-text entry stays available in the
form regardless -- this is just a convenience list, not a whitelist (a
port not currently enumerated, e.g. the fake hardware's /tmp path used
for testing, or a real device plugged in after the page loaded but
before a refresh, still has to work by typing it in)."""
from __future__ import annotations

from fastapi import APIRouter
from serial.tools import list_ports

from hardware_fakes import sim as simulated_devices

from ..models import SerialPortInfo

router = APIRouter(prefix="/serial-ports", tags=["serial-ports"])


@router.get("", response_model=list[SerialPortInfo])
def list_serial_ports() -> list[SerialPortInfo]:
    ports = list_ports.comports()
    real = [
        SerialPortInfo(device=p.device, description=p.description if p.description != "n/a" else None)
        for p in sorted(ports, key=lambda p: p.device)
    ]
    # Built-in simulated devices come after the real ports (the Evo connects by
    # host, not port, so it isn't listed here -- the form offers it beside the
    # host field instead).
    simulated = [
        SerialPortInfo(device=f"{simulated_devices.PREFIX}{k.key}", description=k.label, simulated=True, mode=k.mode)
        for k in simulated_devices.KINDS.values()
        if k.transport == "serial"
    ]
    return real + simulated
