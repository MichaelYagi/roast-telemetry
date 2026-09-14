# Roast Telemetry

An API-first coffee roasting platform that reuses Artisan Scope's concepts
(BT/ET/RoR curves, roast events, `.alog` logs), focused entirely on
*direct* connections to a data source — no dependency on a separately
running Artisan instance for any of them.

**Docs**: [michaelyagi.github.io/roast-telemetry](https://michaelyagi.github.io/roast-telemetry)
*(this repo is private, so GitHub Pages can't be enabled directly on it —
the same content lives in [docs/](docs/index.html) here too, and is kept
in sync manually with the published copy above; GitHub's file browser
shows the local copy as raw HTML source rather than rendering it, so
prefer the link above or open the files locally.)*

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
- **Live rate-of-rise, computed from raw BT/ET either way** — real
  hardware doesn't carry roast-milestone events on its own (a PLC or a
  thermocouple meter has no concept of Charge/Dry End/etc.), and this
  app doesn't guess them from the curve over an operator's own judgment
  either: every milestone is a manual click for Direct Modbus/USB, with
  live RoR shown to help time it. (The Artisan Simulator source is the
  exception — its own thermal model auto-fires milestones, useful as a
  hands-off demo.)
- **Real hardware fakes** for both live-hardware modes, so the full
  connection code path is testable without owning a roaster.

## Getting started

See [docs/getting-started.html](docs/getting-started.html) for
installation and running it. Full docs:

- [Getting started](docs/getting-started.html)
- [Direct Modbus Bridge](docs/modbus/index.html), and
  [FZ-94 (USB)](docs/modbus/fz-94-usb.html) specifically
- [Real-hardware checklist](docs/modbus/real-hardware-checklist.html) —
  connecting to an actual machine safely
- [Architecture reference](docs/architecture.html) — layout, storage, API
  summary, assumptions
- [Contributing](CONTRIBUTING.md) — running tests, code style, releasing
