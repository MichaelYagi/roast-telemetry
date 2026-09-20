# Building standalone desktop artifacts

Produces an unsigned, standalone single executable per platform --
`Roast Telemetry.exe` (Windows), `Roast Telemetry.app` (macOS, a real
double-clickable app bundle wrapping a single-file executable inside)
-- no Python/Node install required to *run* the result, just to
*build* it, and (the default mode) nothing else to keep alongside the
file itself. Windows and macOS are the only supported desktop targets
-- see `scripts/tray_app.py`'s own module docstring for why native
Linux desktop isn't (real testing surfaced genuine, unresolvable-from-
this-app's-own-code tray-icon fragmentation across Linux desktop
environments). Must be built natively on each target platform
(PyInstaller has no supported cross-compile path) -- see
`roast-telemetry.spec`'s own header comment for the full design notes,
including the `PACKAGE_MODE`/`-Mode`/positional-arg switch to build a
folder instead (faster startup, no single-exe antivirus-heuristic risk,
just not a single file -- see that comment for the full tradeoff).

**Verified end-to-end via a real headless build in this sandbox**
(WSL2, used only to run PyInstaller and exercise `--run-server` --
never a tray/GUI test, and not itself a supported distribution
target), both modes -- a real `pyinstaller` build was run for each, and
the resulting binary was actually exercised: `--run-server` starts a
genuine server, `/api/health` responds, a user can register, a
`simulator` roast can be created, an `alog_playback` roast against the
bundled sample file works (the one relative-path assumption riskiest to
get wrong -- see "What actually changed" below), and
`ROAST_TELEMETRY_DATA_DIR` correctly redirects where `roasts.db` gets
created. Two real bugs were caught and fixed in the process, both in
the spec/tray_app.py, not the app itself: the spec initially resolved
paths relative to the caller's cwd instead of the spec file's own
location, and the single-file (onefile) mode's server subprocess
initially inherited a stale working directory from whichever process
launched it instead of establishing its own correct one on every fresh
launch (onefile re-extracts to a brand new temp directory each time,
not a stable folder) -- see the spec's own comment and
`_run_server_entrypoint()`'s. Windows and macOS have both since had
real builds on their own hardware too, catching two further real bugs
along the way -- see "What actually changed" below and the git history
for the `pystray`/`pyserial` hidden-import fixes.

## Windows

1. Clone/pull the repo onto the Windows machine.
2. If you haven't already: `scripts\install.ps1` (sets up `.venv-windows` with
   every dependency this app needs to run from source).
3. `packaging\build-windows.ps1` (or `packaging\build-windows.ps1 -Mode onedir`
   for a folder instead of a single file).
4. Output: `dist\Roast Telemetry.exe` -- a genuinely single file, copy
   or send just that, nothing else needed alongside it. (`-Mode onedir`
   instead produces `dist\Roast Telemetry\`, a folder whose
   `Roast Telemetry.exe` needs its `_internal\` folder alongside it --
   zip the whole folder in that case, not just the exe.)
5. Double-click `Roast Telemetry.exe`. Windows SmartScreen will show
   "Windows protected your PC" the first time (this build is unsigned,
   see below) -- click "More info" -> "Run anyway". After that it
   behaves exactly like running the tray from source: an icon appears
   in the system tray, right-click for Start/Stop/Open in browser/Save
   logs/etc.

## macOS

1. Clone/pull the repo onto the Mac.
2. If you haven't already: `scripts/install.sh`.
3. `packaging/build-macos.sh` (also generates `packaging/icon.icns` from
   the existing PNGs the first time it runs, via `iconutil` -- only
   works on macOS, which is exactly where this script runs). Pass
   `onedir` as an argument for a folder-based `.app` instead of the
   default single-file one -- either way the output is still
   `dist/Roast Telemetry.app`, the difference is only what's inside it.
4. Output: `dist/Roast Telemetry.app`. Zip it (or Finder's own
   Compress) to send it somewhere else.
5. Gatekeeper blocks a plain double-click on an unsigned app the first
   time ("can't be opened because Apple cannot check it for malicious
   software," no "Open anyway" button on that dialog). The way past it:
   **right-click the app -> Open -> Open** (a *different* dialog that
   does have an Open-anyway button) -- only needed once per machine.
   After that it runs like any other app: a menu-bar icon appears
   (no Dock icon -- this is a background utility, same as the
   Windows tray, not a normal windowed app), click for the same
   Start/Stop/Open in browser/Save logs menu.

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

Small, additive backend/tray changes, all fully guarded so the existing
from-source dev workflow (`scripts/install.sh`/`.ps1` +
`tray.sh`/`.ps1`) is completely unaffected:

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
  skips the frontend-rebuild check (no npm/source frontend
  exists inside a frozen build, it's already baked in); re-invokes
  itself with a hidden `--run-server` flag instead of shelling out to
  `python -m uvicorn` (there's no separate python.exe bundled -- see
  `_run_server_entrypoint()`'s own comment for the full reasoning); and
  that same entry point sets its own working directory from its own
  `BASE_DIR` right away, rather than trusting whatever directory it
  happened to be launched from -- required specifically for onefile
  mode, where the server subprocess re-extracts to a brand new temp
  directory on every launch, not a stable folder the parent process
  could just tell it about in advance.

## Not attempted here

- **Code signing** (see above).
- **A real installer** (Inno Setup `.exe`, a `.dmg` with drag-to-
  Applications) -- the current output is a plain folder/`.app` you zip
  yourself. A reasonable next step once the raw build is confirmed
  working, not bundled into this first pass.
- **Auto-update.** Every new version is a fresh manual build/download.
- **Linux packages (`.deb`, `.rpm`, AppImage), including for Raspberry
  Pi OS.** Considered and deliberately not built: Linux has no tray icon
  (see above), so the app there is a server you open in a browser, and
  `scripts/install.sh` + `scripts/run-server.sh` (`--yes` for unattended
  setup) already do the real work -- see docs/getting-started.html's
  "Linux / Raspberry Pi (server only)". A package would add three
  formats to maintain across two CPU architectures (arm64 runners aren't
  free for private repos) for little gain. If one were ever added, the
  `.deb` is the only one with a real payoff (a systemd unit, USB/serial
  permissions).
