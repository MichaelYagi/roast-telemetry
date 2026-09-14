# Direct Modbus Bridge

`modbus_bridge` (`ModbusEngine`) talks straight to a real roaster's PLC
over Modbus RTU (USB/serial), bypassing Artisan entirely. It can also
*control* the roaster (Burner/Air/Drum), since that's just standard
Modbus register reads and writes — the only one of the four data sources
that can, besides the Simulator.

This page covers what's true for *any* Modbus-connected roaster. For a
specific, already-confirmed model, see:

- [FZ-94 (USB)](fz-94-usb.md) — Coffee-Tech Engineering's plain FZ-94,
  the only model this app ships defaults for today.

About to actually connect to a real machine? See the
[real-hardware checklist](real-hardware-checklist.md) — pre-flight
checks, the click-through sequence, and what to do afterward.

## The register map is fully overridable

Nothing about the Modbus wiring is hardcoded to one machine. Every
slave ID, register, and divisor `ModbusEngine` uses — BT/ET/DT/Burner's
own, not just Air/Drum/Burner-range — is a constructor argument, and
every one of those is in turn exposed as a per-roast override in the
Configure Roast form's "Advanced Modbus register map":

| Channel | What it needs |
|---|---|
| BT / ET / DT | Slave ID, register, divisor (each independent) |
| Burner | Slave ID, register, divisor, plus the SV range (°C) the 0–100% UI slider maps onto |
| Air / Drum | Slave ID, control register, frequency register, feedback register, plus the 0–100% operating range |

Leave any of these blank to fall back to whatever defaults are
configured for the selected model (FZ-94's, currently the only shipped
set) — only worth touching once you've confirmed your own unit's actual
register map genuinely differs, e.g. from the VFD drive's own
nameplate/front-panel parameters, or a probe's real slave ID.

## Connection basics that apply regardless of model

- **One serial connection usually handles everything** — temperature
  probes, Burner, and the Air/Drum drives together, at whatever baud
  rate/framing the specific model uses (see that model's own page). A
  genuinely separate second connection (`modbus_control_port`) is
  supported for the uncommon case where a unit's own wiring splits
  drives onto a different physical link — leave it blank unless you've
  confirmed that's actually the case for your machine.
- **Mutually exclusive with anything else holding the same port** —
  Modbus RTU assumes one master per bus. If Artisan (or any other
  software) is already connected to the same serial port, disconnect it
  first; two masters polling the same bus concurrently produces garbled
  reads on both sides, not a clean error.
- **ON connects and starts reading; START begins recording.** These are
  two separate steps for Modbus specifically (see
  [Getting started](../getting-started.md#the-four-data-sources)) —
  clicking ON opens the connection and streams live values immediately,
  with nothing recorded and no roast created yet; a Test Connection panel
  appears at that point to verify reads (and optionally one benign write)
  before you commit to an actual roast.
- **Controls only ever show the machine's real current state.** Since
  Burner/Air/Drum are all genuinely read back from the PLC (not just
  echoed from the last command sent), connecting never writes a
  "starting value" on your behalf — the Controls panel's sliders reflect
  whatever the roaster is actually doing (an operator's own manual
  setting, or state left over from a previous session), and nothing
  changes on the machine until you move a slider yourself.

## Testing Mode

Once connected (armed — see above), a **Test Connection** panel appears
with two options, meant for verifying a real connection is actually
working before you commit to recording a roast:

- **Read-only** — samples every configured channel (BT/ET/DT, Burner SV,
  Air/Drum feedback) for about 4 seconds and checks each one: is it
  present at all, is it within a plausible range (a loose sanity check
  against reading garbage or nothing, not a calibration check), and does
  it vary at all across samples (a real live reading almost always
  jitters slightly; dead-flat isn't necessarily wrong — fine if the
  roaster is genuinely idle — but worth a second look if you expected
  motion). A channel with no data at all shows a neutral warning, not a
  failure — most units don't have every channel wired (DT, for instance,
  is often absent), so "never populated" is frequently the *correct*
  shape, not a fault.
- **Read + write** (only offered when the connection can write at all —
  a pure read-only meter like the MS6514 never gets this option) — does
  everything read-only does, plus one write check: reads Air's own
  current value and writes that exact same value straight back. On an
  idle machine (Air almost always already off) this is a genuine no-op —
  zero physical effect — while still exercising the real write path end
  to end and confirming the round-trip through the feedback register,
  not just that the call didn't error.

**Burner and Drum are never touched by either check, automatically or
otherwise.** Burner because any nonzero setpoint can make the machine's
own bang-bang controller start actually firing heating elements if DT
reads below it; Drum because it's a moving mechanical part. If the write
check passes, an optional, separately-confirmed **"nudge"** is offered
for anyone who wants a *visible* confirmation instead of just a silent
round-trip — it bumps Air by 5% for about 2 seconds, then sets it back,
with its own explicit confirm step and up-front description of exactly
what will happen before it runs.

None of this ever creates a roast record — the whole panel only exists
during the armed-not-recording window, and both checks work purely
through the same connection/command paths a manual Controls-panel
slider adjustment would use.

For the actual procedure — what to check before you even connect, and
what to do once you're done — see the
[real-hardware checklist](real-hardware-checklist.md).

## Testing without real hardware

`modbus_bridge` can be exercised end-to-end — through the app's actual
connection code, not a mock of it — against a fake standing in for the
real PLC. See each model's own page for the specific fake and register
values; [FZ-94 (USB) → Testing without real hardware](fz-94-usb.md#testing-without-real-hardware)
is the one currently available.
