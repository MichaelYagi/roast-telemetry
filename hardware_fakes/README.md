# Hardware fakes

Standalone scripts that stand in for real roasting hardware, so
`modbus_live`, `ms6514_live`, and `tc4_live` can each be tested
end-to-end through the app's actual connection code without owning a
roaster. All are driven by the same roast physics as the app's own
simulator mode (`_thermal.py`, wrapping
`simulator.SimulatorEngine`) so BT/ET behave like a real roast no
matter which one you're using.

| Fake | Stands in for | Protocol | Needs a virtual serial port? |
|---|---|---|---|
| `modbus_fz94.py` | Coffee-Tech FZ-94 (plain, not Evo) | Modbus RTU (hand-rolled framing) | Yes — one (two only for unusual wiring) |
| `modbus_fz94_evo.py` | Coffee-Tech FZ-94 Evo | Modbus TCP (hand-rolled MBAP framing) | No — a real TCP listener, point the app straight at it |
| `ms6514_device.py` | Mastech MS6514 meter | Raw 18-byte serial frames | Yes |
| `tc4.py` | TC4+ (aArtisanQ/PID firmware) | Plain ASCII commands (`READ`/`OT1`/`DCFAN`) | Yes |

**Quick start:** `../scripts/fake-hardware.sh fz94`,
`../scripts/fake-hardware.sh ms6514`, or
`../scripts/fake-hardware.sh tc4` wraps the whole socat-pair +
fake-process dance below into one command, using the same fixed port
name every run (so a serial port you've already saved in a Configure
Roast preset keeps working across restarts), and prints the port to
paste into the app when it's ready. Add
`--tcp` (optionally `--tcp <port>`, default 5020) if the app is running
natively on Windows rather than in WSL alongside the fake -- a
WSL-internal `/tmp/...` path isn't reachable from a native-Windows
process at all, so this bridges to a TCP listener instead and prints a
`socket://127.0.0.1:<port>` URL to paste in instead (pyserial treats
that as a live serial connection; verified working -- reads and writes
both -- over WSL2's default localhost port forwarding). The manual
steps below are what it's doing under the hood either way, useful if
you need something the wrapper doesn't expose (e.g. `--drive-port` for
a genuinely separate Air/Drum connection).

`../scripts/fake-hardware.sh evo [port]` (default 5020) is the Evo's
own equivalent, but genuinely simpler -- the Evo speaks real Modbus TCP
natively, so there's no socat/virtual-serial-port step at all, no
`--tcp` flag, and it works identically whether the app runs in WSL or
natively on Windows. It prints the host/port/device-profile to paste
into the app's Data Source "Direct Modbus (Ethernet)" fields.

## Virtual serial port setup

`ModbusSerialClient` and `pyserial.Serial` (used by the real engines)
both need an actual OS serial device -- they can't be pointed at a
plain TCP socket. `socat` creates a *linked pair* of fake serial ports;
the fake writes to one end, the app's Direct-Modbus/MS6514 `port` field
reads from the other, exactly as if a real USB-serial cable connected
them.

Install once:

```
sudo apt install socat
```

### Modbus (FZ-94)

See [docs/modbus/fz-94-usb.html](../docs/modbus/fz-94-usb.html#testing-without-real-hardware)
-- the fake's own setup (socat pair, `--drive-port` for the
separate-connection case, suggested test-settings timeline, Windows
notes) now lives there alongside the rest of the FZ-94-specific detail,
rather than duplicated in both places.

### MS6514

Same pattern, different pair:

```
# terminal 1
socat -d -d pty,raw,echo=0,link=/tmp/ttyFAKE_METER pty,raw,echo=0,link=/tmp/ttyFAKE_METER_APP

# terminal 2
python -m hardware_fakes.ms6514_device --port /tmp/ttyFAKE_METER
```

In the app, choose **Direct USB (thermocouple meter)**, serial port
`/tmp/ttyFAKE_METER_APP`. This one's read-only in real life too, so
there's nothing to control -- just BT/ET streaming in.

### TC4+

Same socat-pair pattern, but this fake actually responds to commands
(request/response, not continuous streaming) and reacts to Heater/Fan
control writes, same as the Modbus fake does:

```
# terminal 1
socat -d -d pty,raw,echo=0,link=/tmp/ttyFAKE_TC4 pty,raw,echo=0,link=/tmp/ttyFAKE_TC4_APP

# terminal 2
python -m hardware_fakes.tc4 --port /tmp/ttyFAKE_TC4
```

In the app, choose **TC4+ (USB, PID firmware)**, serial port
`/tmp/ttyFAKE_TC4_APP`. Unlike MS6514, this one has real Heater/Fan
control (OT1/DCFAN) -- dragging those vertical-control sliders actually
bends the fake's simulated BT/ET curve.

## Modbus TCP (FZ-94 Evo)

None of the virtual-serial-port setup above applies here -- the Evo
speaks real Modbus TCP natively, so its fake is just a plain TCP
listener:

```
python -m hardware_fakes.modbus_fz94_evo --port 5020
```

In the app, choose Data source **Direct Modbus (Ethernet)**, Host
`127.0.0.1`, TCP port `5020`, Device profile "Coffee-Tech FZ-94 Evo
(built-in)". Works identically whether the app runs in WSL or natively
on Windows -- both reach `127.0.0.1` directly, no bridging needed.

## Notes

- Once a fake's simulated roast finishes (Cool End), BT/ET just hold
  flat at their final cooled-down value -- it does **not** auto-restart
  a new charge. (An earlier version did, "to stay usable as a standing
  fixture," but that silently reset BT/ET back to ~96°C/200°C
  mid-recording if you didn't stop your app-side roast at exactly the
  right second, corrupting the tail of the `.alog`.) Restart the fake
  process to get a fresh roast for your next test.
- Run any of them with `--quiet` to suppress the per-tick log lines.
- These are test fixtures, not part of the app itself -- they're never
  imported by `backend/`, only run standalone from the repo root (e.g.
  `python -m hardware_fakes.modbus_fz94 ...`) with the same venv.
