# Hardware fakes

Standalone scripts that stand in for real roasting hardware, so
`modbus_live`, `artisan_live`, and `ms6514_live` can each be tested
end-to-end through the app's actual connection code without owning a
roaster. All three are driven by the same roast physics as the app's own
"Artisan Simulator" mode (`_thermal.py`, wrapping `simulator.SimulatorEngine`)
so BT/ET behave like a real roast no matter which one you're using.

| Fake | Stands in for | Protocol | Needs a virtual serial port? |
|---|---|---|---|
| `modbus_fz94.py` | Coffee-Tech FZ-94 (plain, not EVO) | Modbus RTU (hand-rolled framing) | Yes — one (two only for unusual wiring) |
| `weblcds_server.py` | A running Artisan (WebLCDs) | WebSocket/JSON | No — plain TCP |
| `ms6514_device.py` | Mastech MS6514 meter | Raw 18-byte serial frames | Yes |

## WebLCDs fake (easiest -- no serial setup needed)

```
python -m hardware_fakes.weblcds_server --port 8080
```

In the app, choose **Artisan Live Bridge**, host `127.0.0.1`, port `8080`.

## Modbus + MS6514 fakes -- virtual serial port setup

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

One serial connection handles BT/ET/DT/Burner *and* Air/Drum together --
confirmed against Artisan's own shipped machine preset for this model
(19200 baud, 8N2). A genuinely separate second connection is only needed
for unusual wiring; skip `--drive-port` below unless you specifically
need that.

Terminal 1 -- create the virtual pair, keep it running:

```
socat -d -d pty,raw,echo=0,link=/tmp/ttyFAKE_ROASTER pty,raw,echo=0,link=/tmp/ttyFAKE_ROASTER_APP
```

Terminal 2 -- start the fake slave:

```
python -m hardware_fakes.modbus_fz94 --port /tmp/ttyFAKE_ROASTER
```

In the app, choose **Direct Modbus (FZ-94, USB)**, serial port
`/tmp/ttyFAKE_ROASTER_APP` (the *other* end of the pair), and leave the
drive-port field blank. Once connected, the Heater/Fan/Drum sliders write
real Modbus registers that this fake decodes and feeds back into the
thermal model -- so turning the burner up should actually show BT/ET/DT
climbing faster.

If you specifically need to test the separate-connection case
(`modbus_control_port`), add a second virtual pair and pass
`--drive-port` to the fake -- see `python -m hardware_fakes.modbus_fz94
--help`.

The thermal clock starts on the fake's *first* received request on
*either* bus, not at process launch -- it's fine to leave it sitting
idle for a while before connecting; Charge happens right when the app
actually starts talking to it.

### MS6514

Same pattern, different pair:

```
# terminal 1
socat -d -d pty,raw,echo=0,link=/tmp/ttyFAKE_METER pty,raw,echo=0,link=/tmp/ttyFAKE_METER_APP

# terminal 2
python -m hardware_fakes.ms6514_device --port /tmp/ttyFAKE_METER
```

In the app, choose **Direct USB (Mastech MS6514)**, serial port
`/tmp/ttyFAKE_METER_APP`. This one's read-only in real life too, so
there's nothing to control -- just BT/ET streaming in.

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
