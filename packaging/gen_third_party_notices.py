#!/usr/bin/env python3
"""Writes THIRD-PARTY-NOTICES.txt for a desktop build: every third-party
package the packaged app actually contains, with its license and the full
license text the package ships with.

Run it with the *build venv's* Python (so importlib.metadata sees exactly
the packages that went into that build -- they differ per platform, e.g.
libusb-package is Windows-only), after `npm ci` (so the frontend's
production dependencies are in frontend/node_modules):

    .venv/bin/python packaging/gen_third_party_notices.py dist/THIRD-PARTY-NOTICES.txt

Why this exists: MIT/BSD/Apache-style licenses require their copyright
notice and license text to accompany copies of the software -- including
the compiled copies sold as a convenience -- and a couple of components
(pystray, libusb) are LGPL, which adds its own notice requirements. This
generates the file from the installed packages' own metadata instead of a
hand-maintained list that would rot the first time a dependency changes.

Not a legal opinion: it reports what each package declares about itself.
"""
from __future__ import annotations

import importlib.metadata as md
import json
import re
import sys
import sysconfig
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Real license texts (copied from each project's own repository) for the few
# packages that ship none in their wheel -- <normalized-name>.txt. Used only
# when the installed package itself provides no license file.
FALLBACKS = Path(__file__).resolve().parent / "license-fallbacks"

# Build/analysis tooling that is not inside the packaged app (only
# PyInstaller's own bootloader is -- handled separately below). Listing
# these would wrongly imply their (sometimes GPL) licenses cover the app.
NOT_BUNDLED = {
    "pip", "setuptools", "wheel", "pkg-resources", "pyinstaller-hooks-contrib",
    "altgraph", "macholib", "pefile", "pywin32-ctypes",
}

LICENSE_FILE_RE = re.compile(r"(^|/)(licen[sc]e|copying|notice|copyright)[^/]*$", re.I)

HEADER = """\
THIRD-PARTY NOTICES

This application (Roast Telemetry) is free software, licensed under the GNU
Affero General Public License, version 3 or (at your option) any later
version; the full text is in LICENSE.txt, next to this file. It bundles the
third-party software listed below. Each of those components is distributed
under its own license; the license text each package ships with is
reproduced in full.

Source code
-----------
The complete corresponding source code for this application, and the build
scripts used to produce this package, are available at
https://github.com/MichaelYagi/roast-telemetry, at the tag matching the
version shown in the app's footer -- so any component below, including the
LGPL-licensed ones, can be replaced with a modified version and the
application rebuilt. If you received this build and cannot reach that
address, ask the person you received it from for the source.

Portions derived from other free software
-----------------------------------------
The Aillio Bullet support (the aillio_bridge package) is derived from the
Aillio R1 driver in Artisan (https://github.com/artisan-roaster-scope/artisan):
Copyright (C) 2010-2026 The Artisan team, represented by Marko Luther and all
contributors; that driver by Rui Paulo, 2023. Artisan is licensed under the
GNU Affero General Public License, version 3 or later, the same license as
this application.

LGPL-licensed components
------------------------
Any component marked LGPL below (for example pystray, and libusb where it is
bundled) is used as an unmodified separate library, under the terms of its
license. Its source is available from its project page (linked in its entry
below) and from PyPI / the project's repository.

PyInstaller
-----------
This application is packaged with PyInstaller. Its bootloader is included in
the executable, under the GNU GPL v2 or later with a special exception that
allows it to be used to distribute applications under any license. See
https://pyinstaller.org/en/stable/license.html

Python runtime
--------------
The executable includes the CPython runtime (PSF License,
https://docs.python.org/3/license.html), which in turn includes components
under their own permissive licenses (e.g. OpenSSL, SQLite, Tcl/Tk, zlib,
libffi, expat). Their notices are available from python.org.
"""


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def license_label(meta) -> str:
    lic = meta.get("License-Expression") or ""
    if not lic:
        raw = (meta.get("License") or "").strip()
        # Some packages paste the whole license text into this field.
        lic = raw if raw and len(raw) < 80 and "\n" not in raw else ""
    if not lic:
        cls = [c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
        lic = "; ".join(cls)
    return lic or "(not declared -- see license text below or the project page)"


def project_url(meta) -> str:
    home = meta.get("Home-page")
    if home:
        return home
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        if label.strip().lower() in {"homepage", "home", "source", "repository", "source code"} and url.strip():
            return url.strip()
    for entry in meta.get_all("Project-URL") or []:
        _, _, url = entry.partition(",")
        if url.strip():
            return url.strip()
    return ""


def python_entries() -> list[dict]:
    out = []
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name or norm(name) in NOT_BUNDLED:
            continue
        texts = []
        for f in dist.files or []:
            if LICENSE_FILE_RE.search(str(f).replace("\\", "/")):
                t = read_text(Path(dist.locate_file(f)))
                if t and t not in texts:
                    texts.append(t)
        if not texts:
            fb = read_text(FALLBACKS / f"{norm(name)}.txt")
            if fb:
                texts.append(fb)
        out.append({
            "name": name, "version": dist.version, "license": license_label(dist.metadata),
            "url": project_url(dist.metadata), "texts": texts, "kind": "python",
        })
    return sorted(out, key=lambda e: e["name"].lower())


def npm_entries() -> list[dict]:
    root = REPO_ROOT / "frontend"
    top = root / "package.json"
    if not top.exists() or not (root / "node_modules").is_dir():
        print("warning: frontend/node_modules not found -- skipping npm packages", file=sys.stderr)
        return []
    seen: dict[tuple[str, str], dict] = {}
    todo = [(dep, root) for dep in json.loads(top.read_text()).get("dependencies", {})]
    done: set[Path] = set()
    while todo:
        name, requirer = todo.pop()
        d = requirer
        found = None
        while True:  # node's resolution: nearest node_modules walking upward
            cand = d / "node_modules" / name
            if (cand / "package.json").exists():
                found = cand
                break
            if d == root.parent or d == d.parent:
                break
            d = d.parent
        if not found or found in done:
            continue
        done.add(found)
        pkg = json.loads((found / "package.json").read_text(encoding="utf-8"))
        lic = pkg.get("license") or pkg.get("licenses") or ""
        if isinstance(lic, dict):
            lic = lic.get("type", "")
        elif isinstance(lic, list):
            lic = " OR ".join(x.get("type", "") if isinstance(x, dict) else str(x) for x in lic)
        texts = []
        for p in sorted(found.iterdir()):
            if p.is_file() and LICENSE_FILE_RE.search(p.name):
                t = read_text(p)
                if t and t not in texts:
                    texts.append(t)
        repo = pkg.get("repository")
        url = repo.get("url", "") if isinstance(repo, dict) else (repo or pkg.get("homepage", ""))
        seen[(pkg.get("name", name), pkg.get("version", ""))] = {
            "name": pkg.get("name", name), "version": pkg.get("version", ""),
            "license": lic or "(not declared -- see license text below or the project page)",
            "url": url, "texts": texts, "kind": "npm",
        }
        todo.extend((dep, found) for dep in pkg.get("dependencies", {}))
    return sorted(seen.values(), key=lambda e: e["name"].lower())


def python_runtime_license() -> str:
    for base in (Path(sysconfig.get_paths()["stdlib"]), Path(sys.base_prefix)):
        for name in ("LICENSE.txt", "LICENSE"):
            t = read_text(base / name)
            if t:
                return t
    return ""


def render(entries: list[dict], title: str) -> str:
    parts = [f"\n{'=' * 78}\n{title}\n{'=' * 78}\n"]
    for e in entries:
        parts.append(f"\n--- {e['name']} {e['version']} ---\nLicense: {e['license']}")
        if e["url"]:
            parts.append(f"Project: {e['url']}")
        if e["texts"]:
            for t in e["texts"]:
                parts.append("\n" + t + "\n")
        else:
            parts.append("(this package ships no license file; the license above is what it declares)\n")
    return "\n".join(parts)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: gen_third_party_notices.py <output-file>", file=sys.stderr)
        return 2
    py = python_entries()
    npm = npm_entries()
    out = [HEADER]
    out.append(render(py, f"Python packages ({len(py)})"))
    out.append(render(npm, f"Frontend (JavaScript) packages ({len(npm)})"))
    rt = python_runtime_license()
    if rt:
        out.append(f"\n{'=' * 78}\nPython runtime license (PSF)\n{'=' * 78}\n\n{rt}\n")
    dest = Path(sys.argv[1])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(out), encoding="utf-8")
    missing = [e["name"] for e in py + npm if not e["texts"]]
    print(f"wrote {dest}: {len(py)} python + {len(npm)} npm packages, {dest.stat().st_size // 1024} KB")
    if missing:
        print(f"note: no license file shipped by: {', '.join(missing)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
