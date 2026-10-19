# Changelog

All notable changes to Roast Telemetry are documented here, newest first.
One entry per tagged release; dates are the release commit's date.

## [v1.3.29] - 2026-10-18
- Make Settings > Temperature Unit genuinely global: typed threshold/setpoint fields (Dry End/FC Start, Burner SV range, alarm rule thresholds, device profile setpoint ranges, Hold Target) now show and accept the selected unit, not just readouts
- Remove the separate "Controller reports °F" checkbox -- a Direct Modbus connection's hardware-native unit now just follows Settings > Temperature Unit
- Fix simulated-FZ94 tests broken by the above (heater_pct writes through the Burner SV channel, which the global unit now also gates)

(v1.3.28's tag exists in history but was never released -- its backend-tests run failed in CI before the fix above landed; this release supersedes it.)

## [v1.3.27] - 2026-10-14
- Debounce Turning Point detection against a single noisy BT sample
- Document why the SV slider floor isn't 0
- Add BT/ET trend projection and live-sync the phase color bar
- Throttle slider drag sends; allow adjusting Drum/Fan while off
- Keep preheat adjustments on Start; freeze elapsed time at Drop
- Default new connections to safety off, Fahrenheit, away-alarm off

## [v1.3.26] - 2026-10-14
- Add roast renaming (History and roast detail page)

## [v1.3.25] - 2026-10-14
- Fix packaged-build plugin loading; add enable/disable/delete for device plugins

## [v1.3.24] - 2026-10-14
- Stop AI review headings from showing raw JSON key underscores
- Document device plugins and webhooks

## [v1.3.23] - 2026-10-10
- macOS build: ad-hoc sign the .app (required to run at all on Apple Silicon)
- Stage milestone edits with Save/Cancel, add Reset to original recording

## [v1.3.22] - 2026-10-10
- Retry Ollama connection checks before reporting not connected
- Add device plugins and outbound webhooks
- Packaging: ship device_plugins/ in the release zip and desktop build

## [v1.3.21] - 2026-10-06
- Analysis AI comment: computed findings; review card full width

## [v1.3.20] - 2026-10-04
- Let the single-roast chart be dragged taller

## [v1.3.19] - 2026-10-03
- Make every color on the roast chart settable in Settings > Colors

## [v1.3.18] - 2026-10-03
- Show the import summary inline instead of as a popup
- Show an imported log's non-temperature channels as what they are

## [v1.3.17] - 2026-10-03
- Import whole folders, skip duplicate files, configurable bulk limits

## [v1.3.16] - 2026-10-03
- Fix selection count showing a raw placeholder in History

## [v1.3.15] - 2026-10-03
- Fix COM port hints, Fahrenheit-configured controllers, fan/drum reset
- Make the Fahrenheit-native checkbox say what it does, shorter

## [v1.3.14] - 2026-10-02
- Sync the live control sliders' colors with Settings > Colors
- Use Settings > Colors for bulk-exported PDF reports' BT/ET curves

## [v1.3.13] - 2026-10-01
- Add a milestone to a finished roast; grab milestones by their label
- Add milestones on the web, fix overlapping chart labels, keep stats live

## [v1.3.12] - 2026-10-01
- Keep edited milestones the same before and after a restart

## [v1.3.11] - 2026-10-01
- Support multiple named API keys per account
- Mark which API key a request authenticated with
- Persist Emergency Stop and incomplete-roast indicators

## [v1.3.10] - 2026-09-30
- Document Complete vs Stopped vs Aborted roast status
- Rename the "Stopped" roast status label to "Finished"

## [v1.3.9] - 2026-09-30
- Fix Emergency Stop (and every fail-safe/automation write) freezing the server

## [v1.3.8] - 2026-09-29
- Make chart line colors customizable; move color-picking to its own section

## [v1.3.7] - 2026-09-29
- Fix cold-roast milestone edits compounding a synthetic lead-in sample

## [v1.3.6] - 2026-09-29
- Stop series-checkbox/hide-labels toggles from resetting chart zoom
- Default a finished roast's chart view to its Dry/Maillard/Dev span

## [v1.3.5] - 2026-09-29
- Add Alt+drag vertical chart pan; fix zoom resetting on unrelated re-renders
- Fix alternating temp-axis shading flipping during vertical chart pan
- Make chart drag pan freely in any direction, not just horizontal

## [v1.3.4] - 2026-09-29
- Let the chart claim Small Readout's space when no channels are enabled

## [v1.3.3] - 2026-09-28
- Fix "How consistent" table adding +32 to temperature spreads in °F
- Relabel Fan/Drum as % everywhere, add per-session VFD frequency factor/offset

## [v1.3.2] - 2026-09-28
- Fix buggy m:ss duration formatting, share one correct helper

## [v1.3.1] - 2026-09-28
- Log sign-ins/sign-outs to the Activity log, with client platform

## [v1.3.0] - 2026-09-28
- Roaster safety kill switch; Emergency Stop moved to the toolbar; fix a real-DB test leak
- Scripts: --help for run-server.sh, auto npm-install, missing-venv checks
- Top nav: scroll instead of wrapping when the breakout split narrows it
- Show the server's OS in the footer, next to the version
- Import: drop the title override field, fix drop-zone flicker
- History: configurable compare limit, drop "Clear selection", bulk Download
- Raise the Ollama review timeout from 3 minutes to 30
- Lock background scroll while a modal is open
- Version the API: /api/* -> /api/v1/* (clean cutover)
- Settings > History: fix the two fields rendering crammed on one line
- Settings: note which exports follow Temperature Unit and which don't
- Make .alog/JSON/CSV downloads follow Temperature Unit, same as CSV/Excel/PDF already did
- Filter embedding-only models out of the Ollama model dropdown
- Add a standalone Linux build: single command-line binary, no tray
- Simplify Getting Started: one clear path instead of five near-duplicates
- Getting started: give the main setup path its own heading too
- Getting Started: cut prose everywhere, not just Setup & run
- Land on Register for a fresh install, Log in otherwise
- Getting Started: move Built-in presets before The data sources
- Linux artifact README: state the binary type plainly
- Add a dedicated Settings docs page
- Getting Started: link the prebuilt binaries (free during beta)
- Repeating audio alarm if the tab is hidden while actually roasting
- Add a 3-state server/roaster status dot to the app header
- Header status dot: don't count simulator/sim:// sessions as connected
- Remove the per-roast connection dot; add an away-alarm setting
- Settings: put "Disable all of the above" before the away-alarm checkbox
- Remove the Live Roast connection badge -- header dot is the one status indicator now
- Away alarm: chime every 5s instead of 12s -- 12s felt too far apart
- RESET now actually clears the timer and chart, not just the form
- Vertical controls: don't silently swap Burner % back in for Burner SV
- Show when a roast is in progress from any page: nav badge + tab title
- Tray menu: add "Open data folder"
- Revert the Burner/SV fallback change -- it left simulator with no burner control at all
- health(): explicit Cache-Control: no-store
- Rename "Data source" to "Connection type" (Configure Roast, roast detail, docs)
- Nav badge/tab title: force a poll on tab visibility change
- Tray: detect and offer to stop a leftover server before Start
- Show server OS version and LAN IP in the footer and Live Roast page
- i18n infrastructure: Japanese as the first language, Settings > Language
- i18n: convert LiveRoastView.jsx (Configure Roast, live panels)
- i18n: convert SimulatedDeviceHint, RoastToolbar, EmergencyStop
- i18n: convert BeansField, VerticalControlPanel, EventButtonRow
- i18n: convert WeightField, NotesPanel, BackgroundProfilePicker, RoastStatsPanel
- i18n: convert AutoControlPanel, AlarmRulesEditor
- i18n: convert ConnectionTestPanel
- i18n: convert DeviceProfileEditor
- i18n: convert RoastChart
- i18n: convert HistoryDashboard
- i18n: convert RoastDetailView
- i18n: convert AnalysisView
- i18n: convert ComparisonPanel
- i18n: convert the rest of SettingsView
- i18n: convert AccountModal, Modal
- i18n: convert BreakoutSettingsEditor, VerticalControlSettingsEditor
- i18n: convert ActivityView
- i18n: convert ServerFileChooser
- i18n: convert BeansView
- i18n: convert DialogProvider
- i18n: convert RoastReviewCard, BulkZipDownload
- i18n: convert UsersView
- i18n: convert LoginView
- i18n: convert ThemePicker
- i18n: convert AnalysisInsights
- i18n: convert SavedViews
- i18n: convert OutcomePanel, BeansCard, RoastNumbers
- Rename the "Air" channel's display label to "Fan" (matches Artisan's own convention)
- docs: document what OFF, Reset, and reconnecting leave behind
- Make roast notes searchable from History/Analysis (closes a known gap)
- Add a Trends tab to Analysis: rolling-average charts, not a new page
- Stack the Trends tab's charts one per row instead of a grid
- Fix Trends tab chart axes: overshoot past real data, and not lining up
- Explore chart: distinct colors per group, legend-hover glow highlight
- Explore chart tooltip: pair each roast's title with its own beans line
- Explore chart tooltip: lead with the same label the legend shows
- Revert Explore tooltip header back to roast title, not legend label
- Push roast starts to idle tabs instead of leaving them to poll or reload
- Add a Drift tab to Analysis: flag batches off their own bean's baseline
- Reorder Analysis tabs by importance: Drift, Trends, Explore
- Add a Roast Analysis Playbook doc page, linked from Analysis
- Add /roasts/active/stream to the API summary table
- Revert Analysis tab order back to Explore, Trends, Drift
- Feed Drift flags and Trends direction into the Analysis "Ask the AI" prompt
- Compare a roast to its own bean's history in the AI review
- Widen the active-roast push guard beyond exactly phase="idle"
- Fix server status dot landing on its own row in mobile header

## [v1.2.31] - 2026-09-22
- Activity page: fix cramped download links + grey out when empty; link the header title home

## [v1.2.30] - 2026-09-21
- Activity log: roast deletes/edits and safety/control events, downloadable

## [v1.2.29] - 2026-09-21
- History: recalculate the top totals tiles after a delete or import

## [v1.2.28] - 2026-09-21
- Analysis and Beans, AI insights, and roast export/import/PDF

## [v1.2.27] - 2026-09-21
- Data source: keep wrapped values right-aligned with a gap from the label
- Roaster control and safety: limits, emergency stop, fail-safes, repeat a roast, hold a target
- Safety: heater watchdog off by default with clearer wording; safety settings first

## [v1.2.26] - 2026-09-20
- Duration is Charge to Drop; recalculate saved roasts once

## [v1.2.25] - 2026-09-20
- Show negative roast times as -0:06 instead of -1:-6

## [v1.2.24] - 2026-09-20
- Notes: show when each note was written, then roast time and author

## [v1.2.23] - 2026-09-20
- Notes: add, edit and delete during and after a roast

## [v1.2.22] - 2026-09-20
- Data source: keep the weight input and Save button on one line
- Roast detail: let Events and Notes show in full instead of scrolling

## [v1.2.21] - 2026-09-20
- History: always show trends, tidier layout, mobile-friendly cards, import panel

## [v1.2.20] - 2026-09-20
- History: upload .alog files from your computer
- Chart: exact axis tops (350 C / 527 F) and loaded roasts fill the chart

## [v1.2.19] - 2026-09-20
- Chart: draw the control lines low on the temperature axis, start the axis at 0

## [v1.2.18] - 2026-09-20
- Rename the ET rate-of-rise readout to ΔET (RoR) and show it by default
- Rate of rise: average it the conventional way
- Chart: align the default look with the familiar roasting-chart conventions

## [v1.2.17] - 2026-09-20
- Fix the Air/Drum slave ID defaults quoted in the API description
- Simulated devices: start the simulated roast at START, not ON

## [v1.2.16] - 2026-09-20
- Built-in simulated devices: try a machine with no hardware

## [v1.2.15] - 2026-09-20
- CLAUDE.md: how to use other projects' machine presets
- Hide the AI Review box unless Ollama is configured
- AI Review: say which state the roast is in and how to end it
- AI Review: simpler wording for the roast-not-ended hint

## [v1.2.14] - 2026-09-20
- Rename the default chart title from 'Roaster Scope' to 'Roast chart'

## [v1.2.13] - 2026-09-20
- Add CLAUDE.md: never violate Artisan's license
- Fix Artisan rejecting exports as 'Invalid artisan format'
- History: Browse... button to pick an .alog file on the server
- Remove start.sh/start.ps1
- File chooser: drop the app's own roast folders from the shortcuts
- Rename ArtisanToolbar to RoastToolbar
- Neutralize Artisan mentions in frontend comments and one UI string
- Neutralize Artisan mentions across the repo

## [v1.2.12] - 2026-09-20
- tray.ps1: explain a missing Windows venv instead of a raw PowerShell error
- Windows: use .venv-windows so it can share a folder with a WSL/Linux .venv
- Build the Artisan export base in code; drop the copied roast file
- Let the docs publish workflow be run by hand

## [v1.2.11] - 2026-09-20
- Prepare for a public repo

## [v1.2.10] - 2026-09-20
- Let --run-server start even when stdout/stderr are None

## [v1.2.9] - 2026-09-20
- Gate desktop builds on the backend test suite
- Clean up install/run scripts, add unattended install, document all scripts
- Take the tray launchers and standalone builds out of the public docs
- Fix the release source zip: add aillio_bridge, tc4_bridge and LICENSE
- Add a workflow that triggers the private desktop build on each tag
- Desktop builds: Apple Silicon + Intel Mac, Windows 10-11, notices included
- Relicense the project under AGPL-3.0-or-later

## [v1.2.8] - 2026-09-19
- Show the running version at the top of the tray menu

## [v1.2.7] - 2026-09-19
- Add a notice: run the server on the computer connected to the roaster
- Auto-run the read plausibility check the instant a connection arms
- Stop the pass badge from listing every channel by name
- Replace "plausible"/"implausible" with plain language in user-facing text
- Give aillio_live correct Test Connection channel labels and a real write test
- Replace the connection-status text badge with a dot beside the title
- Make missing-field connect errors actually actionable

## [v1.2.6] - 2026-09-19
- Stop GET /review's "no review yet" case from 404ing
- Rename "Launch at login" to "Launch at startup", auto-detect stale builds

## [v1.2.5] - 2026-09-19
- Drop the deprecated unprefixed appearance:slider-vertical
- Actually fix the slider-vertical deprecation warning

## [v1.2.4] - 2026-09-19
- Add -h/--help to scripts/fake-hardware.sh
- Add a built-in-presets/real-hardware-tested table, fix stale docs
- Document the more-machine-support roadmap, add Company/Model columns
- Ship a README in each desktop artifact explaining the OS warning

## [v1.2.3] - 2026-09-19
- Fix macOS artifact losing the .app folder name

## [v1.2.2] - 2026-09-19
- Stop double-zipping the macOS artifact

## [v1.2.1] - 2026-09-19
- Document the release process in the README
- Add MIT license and contributing guide ahead of opening the repo up
- Surface modbus_bridge's control_last_error to the API/UI
- Note the FZ94 EVO's real register scheme for comparison, not application
- Add real Air/Drum speed feedback (register 8451), stop echoing commands
- Auto-reflect real Heater/Fan/Drum state on modbus_live connect
- Make Air/Drum/Burner-SV register map configurable per roast
- Add per-item color customization for the Big Readout Panel
- Add a preset-color palette to the Big Readout Panel color picker
- Remove Artisan Live Bridge and the machine catalog; seed default presets
- Expose BT/ET/DT/Burner's own register map as per-roast overrides too
- Drop specific-hardware examples from mode dropdown labels
- Add Burner SV (raw °C setpoint) as a Big Readout Panel option
- Hide the small ET/BT/DT readout column in split breakout-panel mode
- Relabel the power button to RESET once a roast is finished
- Add an always-visible Small Readout column, independent of the Big Readout Panel
- Fix three Big Readout / Small Readout split-layout bugs
- Highlight the hovered row in the Big/Small Readout enabled-items lists
- Make the chart's height drag-resizable, down to a real minimum
- Fix Small Readout getting squeezed to a sliver between 700-900px wide
- Cap the Big Readout Panel's font-size ceilings for the few-items case
- Fix chart panel not lining up with header/toolbar in mobile layout
- Stop the header changing height on every connect/disconnect
- Auto-detect available serial ports for the Configure Roast form
- Make ON connect+read, START begin recording -- matches Artisan (modbus_live/ms6514_live)
- Add a Testing Mode (read-only / read+write) for the armed-not-recording window
- Handle a failed Air restore in the nudge test explicitly, with a retry
- Split docs out of the README into a docs/ folder
- Document Testing Mode and add a real-hardware checklist
- Convert docs/ from Markdown to static HTML pages
- Add a GitHub Pages deploy workflow for docs/
- Publish docs via michaelyagi.github.io instead of Pages on this repo
- Add app icons: favicon set + header logo
- Fix static files (favicon, icons) 404-ing into the SPA fallback
- Replace LiveRoastView's settings polling with SSE push
- Drop the TURNING POINT button from the event row
- Make Charge/Dry End/FC Start manual for real hardware, not auto-detected
- Add milestone-triggered automation (Artisan-style Alarms) for Direct Modbus
- Expand automation: temperature/time triggers, notifications, DTR% fix
- Upgrade Air/Drum feedback register confidence, confirmed via Artisan's UI
- Add Celsius/Fahrenheit display toggle (Settings -> Temperature Unit)
- Rename the Ollama settings panel heading from "Settings"
- Move AI Roast Review (Ollama) to the last settings panel
- Remove all user-visible mentions of Artisan from the UI
- Fix: roast.status never updated on the frontend after START
- Fix: Turning Point should still auto-fire even with Charge manual
- Add Fahrenheit column to the fake FZ-94's test-settings table
- Add the missing SC End row to the fake FZ-94's test-settings table
- Make Charge/Dry End/FC Start auto-detection opt-in for real hardware
- Let automation rules chain into auto-marking a later milestone
- Add login/registration, with the first account becoming admin
- Add a "Remember me" checkbox to login
- Fix: register-mode hint claimed every signup becomes admin
- Don't reveal in the UI that an admin already exists
- Add live Background Profile overlay to the roast chart
- Add a generic Modbus device-profile system for multi-brand roasters
- Split Configure Roast into tabs, drop two verbose hints
- Stop collapsing Advanced register map / Device profiles / Automation rules
- Add Edit and Enable/Disable to Automation Rules (was delete-only)
- Move Background Profile picker below the chart, not above it
- Make Title required, show it in the Artisan toolbar
- Drop "uncommon" from the Separate drive port field label
- Replace the enabled checkbox with a Disable/Enable button
- Revert "Replace the enabled checkbox with a Disable/Enable button"
- Give the Background Profile picker breathing room above it
- Remove the fixed DRY%/»DRY/»FCs toolbar boxes
- Restructure toolbar: status under title, controls+clock up top, no divider
- Add CSV export and a printable roast report
- Stack the download/print links, one per line
- Ask "did it actually move?" after the nudge, offer Drum as a fallback
- Fix 10 code-review findings: CSV header injection, registration race, silent-fail bugs
- Bump to 1.0.0, show version + build in a footer
- Replace the horizontal Controls panel with vertical Artisan-style sliders
- Fix vertical control stacking: strip padding between grouped sliders
- Fix stacking axis: grouped sliders split one lane's height, not side-by-side
- Chart resize divider now spans the whole row; fix stacked-lane overflow
- Fix stacked sliders overlapping instead of cleanly scrolling
- Drop the vertical control panel's own height floor
- One shared scroll for the whole control panel, not one per lane
- Raise the shared chart-row minimum instead of a control-panel-only floor
- Subtract .panel's own padding/border so the outer box actually matches
- Remove the "Controls are inactive" message from the vertical control panel
- Tighten spacing between vertical control lanes
- Shrink the vertical control panel's overall footprint
- Release an abandoned connected-but-never-started session on reconnect
- Replace the top nav bar with a left-side drawer
- Revert "Replace the top nav bar with a left-side drawer"
- Show Beans + Green weight beside the title in the toolbar
- Default the Title field to the current timestamp (YYYYMMDDHHMMSS)
- Replace window.confirm/window.alert with custom closable modals
- Add per-user API keys (X-API-Key) alongside session-cookie login
- Fix Copy button on plain-HTTP LAN deployments
- Prefix generated API keys with "rt_"
- Bump to 1.1.0
- Update docs for the vertical control panel, API keys, and app icons
- Add a plain-language glossary, and hover tooltips for abbreviated labels
- Add the missing GitHub nav link to glossary.html
- Turn glossary cross-references into real links
- Add a floating "back to top" button to the glossary page
- Fix anchor links landing under the sticky header
- Add convenience scripts for running the server and fake hardware
- Add a PowerShell equivalent of run-server.sh for native Windows
- Add --tcp mode to fake-hardware.sh for a native-Windows app
- Restructure getting-started.html into clear per-platform setup sections
- Add a sticky "on this page" TOC, and a mobile pass on the docs site
- Fix "unbound variable" on macOS's default bash (3.2)
- Add --lan/--host to run-server.sh for LAN access
- Fix --lan silently killing run-server.sh on macOS
- Add a hide-event-labels toggle and mouse/trackpad zoom to the chart
- Fix hide-labels/zoom controls not showing on the Live Roast page
- Disable chart zoom/pan and hide-labels while a roast is recording
- Move chart options into a pull-up menu on the chart itself
- Use a fixed serial port name for fake-hardware.sh, not one per run
- Add self-service password change to Account settings
- Add install.sh/install.ps1 for one-command fresh-clone setup
- Record which user created each roast, show it in History + detail
- Add a system tray icon to start/stop the server (Windows/Mac/Linux)
- Use the 256x256 logo as the tray icon source, not the 64x64 one
- Install tray deps by default, add double-click launchers
- Collapse install + tray into one script/launcher
- Bootstrap Homebrew itself if missing, on macOS
- Redirect tray-started server output to backend/data/server.log
- Document server.log for tray-started runs
- Add Port/Host settings, Restart, Copy URL, Save logs, Launch at login
- Add a double-click launcher for real Linux desktops
- Fix "module '.venv' could not be loaded" across all .ps1 scripts
- Detect a non-Windows .venv instead of crashing on it
- Fix wsl wslpath silently eating backslashes from the Windows path
- Prevent multiple tray instances from running at once
- Fix unresponsive Port/Host/Save-logs dialogs
- Keep the Linux desktop launcher's terminal open after it finishes
- Fix "No such file or directory" on the Linux desktop launcher
- Document that the Linux .desktop launcher needs install.sh run first
- Fix %k field-code usage so the Linux launcher can self-locate again
- Revert to install.sh-baked-in path -- %k confirmed not viable here
- Fix "No module named '_tkinter'" on macOS -- add python-tk@X.Y check
- Make start.sh/ps1 and tray.sh/ps1 check tkinter, not just pystray
- Auto-fix "Cannot install under Rosetta 2" for every brew call
- Replace tkinter with osascript on macOS -- Tk 9.0 crashes hard there
- Stop requiring tkinter on macOS -- it's no longer used there at all
- Fix tray menu not opening on Linux (lxpanel/Raspberry Pi OS)
- Fix Air/Drum slave-ID swap and raise burner SV ceiling to 260C
- Convert the Burner SV slider to the selected temperature unit
- Apply saved-preset starting controls at START; preserve VFD frequency on Off
- Relabel Air/Drum as RPM (not %) everywhere; persist weight_roasted_g
- Add roasted-weight capture and weight-loss % display
- Correct the framing of Drum/Air's slave-ID sourcing -- not blog-wrong
- Catch a Windows-layout .venv when run from WSL2, with a clear message
- Fix Roasted weight edit row overflowing its narrow history-page column
- Add right-click milestone editing: delete and drag-to-retime
- Require Ctrl/Cmd+scroll to zoom the chart, not plain scroll
- Switch chart wheel-zoom from Ctrl/Cmd to Alt -- Ctrl collides with native page-zoom
- Vertically center the live-header meta row (fixes off-center weight input)
- Fix three mobile layout overflow bugs on the Live Roast page
- Give mobile vertical sliders a real min-height instead of shrink-wrapping
- Don't stack lanes on mobile -- every slider gets its own column instead
- Spread mobile control-panel lanes to fill the container width
- Document running the roaster and backend on different machines
- Drum/Air toggle buttons, bigger slider labels, configurable +/- step size
- Decouple slider drag step from the +/- buttons' configured step size
- Air/Drum stop actually reflects in the graph; render as a sharp step, not smoothed
- Fully reset the Drum/Air toggle button so it can't shift the layout
- Give the Drum/Air toggle actual button chrome, not just colored text
- Add a visible divider between 2 stacked sliders sharing one lane
- Add FZ-94 Evo, Aillio Bullet R1, chart persistence, and desktop packaging
- Remove the double-click launchers in favor of packaged builds
- Add scripts/sync-docs.sh to publish docs/ to the public GitHub Pages mirror
- Wire scripts/sync-docs.sh into GitHub Actions
- Build a single-file executable by default for Windows, macOS, and Linux
- Fix "No module named 'pystray'" in built executables
- Fix "could not open serial port 'socket://...'" in built executables
- Fix wrong scripts/ paths for the macOS/Linux build scripts (they're in packaging/)
- Remove native Linux desktop tray/packaging support
- Bundle alog_playback's template JSON in packaged builds
- Make green weight editable at any time, during and after a roast
- Add tags + search/filter to roast history
- Autocomplete the tag input against existing tags
- Add tags editor to the live roast view
- Split the live-header panel's batch info into its own panel
- Paginate History, results-per-page configurable in Settings
- Add a "Roasted by" filter to History
- Make Roasted weight match Green weight's edit/add toggle in the live view
- Revert "Make Roasted weight match Green weight's edit/add toggle in the live view"
- Fix Roasted weight input disappearing once a value is already saved
- Unify weight editing into one shared component, add delete
- Stop WeightField from stealing focus/scrolling on page load
- Fix Charge marker lost on .alog round-trip in live-bridge/simulator roasts
- Add derived roast stats as a reusable endpoint
- Show single-roast derived stats on Live/History detail pages
- Add a "Show trends" toggle to History for cross-roast averages
- Add a numeric stats table and roast identification to the Compare page
- Fix cramped "Curve" label/dropdown spacing on the Compare page
- Add TC4+ (aArtisanQ firmware) live-bridge support
- Add a TC4+ hardware fake for testing without real hardware
- Add TC4+ to the Configure Roast form, Data source panel, and History filter
- Raise Vite's chunk-size warning threshold
- Add TC4+ onboard PID support (PID,SV / PID,ON / PID,OFF)
- Revert "Add TC4+ onboard PID support (PID,SV / PID,ON / PID,OFF)"
- Support a real write test for tc4_live in Test Connection
- Add a GitHub Actions workflow to build the desktop .exe/.app

## [v0.1.0] - 2026-09-13
- Initial commit of roast telemetry backend, frontend, and bridges
- Add AI roast review, breakout panel, hardware fakes, and rebrand to Roast Telemetry
- Add backend pytest suite (44 tests, no prior test coverage existed)
- Show which saved config is loaded after pressing ON, not just in the form
- Add a theme picker (Light, Dark, Coffee, Croissant, Matcha)
- Fix breakout panel horizontal clipping/scrollbar, document storage split
- Write only one .alog format: real Artisan's own native shape
- Fix two more real-Artisan export bugs found by actually test-loading it
- Fix stale roast date shown in Artisan after a successful open
- Compute the temp-axis range per roast instead of leaving it stale
- Fix scope chart temp and control axes lacking explicit ranges
- Fix FZ-94 Modbus register map with real sources; add DT channel
- Add DT to the live-readout column and Big Readout Panel
- Fix Modbus connection architecture using Artisan's own shipped preset
- Add Garbagefire theme, ported from mcp-platform's themes.css
- Make Garbagefire actually offensive; add Maillard%/Dev% breakout panels
- Fix theme dropdown click-through and breakout panel text overflow in split layout
- Add CI and tag-gated release workflow
