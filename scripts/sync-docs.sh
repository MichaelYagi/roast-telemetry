#!/usr/bin/env bash
# Publishes docs/ to the public site at https://michaelyagi.github.io/roast-telemetry
# -- a genuinely separate GitHub repo (michaelyagi.github.io) from this
# one, since this repo is private and GitHub Pages can't serve a
# private repo's content directly. Runs automatically in CI on every
# push to main that touches docs/ (.github/workflows/docs.yml) --
# deliberately its own workflow, not folded into packaging/'s build
# scripts (those build platform executables, an unrelated concern) or
# ci.yml (that's PR-triggered too, and a PR isn't merged yet -- nothing
# should publish from one). Also runnable by hand for the same reason
# any CI step is: to test a change or force a sync without waiting on
# a push.
#
# What it does:
#   1. Clones/updates a local copy of michaelyagi.github.io next to
#      this repo (../michaelyagi.github.io by default -- override with
#      MIRROR_DIR).
#   2. Copies every file under docs/ into that clone's roast-telemetry/
#      subdirectory, rewriting each .html file's links to this
#      (private) repo -- the nav bar's own "GitHub" link is dropped
#      entirely, an inline reference like "see CONTRIBUTING.md" is
#      unwrapped to plain text -- so nothing on the public site links
#      somewhere a visitor can't actually reach. Never deletes a file
#      already in the mirror that isn't in docs/ (a deliberately
#      removed docs page needs its mirror copy cleaned up by hand --
#      safer than a sync script silently deleting something it doesn't
#      recognize).
#   3. Commits and pushes, but only if something actually changed.
#
# Usage: scripts/sync-docs.sh
# Also runs in CI (.github/workflows/docs.yml, on every push to main
# that touches docs/) -- set PAGES_DEPLOY_TOKEN (a PAT with push access
# to michaelyagi.github.io; GITHUB_TOKEN's own default permissions
# don't reach outside this repo) to authenticate non-interactively
# instead of relying on whatever git credentials are already configured
# locally. Same script either way -- one code path, not a separate CI
# implementation to keep in sync with this one.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
MIRROR_DIR="${MIRROR_DIR:-$REPO_ROOT/../michaelyagi.github.io}"
if [[ -n "${PAGES_DEPLOY_TOKEN:-}" ]]; then
  MIRROR_REMOTE="https://x-access-token:${PAGES_DEPLOY_TOKEN}@github.com/MichaelYagi/michaelyagi.github.io.git"
else
  MIRROR_REMOTE="https://github.com/MichaelYagi/michaelyagi.github.io.git"
fi

if [[ ! -d "$MIRROR_DIR/.git" ]]; then
  echo "No local clone at $MIRROR_DIR -- cloning it now."
  git clone "$MIRROR_REMOTE" "$MIRROR_DIR"
else
  echo "Updating existing clone at $MIRROR_DIR"
  git -C "$MIRROR_DIR" remote set-url origin "$MIRROR_REMOTE"
  git -C "$MIRROR_DIR" pull --ff-only
fi

# CI has no git identity configured at all by default; a real local dev
# run already has the user's own, left untouched. $CI is set to "true"
# by GitHub Actions automatically -- not something to set by hand.
if [[ "${CI:-}" == "true" ]]; then
  git -C "$MIRROR_DIR" config user.name "github-actions[bot]"
  git -C "$MIRROR_DIR" config user.email "41898282+github-actions[bot]@users.noreply.github.com"
fi

SOURCE_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"

python3 - "$REPO_ROOT/docs" "$MIRROR_DIR/roast-telemetry" <<'PYEOF'
import re
import shutil
import sys
from pathlib import Path

src_dir, dest_dir = Path(sys.argv[1]), Path(sys.argv[2])

# The nav bar's own GitHub link -- a bare link to the repo root, no
# /blob/ path -- gets dropped entirely rather than left as plain text;
# "GitHub" as an unlinked nav item wouldn't make sense.
nav_link_re = re.compile(r'\s*<a href="https://github\.com/MichaelYagi/roast-telemetry">GitHub</a>\n')
# An inline reference (e.g. "see CONTRIBUTING.md") keeps its own text,
# just unwrapped from the link -- the sentence still reads fine without it.
blob_link_re = re.compile(r'<a href="https://github\.com/MichaelYagi/roast-telemetry/blob/main/[^"]*">([^<]*)</a>')

dest_dir.mkdir(parents=True, exist_ok=True)
changed = False
for src_path in src_dir.rglob("*"):
    if src_path.is_dir():
        continue
    rel = src_path.relative_to(src_dir)
    dest_path = dest_dir / rel
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if src_path.suffix == ".html":
        content = src_path.read_text(encoding="utf-8")
        content = nav_link_re.sub("", content)
        content = blob_link_re.sub(r"\1", content)
        new_bytes = content.encode("utf-8")
    else:
        new_bytes = src_path.read_bytes()
    if not dest_path.exists() or dest_path.read_bytes() != new_bytes:
        dest_path.write_bytes(new_bytes)
        changed = True
        print(f"  updated {rel}")

if not changed:
    print("Nothing changed -- docs/ already matches the mirror.")
PYEOF

cd "$MIRROR_DIR"
git add roast-telemetry/
if git diff --cached --quiet; then
  echo "Nothing to commit -- mirror is already up to date."
  exit 0
fi

git commit -m "Sync: docs update

Mirrors roast-telemetry@${SOURCE_SHA}.
"
git push origin main

echo
echo "Pushed. GitHub Pages usually takes a minute or two to rebuild --"
echo "https://michaelyagi.github.io/roast-telemetry"
