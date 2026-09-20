# Roast Telemetry

An API-first coffee roasting platform that reuses Artisan Scope's concepts
(BT/ET/RoR curves, roast events, `.alog` logs), focused entirely on
*direct* connections to a data source — no dependency on a separately
running Artisan instance for any of them.

**Docs**: [michaelyagi.github.io/roast-telemetry](https://michaelyagi.github.io/roast-telemetry)
*(`docs/` here is what gets published to that site automatically on every
change to `main`; GitHub's file browser shows the local copy as raw HTML
source rather than rendering it, so prefer the link above or open the files
locally.)*

## Major features

- **Six interchangeable data sources**, picked per roast:
  - **Artisan Simulator** — a thermal model producing realistic BT/ET/RoR
    curves and auto-detected roast events. No hardware needed.
  - **`.alog` Playback** — replays a real roast log, including events and
    notes, at real or accelerated speed. Reads genuine Artisan-exported
    `.alog` files too, not just this project's own.
  - **Direct Modbus (USB or Ethernet)** — talks straight to a real
    roaster's PLC over Modbus RTU or TCP, bypassing Artisan entirely,
    with full read *and* write control (Burner/Air/Drum). Ships
    configured for Coffee-Tech's FZ-94 (USB) and FZ-94 Evo (Ethernet),
    each expressed as a built-in **Device Profile** — a named,
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
  - **Aillio Bullet (USB)** — talks straight to a real Aillio Bullet over
    its own vendor-specific USB protocol (not Modbus/serial), with full
    Heater/Fan/Drum control. Ships with the R1 model.
  - **TC4+ (USB, aArtisanQ/PID firmware)** — plain ASCII serial commands
    over USB to a TC4+ shield, bypassing Artisan entirely, with real
    Heater/Fan control (no Drum channel on this hardware). DIY hardware
    with no single default wiring, so it has no built-in preset — see
    the table below.
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
- **Real hardware fakes** for Direct Modbus, Direct USB (thermocouple
  meter), and TC4+, so the full connection code path is testable without
  owning a roaster — Aillio Bullet is the one exception (a raw USB
  device, not a serial port or TCP socket; covered instead by thorough
  protocol-level pytest coverage).
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

## Built-in presets

"Load saved config" entries the app ships with, so most people never
type in a register map or port by hand. Alphabetical by preset name;
"Tested on real hardware" means actually connected to and verified
against the physical device, not just protocol-level unit tests or a
fake — everything else here is well-tested in every *other* sense
(real end-to-end tests against `hardware_fakes/`, or thorough
protocol-decoding pytest coverage for Aillio), just not yet against
its real hardware.

| Preset | Company | Model | Data source | Tested on real hardware |
| --- | --- | --- | --- | --- |
| Bullet R1, USB | Aillio | Bullet R1 | Aillio Bullet (USB) | ❌ |
| FZ-94 Evo, Ethernet | Coffee-Tech | FZ-94 Evo | Direct Modbus | ❌ |
| FZ-94, USB | Coffee-Tech | FZ-94 | Direct Modbus | ✅ |
| Mastech MS6514, USB | Mastech | MS6514 | Direct USB (thermocouple meter) | ❌ |

TC4+ has no built-in preset — it's DIY hardware with no single default
wiring/config to ship, unlike the rest of these.

**More machine support is planned over time**, the way Artisan covers a
wide range of roasters/meters. In practice that mostly isn't new
engine code: a new *Modbus* roaster model is usually just a new
[Device Profile](docs/modbus/index.html#device-profiles) (register
numbers as data, not code); a genuinely different protocol (raw USB
like Aillio, plain serial commands like TC4+) gets its own thin engine
sharing the same core contract (`tick`/`apply_command`/`status`/
`close`/`is_finished`) every other data source already implements. If
your roaster or meter isn't listed above, it's very likely addable
without a rewrite.

## Getting started

**Run it on whichever computer is physically connected to the
roaster.** For every USB/serial data source (Direct Modbus RTU,
MS6514, TC4+, Aillio Bullet) the server process needs the OS to see
that serial/USB port directly — it can't reach across a network to a
port plugged into a different machine. LAN access (`Host: 0.0.0.0`)
only controls who can reach an *already-running* server remotely, not
where the server itself has to run. The one exception: Direct Modbus
*over Ethernet* (e.g. FZ-94 Evo) is genuinely network-connected, so the
server can run anywhere that can reach its IP. See
[docs/getting-started.html#lan-exposure](docs/getting-started.html#lan-exposure)
for the full explanation, including the safety side of LAN access.

See [docs/getting-started.html](docs/getting-started.html) for
installation and running it. In short: `scripts/install.sh` once, then
`scripts/run-server.sh` (on Windows: `scripts\install.ps1`, then
`scripts\run-server.ps1`) and open http://localhost:8000. That's the
same on macOS, Linux (Raspberry Pi included) and Windows; there are no
`.deb`/`.rpm`/AppImage packages, the scripts cover it. What every
script does is in the docs' [Scripts
reference](docs/getting-started.html#scripts-reference). Full docs:

- [Getting started](docs/getting-started.html)
- [Direct Modbus Bridge](docs/modbus/index.html), and
  [FZ-94 (USB)](docs/modbus/fz-94-usb.html) specifically
- [Real-hardware checklist](docs/modbus/real-hardware-checklist.html) —
  connecting to an actual machine safely
- [Aillio Bullet R1](docs/aillio/bullet-r1.html) — USB protocol, install
  requirements, known limitations
- [Architecture reference](docs/architecture.html) — layout, storage, API
  summary, assumptions
- [Contributing](CONTRIBUTING.md) — running tests, code style, releasing

## License

Roast Telemetry is free software, licensed under the
[GNU Affero General Public License v3.0 or later](LICENSE) (AGPL-3.0-or-later).
You can use, study, change and share it. If you run a modified version as a
network service, the AGPL requires you to offer its users the source of your
version; the app's footer links to this repository for that reason.
Third-party components keep their own licenses -- see the
`THIRD-PARTY-NOTICES.txt` shipped with each build.
