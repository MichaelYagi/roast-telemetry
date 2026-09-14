# Roast Telemetry

An API-first coffee roasting platform that reuses Artisan Scope's concepts
(BT/ET/RoR curves, roast events, `.alog` logs), focused entirely on
*direct* connections to a data source — no dependency on a separately
running Artisan instance for any of them.

**Docs**: [michaelyagi.github.io/roast-telemetry](http://michaelyagi.github.io/roast-telemetry)
*(eventual home for these docs — for now, see [docs/](docs/getting-started.md) in this repo.)*

## Major features

- **Four interchangeable data sources**, picked per roast:
  - **Artisan Simulator** — a thermal model producing realistic BT/ET/RoR
    curves and auto-detected roast events. No hardware needed.
  - **`.alog` Playback** — replays a real roast log, including events and
    notes, at real or accelerated speed. Reads genuine Artisan-exported
    `.alog` files too, not just this project's own.
  - **Direct Modbus** — talks straight to a real roaster's PLC over
    Modbus RTU, bypassing Artisan entirely, with full read *and* write
    control (Burner/Air/Drum). Ships configured for Coffee-Tech's
    FZ-94; every register is overridable for a different Modbus roaster.
  - **Direct USB (thermocouple meter)** — reads a Mastech MS6514 dual
    K-type thermocouple meter straight over USB-serial.
- **Artisan-style connect-then-record flow** for the two live-hardware
  modes — ON connects and streams live readings so you can verify a
  connection before committing to a roast; START begins actually
  recording.
- **A guided Test Connection panel** for verifying a fresh Modbus
  connection safely — comprehensive per-channel reads, plus an optional,
  deliberately benign write check (a no-op round-trip on the least
  consequential channel) before you ever start a real roast.
- **Live event auto-detection** (Charge/Turning Point/Dry End/FC Start)
  from the raw temperature curve, shared by both hardware bridges, since
  neither a PLC nor a thermocouple meter carries roast-milestone events
  on its own.
- **Real hardware fakes** for both live-hardware modes, so the full
  connection code path is testable without owning a roaster.

## Getting started

See [docs/getting-started.md](docs/getting-started.md) for installation
and running it. Full docs:

- [Getting started](docs/getting-started.md)
- [Direct Modbus Bridge](docs/modbus/README.md), and
  [FZ-94 (USB)](docs/modbus/fz-94-usb.md) specifically
- [Architecture reference](docs/architecture.md) — layout, storage, API
  summary, assumptions
- [Contributing](CONTRIBUTING.md) — running tests, code style, releasing
