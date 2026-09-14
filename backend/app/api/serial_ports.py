"""Lists the serial ports the OS currently sees -- lets the Configure
Roast form's Serial port / drive-port fields offer a dropdown of what's
actually plugged in (e.g. "COM3 -- USB-SERIAL CH340"), the same idea as
Artisan's own port picker, instead of requiring a trip to Device Manager
to find the right COM number. Free-text entry stays available in the
form regardless -- this is just a convenience list, not a whitelist (a
port not currently enumerated, e.g. the fake hardware's /tmp path used
for testing, or a real device plugged in after the page loaded but
before a refresh, still has to work by typing it in)."""
from __future__ import annotations

from fastapi import APIRouter
from serial.tools import list_ports

from ..models import SerialPortInfo

router = APIRouter(prefix="/serial-ports", tags=["serial-ports"])


@router.get("", response_model=list[SerialPortInfo])
def list_serial_ports() -> list[SerialPortInfo]:
    ports = list_ports.comports()
    return [
        SerialPortInfo(device=p.device, description=p.description if p.description != "n/a" else None)
        for p in sorted(ports, key=lambda p: p.device)
    ]
