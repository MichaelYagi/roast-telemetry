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
    FZ-94, expressed as a built-in **Device Profile** — a named,
    reusable register map covering any number of temperature channels
    and three control mechanisms (setpoint-temperature, VFD drive,
    plain direct-register), so supporting a different roaster brand is
    building/saving a profile, not writing new code. The 26 individual
    flat register-override fields still work exactly as before for
    anyone not using a profile. Up to two extra temperature channels
    beyond BT/ET/DT (e.g. a flue probe) are recorded and charted live;
    the first two round-trip through a real `.alog` export too (a fixed
    capacity in Artisan's own file format).
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
- **Optional automation rules** for Direct Modbus — bind a Burner/Air/Drum
  command, an in-app banner message, and/or auto-marking a later
  milestone (chaining one milestone into another, e.g. "30s after
  Turning Point, mark FC End") to fire automatically on a milestone, a
  BT/ET temperature threshold, or elapsed roast time, with an optional
  extra delay, Artisan-Alarms-style. Burner bindings need an extra
  explicit confirm step; a pending delayed action is cancelled, not
  fired late, if you stop the roast first.
- **Celsius/Fahrenheit display toggle** (Settings → Temperature Unit),
  same idea as Artisan's own Celsius/Fahrenheit Mode — display only,
  everything is still stored and sent as Celsius.
- **Live rate-of-rise, computed from raw BT/ET either way** — real
  hardware doesn't carry roast-milestone events on its own (a PLC or a
  thermocouple meter has no concept of Charge/Dry End/etc.), so every
  milestone is a manual click for Direct Modbus/USB by default, with
  live RoR shown to help time it. Charge/Dry End/FC Start can optionally
  be switched to auto-detect from BT instead (opt-in, off by default —
  manual clicks still work as an override); Turning Point always
  auto-fires either way, since it's a pure observation, not a judgment
  call. (The Artisan Simulator source is the exception — its own thermal
  model auto-fires every milestone, useful as a hands-off demo.)
- **Real hardware fakes** for both live-hardware modes, so the full
  connection code path is testable without owning a roaster.
- **Background Profile overlay** — load any previously recorded, finished
  roast onto the live chart as a dashed BT/ET reference to pace against,
  same idea as Artisan's own Background Profile. Both curves are already
  measured from their own Charge event, so no realignment is needed —
  purely a visual overlay picked per session; it never reads from or
  affects the live roast's own recording, automation, or control state.
- **CSV export and a printable roast report** — every roast (finished or
  still recording) has a spreadsheet-friendly CSV download alongside the
  Artisan-native `.alog` one, plus a "Print report" button using the
  browser's own print-to-PDF (no extra dependency) for a clean, chart-
  included summary sheet.
- **Login, with the first registrant becoming admin.** No built-in
  default account — the first person to register gets immediate admin
  access; everyone after that is pending until the admin allows them
  from the Manage Access page. Every allowed account, admin or not, has
  full access to everything else — no per-feature permissions, just the
  one gate.

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
