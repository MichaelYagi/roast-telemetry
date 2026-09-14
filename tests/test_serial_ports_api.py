"""GET /api/serial-ports -- mocks serial.tools.list_ports.comports() rather
than depending on whatever serial hardware happens to be attached to the
machine running the tests (none, on CI)."""
from __future__ import annotations

from types import SimpleNamespace

from backend.app.api import serial_ports


def test_list_serial_ports_empty(client, monkeypatch):
    monkeypatch.setattr(serial_ports.list_ports, "comports", lambda: [])
    resp = client.get("/api/serial-ports")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_serial_ports_maps_device_and_description(client, monkeypatch):
    fake_ports = [
        SimpleNamespace(device="COM4", description="USB-SERIAL CH340 (COM4)"),
        SimpleNamespace(device="COM3", description="n/a"),
    ]
    monkeypatch.setattr(serial_ports.list_ports, "comports", lambda: fake_ports)
    resp = client.get("/api/serial-ports")
    assert resp.status_code == 200
    # Sorted by device -- COM3 before COM4 -- regardless of comports()'s own order.
    assert resp.json() == [
        {"device": "COM3", "description": None},
        {"device": "COM4", "description": "USB-SERIAL CH340 (COM4)"},
    ]
