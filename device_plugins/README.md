# Device plugins

A plugin adds a *protocol* this app doesn't already speak -- a raw-USB
device, a different serial line format, Bluetooth, anything that isn't
Modbus and isn't already one of the built-in bridges (`modbus_bridge`,
`ms6514_bridge`, `aillio_bridge`, `tc4_bridge`).

That's a narrower gap than it sounds. A new *Modbus* roaster needs no code
at all -- see `modbus_bridge/device_profiles.py` and Settings > Device
Profiles, which already let you add one by register facts (slave id,
register numbers, scaling) alone. Reach for a plugin only when the device's
own protocol genuinely isn't Modbus.

## Writing one

1. Copy `device_plugins/examples/example_serial_meter.py` into
   `device_plugins/installed/` and rename it.
2. Write a class matching the `DeviceEngine` shape in `base.py`: `tick`,
   `get_new_events`, `apply_command`, `is_finished`, `status`,
   `reset_detection`, `mark_milestone_fired`, `notify_manual_charge`,
   `close`. Every built-in bridge already has this exact shape --
   `ms6514_bridge/engine.py`'s `MS6514Engine` is the simplest real one to
   read alongside the example.
3. At the bottom of your file, call `register(PluginSpec(...))` with a
   unique `kind`, a `label` for the New Roast form's dropdown, and a
   `connect` function.
4. Restart the server. `GET /api/v1/device-plugins` lists it, and the New
   Roast form shows a "Plugin device" option once at least one plugin is
   installed.

## What a plugin is not responsible for

- **Milestone auto-detection** is optional -- forward `dry_end_c`/
  `fc_start_c`/`detect_milestones` to `roast_heuristics.LiveRoastDetector`
  the way the example does, or skip it; the operator's manual event buttons
  always work regardless.
- **Control** is optional -- set `read_only=True` on your `PluginSpec` for
  a meter with no command channel (`apply_command` becomes a no-op, and the
  control sliders stay hidden for that roast), same as the built-in
  `ms6514_live` mode.
- **A port field** is optional -- set `needs_port=False` for a device with
  nothing to configure (a raw-USB device picked by model name, say), same
  as `aillio_live`. Your `connect(port, ...)` then always receives `port=None`.
- **Store the port on `self.port`** on your engine (the example does) if
  `needs_port=True` -- lets the server clean up a stale connected-but-idle
  session on the same port automatically, the same way every built-in
  serial bridge already does. Not required, just a convention worth
  following.
- **Testing against real hardware** isn't required to write one, but is
  required before you trust it on a real roast -- this project's own
  built-in bridges each say plainly in their docstring whether they've been
  confirmed against real hardware yet.

## Licensing

This project is AGPL-3.0-or-later (see the repo root `CLAUDE.md` and
`LICENSE`). A plugin you write and keep for yourself has no obligations
either way. A plugin you *distribute* to other people is loaded in-process
and calls into this app's own code (`roast_heuristics`, and whatever else
it imports), which is exactly the kind of combination the AGPL has opinions
about -- this isn't something to guess at. If you plan to share a plugin,
license it AGPL-3.0-or-later (or get your own legal advice first) rather
than assume a narrower license is fine. Contributing one back to this repo
needs AGPL-3.0-or-later compatibility like everything else here.
