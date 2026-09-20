# Notes for Claude Code

## Licensing: NEVER violate Artisan's license (hard rule)

This project is **AGPL-3.0-or-later**. Parts of it (the `aillio_bridge/`
package) are derived from Artisan, which is also AGPL-3.0-or-later. Treat
Artisan's license as binding on every change you make.

- **Never copy, paste, translate line-by-line, or "lightly reword" Artisan
  source, data files, saved profiles/settings, translations, icons/logos or
  documentation text into this repo.** Implement from protocol facts and
  your own design. If something genuinely must be ported, stop and ask the
  user first; if they approve, keep the original copyright notice and an
  SPDX header, state that the file was modified, and update the derived-code
  paragraph in `packaging/gen_third_party_notices.py`.
- **No third-party or real-world data as fixtures, templates or samples**
  (roast logs, `.alog` exports, machine lists) unless it is the user's own
  or clearly licensed for this use. Build such data from scratch.
- **Never remove or weaken legal notices:** the headers in
  `aillio_bridge/`, `LICENSE`, the derived-code credit and license text in
  `THIRD-PARTY-NOTICES`, and the "Source code" link in the app footer
  (AGPL section 13).
- **Never add terms that restrict what recipients may do** (no EULA or
  "no redistribution" on builds). Anyone given a build must be able to get
  the source.
- **New dependencies must be AGPL-compatible** (MIT, BSD, Apache-2.0, LGPL,
  MPL are fine; no GPL-2.0-only, no proprietary).
- **Don't imply affiliation:** don't use Artisan's name or logo as if this
  were the official tool. Keep Artisan citations out of `docs/` and the
  README (the user's rule); legal attribution belongs only in source
  headers and the notices file.
- **When in doubt, don't, and ask the user.** Reading Artisan's public
  source to understand a file format or protocol is fine; reproducing it is
  not.
