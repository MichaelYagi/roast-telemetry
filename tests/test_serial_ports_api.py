"""GET /api/v1/serial-ports -- mocks serial.tools.list_ports.comports() rather
than depending on whatever serial hardware happens to be attached to the
machine running the tests (none, on CI)."""
from __future__ import annotations

from types import SimpleNamespace

from backend.app.api import serial_ports


def _real(resp):
    """Just the ports the OS reported -- the built-in simulated devices are covered below."""
    return [{"device": p["device"], "description": p["description"]} for p in resp.json() if not p["simulated"]]


def test_simulated_devices_are_listed_after_the_real_ports(client, monkeypatch):
    fake_ports = [SimpleNamespace(device="COM3", description="USB-SERIAL CH340 (COM3)")]
    monkeypatch.setattr(serial_ports.list_ports, "comports", lambda: fake_ports)
    ports = client.get("/api/v1/serial-ports").json()

    assert ports[0]["device"] == "COM3" and ports[0]["simulated"] is False
    simulated = [p for p in ports if p["simulated"]]
    assert ports[1:] == simulated  # all after the real ones
    assert {p["device"]: p["mode"] for p in simulated} == {
        "sim://fz94": "modbus_live",
        "sim://ms6514": "ms6514_live",
        "sim://tc4": "tc4_live",
    }
    assert all("no hardware needed" in p["description"] for p in simulated)


def test_list_serial_ports_empty(client, monkeypatch):
    monkeypatch.setattr(serial_ports.list_ports, "comports", lambda: [])
    resp = client.get("/api/v1/serial-ports")
    assert resp.status_code == 200
    assert _real(resp) == []


def test_list_serial_ports_maps_device_and_description(client, monkeypatch):
    fake_ports = [
        SimpleNamespace(device="COM4", description="USB-SERIAL CH340 (COM4)"),
        SimpleNamespace(device="COM3", description="n/a"),
    ]
    monkeypatch.setattr(serial_ports.list_ports, "comports", lambda: fake_ports)
    resp = client.get("/api/v1/serial-ports")
    assert resp.status_code == 200
    # Sorted by device -- COM3 before COM4 -- regardless of comports()'s own order.
    assert _real(resp) == [
        {"device": "COM3", "description": None},
        {"device": "COM4", "description": "USB-SERIAL CH340 (COM4)"},
    ]
