#!/usr/bin/env bash
# Fetch third-party browser libraries that are not shipped with the public edition.
#
# ChordSheetJS (the song-sheet renderer in the music studio UI) is GPL-2.0-only. That licence
# cannot be combined with this project's, so the file is never committed to the public repo.
# This script downloads the exact release the UI was built against and refuses anything else.
#
#   scripts/fetch_vendor.sh          # run once before deploying the music studio
set -euo pipefail

if [ -d Infrastructure/integration ]; then STATIC=integration/music_studio_ui/static
else STATIC=integration/music_studio_ui/static; fi
URL="https://cdn.jsdelivr.net/npm/chordsheetjs@18.0.0/lib/bundle.min.js"
SHA256="dd5cefad2dd6e979e518bb473db9583e3341d74c4e1c5d8bdc14fabb4e0b8913"
OUT="$STATIC/chordsheetjs-18.0.0.min.js"

if [ -f "$OUT" ] && echo "$SHA256  $OUT" | (sha256sum -c - >/dev/null 2>&1 || shasum -a 256 -c - >/dev/null 2>&1); then
    echo "ChordSheetJS 18.0.0 already present and verified."; exit 0
fi
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
curl -fsSL "$URL" -o "$tmp"
got=$( (sha256sum "$tmp" 2>/dev/null || shasum -a 256 "$tmp") | cut -d' ' -f1)
[ "$got" = "$SHA256" ] || { echo "Checksum mismatch for $URL (got $got); not installing." >&2; exit 1; }
mkdir -p "$STATIC"; mv "$tmp" "$OUT"; trap - EXIT
echo "Installed $OUT (GPL-2.0-only, keep it out of redistributed copies of this repository)."
