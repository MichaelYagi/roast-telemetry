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

## Where this lives if you're running the packaged desktop app

Everything below assumes a source checkout, where `device_plugins/installed/`
is just a folder in the repo. The packaged Windows/macOS build doesn't have
a repo to put files in -- its copy of `installed/` lives in the app's own
per-user data folder instead (the same one `roasts.db` and the server log
already live in; the tray icon's **Open data folder** gets you there), with
`example_serial_meter.py` itself refreshed into it on every app start, same
as the source checkout's own copy below. Drop your own new plugin in there
the same way the steps below describe, just in that folder instead of the
repo's.

## Writing one

1. Copy `device_plugins/installed/example_serial_meter.py` to a new
   filename in that same folder and rename it. It ships registered with
   `enabled=False` (see its own module docstring) -- remove that line, or
   flip it to `True`, once your own plugin is ready for Settings > Device
   plugins and the New Roast form to actually show it (see below).
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
   installed *and enabled* (see below).

## Hiding one while you're still building it

`PluginSpec.enabled=False` is your own, in-code switch -- set it while a
plugin is still a work in progress, and it's loaded and registered
normally at startup (so you can still point a roast straight at its
`kind` yourself, e.g. from a script or `curl`, to test it) but never
appears in `GET /api/v1/device-plugins`, Settings > Device plugins, or the
New Roast form, and can't be managed through that endpoint's PUT/DELETE
either. There's nothing for anyone else to see or toggle until you remove
the line (or flip it to `True`) yourself and restart the server.
`example_serial_meter.py` itself ships this way -- real, loaded, fully
working, and invisible until someone copies it to build something real.

## Turning one off, or removing it for good

This section is about Settings' own runtime checkbox -- it only ever
applies to a plugin shipped `enabled=True` (the normal case above). It
lists every such installed plugin with a checkbox -- unchecking one hides
it from the New Roast form and rejects starting a new roast with it,
without deleting the file or restarting the server (an already-running
roast using it keeps going regardless). This is a visibility switch, not
an unload: the file is still imported and registered at startup either
way, same as always.

The same panel's **Delete** button is the real thing: it removes the
plugin from the running server immediately (no restart needed) and
deletes its file from `installed/`, so it's gone even from this list
until the file is put back. There's no undo -- keep a copy elsewhere
first if you might want it again.

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
