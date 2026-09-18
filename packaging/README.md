# Building standalone desktop artifacts

Produces an unsigned, standalone Windows folder, a macOS `.app`, and a
Linux folder -- no Python/Node install required to *run* the result,
just to *build* it. Must be built natively on each target platform
(PyInstaller has no supported cross-compile path) -- see
`roast-telemetry.spec`'s own header comment for the full design notes.

**Verified end-to-end on Linux** (the sandbox this was built in) -- a
real `pyinstaller` build was run, and the resulting binary was actually
exercised: `--run-server` starts a genuine server, `/api/health`
responds, a user can register, a `simulator` roast can be created, an
`alog_playback` roast against the bundled sample file works (the one
relative-path assumption riskiest to get wrong -- see "What actually
changed" below), and `ROAST_TELEMETRY_DATA_DIR` correctly redirects
where `roasts.db` gets created. One real bug was caught and fixed in
the process (the spec initially resolved paths relative to the caller's
cwd instead of the spec file's own location -- see the spec's own
comment). **Windows and macOS still need their own first real build**
to confirm (no such environment available here) -- the design is the
same across all three platforms and the trickiest part (path
resolution inside the frozen bundle) is now confirmed correct on one
of them, but treat the Windows/macOS builds as a first real test on
those specific platforms, not a formality.

## Windows

1. Clone/pull the repo onto the Windows machine.
2. If you haven't already: `scripts\install.ps1` (sets up `.venv` with
   every dependency this app needs to run from source).
3. `packaging\build-windows.ps1`
4. Output: `dist\Roast Telemetry\` -- a folder, `Roast Telemetry.exe`
   inside is the double-clickable launcher. Zip the whole folder to
   send it somewhere else; it needs everything alongside the exe, not
   just the exe by itself.
5. Double-click `Roast Telemetry.exe`. Windows SmartScreen will show
   "Windows protected your PC" the first time (this build is unsigned,
   see below) -- click "More info" -> "Run anyway". After that it
   behaves exactly like running the tray from source: an icon appears
   in the system tray, right-click for Start/Stop/Open in browser/Save
   logs/etc.

## macOS

1. Clone/pull the repo onto the Mac.
2. If you haven't already: `scripts/install.sh`.
3. `scripts/build-macos.sh` (also generates `packaging/icon.icns` from
   the existing PNGs the first time it runs, via `iconutil` -- only
   works on macOS, which is exactly where this script runs).
4. Output: `dist/Roast Telemetry.app`. Zip it (or Finder's own
   Compress) to send it somewhere else.
5. Gatekeeper blocks a plain double-click on an unsigned app the first
   time ("can't be opened because Apple cannot check it for malicious
   software," no "Open anyway" button on that dialog). The way past it:
   **right-click the app -> Open -> Open** (a *different* dialog that
   does have an Open-anyway button) -- only needed once per machine.
   After that it runs like any other app: a menu-bar icon appears
   (no Dock icon -- this is a background utility, same as the
   Windows/Linux tray, not a normal windowed app), click for the same
   Start/Stop/Open in browser/Save logs menu.

## Linux

1. Clone/pull the repo.
2. If you haven't already: `scripts/install.sh`.
3. `scripts/build-linux.sh` (the build itself works fine from WSL2/any
   Linux with no display -- PyInstaller doesn't need one -- but see
   step 5).
4. Output: `dist/Roast Telemetry/` -- tar/zip the whole folder to send
   it somewhere else; `Roast Telemetry` inside is the launcher.
5. Needs a **real Linux desktop** to actually show the tray icon
   (pystray needs GTK/AppIndicator/Ayatana) -- a bare WSL2 shell without
   WSLg won't display one even though the binary itself runs fine (this
   is exactly why `scripts/tray_app.py`'s own module docstring already
   scopes WSL2 to the terminal-based `install.sh`/`fake-hardware.sh`
   workflow, not the tray). No SmartScreen/Gatekeeper-style warning on
   Linux -- it just runs.

## Why unsigned, and what that costs

Code signing needs a paid Apple Developer account (~$99/yr) and a
Windows code-signing certificate (also a real recurring cost, or a much
cheaper but still non-trivial-to-set-up path for open-source projects).
Unsigned just means the two OS-level warnings above on *first* run per
machine -- not a functional limitation, and not something a build
config can fix without an actual certificate. Worth revisiting if this
ever gets wide, non-technical distribution; not needed to have a real,
working, shareable build today.

## What actually changed to make this possible

Two small, additive backend/tray changes, both fully guarded so the
existing from-source dev workflow (`scripts/install.sh`/`.ps1` +
`start`/`tray.sh`/`.ps1`) is completely unaffected:

- `backend/app/storage.py`: `DATA_DIR` now reads a
  `ROAST_TELEMETRY_DATA_DIR` env var if set, falling back to today's
  source-tree-relative path otherwise (which is what every existing
  dev/CI invocation still gets, since that env var is never set there).
- `scripts/tray_app.py`: detects `sys.frozen` (only ever true inside a
  PyInstaller build) and, only in that case: points logs/config/lock
  and the server's own `DATA_DIR` at a real per-user directory
  (`%APPDATA%\RoastTelemetry` on Windows, `~/Library/Application
  Support/RoastTelemetry` on macOS) instead of a path next to the
  executable (which may not even be writable, e.g. Program Files);
  skips the "rebuild frontend on start" step (no npm/source frontend
  exists inside a frozen build, it's already baked in); and re-invokes
  itself with a hidden `--run-server` flag instead of shelling out to
  `python -m uvicorn` (there's no separate python.exe bundled -- see
  `_run_server_entrypoint()`'s own comment for the full reasoning).

## Not attempted here

- **Code signing** (see above).
- **A real installer** (Inno Setup `.exe`, a `.dmg` with drag-to-
  Applications) -- the current output is a plain folder/`.app` you zip
  yourself. A reasonable next step once the raw build is confirmed
  working, not bundled into this first pass.
- **Auto-update.** Every new version is a fresh manual build/download.
