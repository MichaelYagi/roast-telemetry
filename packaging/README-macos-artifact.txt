Roast Telemetry for macOS
===========================

This is an unsigned build (no paid Apple Developer certificate) --
Gatekeeper will block it the first time you try to open it. That's
expected, not a sign anything's wrong.

How to open it depends on your macOS version:

macOS 14 (Sonoma) and earlier
------------------------------
  1. Right-click (or Control-click) "Roast Telemetry.app".
  2. Choose "Open".
  3. Click "Open" again in the confirmation dialog that appears.

macOS 15 (Sequoia) and later
------------------------------
  Apple removed the right-click bypass for this dialog -- instead:

  1. Double-click the app once (it'll be blocked -- that's expected).
  2. Open System Settings -> Privacy & Security.
  3. Scroll down to the blocked-app notice for "Roast Telemetry".
  4. Click "Open Anyway".
  5. Double-click the app again, and confirm when prompted.

Note: this approval is tied to this exact .app, not to "Roast
Telemetry" as a name -- a future version will trigger the same warning
again the first time, even though it's the same app.
