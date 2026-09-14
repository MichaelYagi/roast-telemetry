# Real-hardware checklist

A practical, low-risk procedure for connecting to a real Modbus roaster
for the first time — especially useful if it's not your own machine, and
your time with it (and its operator) is limited. Nothing here requires
touching the machine's settings or creating a roast record; see
[Testing Mode](README.md#testing-mode) for what the actual checks do and
why the one write check is designed to be as benign as possible.

If you can, **rehearse the click-through sequence against a fake first**
— see [FZ-94 (USB) → Testing without real hardware](fz-94-usb.md#testing-without-real-hardware)
for a fully offline setup using this app's own hardware simulator, so
none of the steps below are unfamiliar when you're actually standing at
the machine.

## Before you go

1. **Download common USB-serial driver installers ahead of time** — CH340,
   FTDI VCP, CP210x (Silicon Labs' "Universal Windows Driver" if you're
   on Windows), and PL2303 cover the vast majority of USB-RS485 adapters
   and cables. You often can't identify the exact chipset in advance
   (especially if you're planning to use a cable that's already part of
   the machine's own setup, run by different software with its own OS
   and driver stack — that tells you nothing about whether *your*
   computer already has a driver for it). Having installers ready offline
   means you're not dependent on internet access on-site.
2. If the driver package turns out to be a raw `.inf`/`.sys` bundle
   rather than a setup executable (common for Silicon Labs' CP210x
   package), you don't need to run it now — just have it extracted
   somewhere you'll remember. There's nothing to install without the
   actual device plugged in yet; on the day, if Device Manager shows an
   unrecognized device, point **Update driver → Browse my computer for
   drivers** at the extracted folder with **"Include subfolders"**
   checked, and Windows finds the right one itself.
3. Decide whether you're using **the machine's own existing
   adapter/cable** (unplugging it from whatever it's currently connected
   to and plugging it into your machine instead) or **bringing your own
   separate adapter**. The former needs no extra hardware, but its driver
   working on *its* current computer says nothing about whether it'll
   work on *yours* if the two run different operating systems. The
   latter needs you to check ahead of time that your own adapter's
   connector actually matches what the roaster expects, and that its
   driver already works on your machine (plug it in, confirm it shows
   up as a serial port before you go).

## Pre-flight (at the machine)

1. **Machine is cold and empty** — no beans, no active roast, nothing
   preheating. This makes every following step zero-risk even if
   something turns out to be misconfigured.
2. **Confirm the connection type before anything else.** Look at
   whatever cable currently connects the roaster to its existing
   software (often Artisan). USB (or a USB↔RS-485 adapter inline) →
   proceed. A network/Ethernet cable → stop — that's a fundamentally
   different connection method (Modbus TCP, not RTU) that this app
   doesn't support at all, no amount of configuration will make it work.
3. **Tell the operator what's about to happen**, specifically calling out
   the one thing that could actually change anything:
   > "I'm going to connect using [my own laptop / the existing cable] for
   > a few minutes. First I'll just watch the readings come through — BT,
   > ET, that kind of thing — to make sure it's reading correctly. That
   > part can't change anything. If it looks good, there's one more
   > check I could run that actually sends a command — it'd briefly bump
   > the air/cooling fan up a little, then set it right back. Not the
   > heat, not the drum, just the fan, for a couple seconds — I'll ask
   > you separately before doing that one."
4. **If using the existing cable**: disconnect it from whatever it's
   currently plugged into, plug it into your machine instead. If using
   your own adapter: connect it to the roaster's Modbus terminals/port
   (confirm polarity if it's a bare A/B screw-terminal connection — RS-485
   wired backwards just fails silently to communicate, it isn't
   dangerous, but it can look like a bigger problem than it is).
5. **Confirm the OS sees it as a serial port** (Device Manager on
   Windows, or check `/dev/tty*`/`ls /dev/serial/by-id` on Linux/macOS)
   before opening the app. Install a driver from what you prepared
   earlier if it's not recognized.
6. **Confirm it's one connection, not two** — ask whether the drives
   (Air/Drum) are wired through a separate serial link from the
   temperature probes and Burner. A single connection for everything is
   the standard, and this app's default assumption; if this particular
   unit is wired differently, you'll need the second-port field too (see
   [Direct Modbus Bridge](README.md)).

## The test

1. Live Roast → Data source: **Direct Modbus (USB)**.
2. Serial port: pick it from the dropdown (⟳ to refresh if you connected
   after the page was already open), or type it in directly.
3. Leave baud rate at its model default, leave the drive-port field
   blank (unless step 6 above told you otherwise).
4. Click **ON**.
   - A red error banner → stop, read it (a bad port, or the port still
     held by whatever was using it before, are the usual causes), fix,
     and retry.
   - No error → you're connected ("armed") — the Test Connection panel
     appears.
5. Click **"Run read-only test"**. About 4 seconds. Every row should show
   a green check. A neutral gray mark on a channel is fine if that
   channel genuinely isn't wired on this unit (see
   [Testing Mode](README.md#testing-mode)).
6. If that looks good and you want write confidence too, click **"Run
   read + write test"** instead (or run it after) — same reads, plus one
   no-op write, confirmed via its own feedback register.
7. Optional: the visible "nudge" confirmation, only after asking the
   operator specifically, separate from the general heads-up in step 3
   of Pre-flight.
8. Decide here: if that's everything you needed, skip to post-flight. To
   actually record a roast, click **START**.

## Post-flight

1. Click **OFF** (or **STOP** if you started an actual roast) —
   don't just close the tab; that leaves the connection open longer than
   it needs to be.
2. Glance at the Controls panel — it always reflects the machine's real
   current state, not a guess — and confirm Air/Burner/Drum look like
   resting values.
3. Check History: a roast record only exists if you clicked START at
   some point. Delete it if it was only ever a connectivity test.
4. Disconnect and hand the port back exactly as you found it — reconnect
   whatever software/cable arrangement was there before you started.
5. Confirm the original setup (e.g. Artisan) reconnects and reads
   correctly, before you consider the visit done.
6. Worth noting for next time: anything that read implausibly, any
   channel that needed a register override, anything about the physical
   setup that wasn't what you expected going in.
