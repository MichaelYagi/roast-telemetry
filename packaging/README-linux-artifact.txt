Roast Telemetry for Linux
===========================

"roast-telemetry" is a 64-bit (x86_64) Linux executable (ELF), built
with PyInstaller -- Python itself and every dependency are bundled
inside it, so nothing else needs installing to run it. It's a single
command-line program -- no installer, no tray icon, no desktop
integration. Open a terminal, run it, and it prints a URL to open in
your browser (http://127.0.0.1:7890 by default).

To run it:

  1. chmod +x roast-telemetry
     (downloads usually lose the executable bit -- this restores it)
  2. ./roast-telemetry
  3. Open the URL it prints in your browser.
  4. Ctrl+C in that terminal stops it.

Options:

  ./roast-telemetry --port 8080          # a different port
  ./roast-telemetry --host 0.0.0.0       # reachable from other devices on your LAN

Your roasts are stored in ~/.local/share/RoastTelemetry (or
$XDG_DATA_HOME/RoastTelemetry if you've set that).

This build is tied to the glibc version of the machine it was built on
(a standard Linux binary constraint, not specific to this app) -- if it
refuses to run with a "GLIBC_2.XX not found" error, your distro is older
than what this build was compiled against; running from source
(scripts/install.sh + scripts/run-server.sh) sidesteps that entirely.
