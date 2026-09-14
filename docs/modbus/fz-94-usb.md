# FZ-94 (USB)

Coffee-Tech Engineering's plain **FZ-94** — not the EVO, which connects
over Modbus TCP/Ethernet instead, a genuinely different connection
method this app doesn't support at all. If your unit's cable to a PC is
Ethernet (RJ45) rather than USB, this page doesn't apply to it.

This is the only model this app ships default register values for
today; see [Direct Modbus Bridge](README.md) for what's true of any
Modbus-connected roaster, register overrides included, if you're
pointing this at a different unit.

## Register map

Confirmed against Artisan's own shipped machine preset for this exact
model —
[`FZ94.aset`](https://github.com/artisan-roaster-scope/artisan/blob/master/src/includes/Machines/Coffee-Tech/FZ94.aset)
in [artisan-roaster-scope/artisan](https://github.com/artisan-roaster-scope/artisan)
— and the source that interprets it,
[`modbusport.py`](https://github.com/artisan-roaster-scope/artisan/blob/master/src/artisanlib/modbusport.py),
not just paraphrased from blog write-ups, though those independently
corroborate it:
[BT/ET/DT](https://artisan-roasterscope.blogspot.com/2015/01/connecting-artisan-to-coffee-tech-fz-94.html),
[Burner](https://artisan-roasterscope.blogspot.com/2016/08/fz-94-2-pushing-drum-heat-limit.html),
Air/Drum control writes from
[part 4](https://artisan-roasterscope.blogspot.com/2016/08/fz-94-4-taking-control.html)
specifically, Air/Drum speed *feedback* from
[part 3](https://artisan-roasterscope.blogspot.com/2016/08/fz-94-3-connecting-drives.html)
specifically — different posts in the same 5-part series, not one post
covering both directions.

**One serial connection handles everything** — 19200 baud, 8N2, straight
from the `.aset`'s own `[Modbus]` block (an earlier version of this
assumed the temperature probes and Air/Drum drives needed two separate
connections at different baud rates, based on one blog mid-series; that
was a snapshot of that author's setup *before* they unified everything
onto one bus, not Artisan's actual shipped configuration).

| Channel | Slave | Register | Notes |
|---|---|---|---|
| BT | 11 | 0 | Function code 3, value = temperature × 10 |
| ET | 13 | 0 | Same convention |
| DT | 12 | 0 | Same convention |
| Burner | 12 (shares DT's slave) | 5 | Same ×10 convention (confirmed in source — `modbusport.py`'s `setTarget()` maps the `.aset`'s `SVmultiplier=1` to an actual ×10 multiplier) |
| Air | 1 | control 8192, frequency 8193, feedback 8451 | Value = percent × 100 |
| Drum | 2 | control 8192, frequency 8193, feedback 8451 | Same convention, own slave |

**Burner is a drum-temperature *setpoint*, not a power percentage.** The
roaster's own bang-bang PID switches its 3 heating elements around this
setpoint — the FZ-94's user manual independently confirms 3
separately-switched 1000W elements plus one "Drum heat limit
controller," no per-element percentage control at all. This app maps its
0–100 `heater_pct` UI onto a configurable SV range (default 100–250°C,
comfortably spanning Coffee-Tech's own factory-recommended 190°C
starting point) as its own approximation, not something documented
anywhere. Since it's a holding register, it's also *read back* (same
address as the write) and reported as the PLC's actual current setpoint
— not just an echo of this app's own last command.

**Air and Drum are Delta VFD-L drives** needing two writes each (control
register for run/stop, then frequency register for speed), plus a
separate read-only feedback register reporting the drive's *actual*
current speed — used instead of just echoing the last command back, so a
rejected/misrouted write doesn't silently look identical to a successful
one. This part is blog-sourced only — the `.aset`'s own `[Sliders]`
block ships without a configured write command for it, so it's the
least-confirmed piece of this register map.

`modbus_control_port` exists only for wiring that genuinely needs a
*separate* second connection (uncommon) — leave it blank for the
standard single-cable setup.

## What's actually been verified

Tested against a mocked `pymodbus` client plus actual serial traffic
against `hardware_fakes/modbus_fz94.py` (register math, control-command
clamping, and failure handling all verified this way) — **not real FZ-94
hardware**. The slave IDs/registers/baud rate themselves are from
Artisan's own source; the wire-level RTU behavior against your specific
unit is unverified until you try it.

## Testing without real hardware

`ModbusSerialClient` needs a real OS serial port, so faking it over
plain TCP isn't directly an option for the app itself — instead, `socat`
creates a linked pair of virtual serial ports, one for the fake device,
one for the app to connect to, exactly as if a USB-RS485 cable joined
them. (On Windows, `socat` isn't available — see the note at the bottom
of this section for a TCP-based alternative that works from a native
Windows install.)

```bash
# one-time
sudo apt install socat

# terminal 1 -- create the virtual pair, leave it running
socat -d -d pty,raw,echo=0,link=/tmp/ttyFAKE_ROASTER pty,raw,echo=0,link=/tmp/ttyFAKE_ROASTER_APP

# terminal 2 -- start the fake FZ-94 (one connection handles everything)
cd roast-telemetry && source .venv/bin/activate
python -m hardware_fakes.modbus_fz94 --port /tmp/ttyFAKE_ROASTER

# terminal 3 -- the app itself (see docs/getting-started.md)
PYTHONPATH=. uvicorn backend.app.main:app --port 8000
```

In the app's **Live Roast → Configure Roast** form:
- **Data source**: `Direct Modbus (USB)`
- **Serial port**: `/tmp/ttyFAKE_ROASTER_APP`
- Leave baud rate at its default (19200) and the drive-port field blank
- Click **ON**, then (after checking the Test Connection panel if you
  like) **START**

BT/ET/DT should populate immediately and climb like a real roast; the
Burner/Air/Drum sliders write real Modbus registers the fake decodes and
feeds back into its thermal model, so raising Burner visibly speeds up
ET/BT/DT.

The fake's thermal clock starts on the *first* request it receives, not
when the process launches — so it's fine to leave it running for a while
(setting up socat, configuring the form, clicking ON to just watch
readings) before you click START; Charge happens right when the app
actually starts recording, not whenever the fake happened to start. Once
a fake's simulated roast finishes (Cool End), BT/ET just hold flat at
their final cooled-down value — it does **not** auto-restart a new
charge. Restart the fake process to get a fresh roast for your next
test.

Run it with `--quiet` to suppress the per-tick log lines. This fake is a
test fixture, not part of the app itself — never imported by `backend/`,
only run standalone from the repo root with the same venv.

> **Windows note**: `socat` doesn't exist on Windows. If you're running
> the backend natively on Windows (recommended over WSL2 for a real
> serial connection — see [Getting started](../getting-started.md)),
> either install a virtual-COM-port driver (search "com0com signed
> driver" — the original com0com's driver is unsigned and gets blocked
> by Secure Boot on modern Windows), or run `socat` inside WSL2 as above
> but bridge its second end to a TCP listener instead of a second PTY
> (`socat ... TCP-LISTEN:5020,reuseaddr` in place of the second
> `pty,...` argument) and point the Windows app's Serial port field at
> `socket://127.0.0.1:5020` — pyserial treats that URL as a live
> connection, no virtual COM port needed at all, using WSL2's default
> localhost port forwarding.

### Suggested manual test settings

Right after clicking START, set **Burner=85%, Air=15%** (Drum at the
default 50%) in the Controls panel. This isn't a guess — it's the actual
thermal model run forward, so the timings below are exact for this
setting:

| Event | Type | Time | BT |
|---|---|---|---|
| Charge | auto | 0:00 | 96.0°C |
| Turning Point | auto | 0:45 | 82.0°C |
| Dry End | auto (threshold 160°C) | 3:35 | 160.1°C |
| FC Start | auto (threshold 196°C) | 5:27 | 196.1°C |
| FC End | **click it** | ~6:00 | ~205°C |
| SC Start | **click it** | ~6:30 | ~211°C |
| Drop | **click it, by** | ~6:50–6:54 | ~217–218°C |
| Cool End | **click it, then stop the roast** | by ~9:50 | falling |

Charge/Turning Point/Dry End/FC Start auto-fire from the BT curve
(`roast_heuristics.LiveRoastDetector`) — nothing to click. FC End, SC
Start, Drop and Cool End are judgment calls in real roasting too, so they
stay manual event buttons under the chart; the times above just tell you
*when the underlying fake's own physics hit those points*, so your
manual clicks land in the right place on the curve.

The **Drop** deadline matters more than the others: the fake's internal
physics hit its own drop threshold (218°C) at ~6:50 regardless of
whether you've clicked anything, and BT starts falling on its own after
that — click late and you'll mark Drop on an already-cooling curve.
After ~9:50 the fake's roast is fully finished and BT/ET just hold flat
at their final cooled-down value — click OFF around then to end your
recording cleanly.

Since re-entering the serial port / thresholds every time gets old fast,
use the **"Save this configuration as"** field at the bottom of the form
once you've got it dialed in — it persists to the backend and reappears
in the **"Load saved config"** dropdown next time.
