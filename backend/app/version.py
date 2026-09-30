"""Single source of truth for the app version. backend/app/main.py's
FastAPI(version=...) and scripts/tray_app.py's tray-menu display both
import this instead of each hardcoding their own copy -- kept as its own
tiny, dependency-free module (not just read off `main.py`'s `app`
object) specifically so the tray process doesn't have to import the
whole FastAPI app graph (all routers, storage.py, roast_session, ...)
just to display a version string; see that file's own module docstring
on why it otherwise avoids importing `backend` at all.

Bump this alongside frontend/package.json's "version" and
packaging/roast-telemetry.spec's CFBundleShortVersionString on every
release -- three places, no automated sync between them (npm's
package.json and a PyInstaller spec's plist dict can't import a Python
module).
"""
VERSION = "1.3.9"
