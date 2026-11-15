#!/usr/bin/env python3
"""
beet_scan.py — recursive per-file Beets import for a whole directory tree.

Why this exists instead of just `beet import -A -s <directory>` (which lets
beets do its own recursive walk): found live 2026-09-04 doing the first
full-archive scan of nasty.internal — singleton duplicate-detection keys on
(artist, title), and most raw personal recordings (Zoom recorder dumps,
old cassette conversions, etc.) have NO embedded tags at all, so they all
share the same blank (artist='', title=''). beet_import_safe.sh's post-
import title-backfill-from-filename fix only runs AFTER the whole `beet
import` invocation finishes — but duplicate detection happens live, DURING
that one invocation, against whatever's already in the library at that
instant. Feed beets a directory with a thousand blank-tagged files and
exactly one of them "wins" the (artist='', title='') slot; the other 999
all collide with it and get skipped as false-positive duplicates before
the fixup ever gets a chance to run.

The fix: import ONE file at a time, running the same fixup after each
single import, so every file's title gets uniquified from its own filename
before the NEXT file is even considered for import. Slower (one `beet`
subprocess per file instead of one for the whole tree) but the only way
to get correct results at this scale with untagged content.

Also handles the folder-exclusion that beets' own `ignore:` config would
otherwise apply during ITS internal walk — since we're doing our own
os.walk here instead, bypassing that config, we prune the same names
ourselves (kept in sync with music_beets_config.yaml's ignore: list).

Usage: beet_scan.py <directory> [--dry-run]
"""

import os
import sqlite3
import subprocess
import sys
import time

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aiff", ".aif", ".ogg"}

# Kept in sync with music_beets_config.yaml's `ignore:` list — see that
# file's comment for why each of these is excluded (Ableton's own bundled
# install/library content, third-party sample/VST libraries, not the
# user's own recordings).
IGNORED_DIR_NAMES = {
    "Factory Packs", "Acapellas4U", "Waveforms", "Template Project",
    "User Library", "Clips", "Lessons", "Presets", "Samples",
    "Native Instruments", "Image-Line", "Steinberg", "Roland",
    "System Volume Information", "lost+found",
}

BEETS_DB = "/etc/beets/beets.db"


def fixup_db():
    """Same two fixes as beet_import_safe.sh, run after every single file:
    relative-path repair (defensive no-op under copy:no) and blank-title
    backfill from filename (the one that actually matters here — see the
    module docstring)."""
    conn = sqlite3.connect(BEETS_DB)
    fixed_paths = 0
    for item_id, path in conn.execute("SELECT id, path FROM items").fetchall():
        if not path.startswith(b"/"):
            conn.execute("UPDATE items SET path = ? WHERE id = ?",
                         (b"/music/library/" + path, item_id))
            fixed_paths += 1
    fixed_titles = 0
    for item_id, path in conn.execute(
        "SELECT id, path FROM items WHERE title = '' OR title IS NULL"
    ).fetchall():
        filename = os.path.splitext(os.path.basename(path.decode("utf-8", "replace")))[0]
        conn.execute("UPDATE items SET title = ? WHERE id = ?", (filename, item_id))
        fixed_titles += 1
    conn.commit()
    conn.close()
    return fixed_paths, fixed_titles


def iter_audio_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in place — os.walk respects mutations to dirnames, so this
        # actually stops descent, not just a post-hoc filter.
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIR_NAMES and not d.startswith(".")]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in AUDIO_EXTENSIONS:
                yield os.path.join(dirpath, fn)


# Root of the NASTY mount. A full re-scan of everything under here was run
# once (2026-09-04, 3,572 files, ~2h10m) to catalog the whole existing
# archive — that was a one-time bootstrap, not a repeatable operation.
# Re-running it isn't just slow: beets' SQLite item ids are NOT guaranteed
# stable when a huge batch re-processes files that are already cataloged
# (observed live — a track's id changed from 1 to 1326 across the full
# scan, for reasons not fully root-caused). Any saved /ui/track/{id} link,
# external reference, or engagement/annotation row keyed on an item_id
# breaks if ids shift under it. Going forward: point this at the SPECIFIC
# new subfolder/file(s) that were actually added, never back at the whole
# tree — see MUSIC_STUDIO.md "Adding new recordings".
NASTY_ROOT = "/import/nasty"


def main():
    if len(sys.argv) < 2:
        print("Usage: beet_scan.py <directory> [--dry-run] [--i-know-this-rescans-everything]", file=sys.stderr)
        sys.exit(1)
    root = sys.argv[1]
    dry_run = "--dry-run" in sys.argv[2:]
    confirmed_full_rescan = "--i-know-this-rescans-everything" in sys.argv[2:]

    if os.path.normpath(root) == os.path.normpath(NASTY_ROOT) and not (dry_run or confirmed_full_rescan):
        print(
            f"beet_scan: refusing to scan the whole NASTY root ({NASTY_ROOT}) again.\n"
            f"This re-processes every already-cataloged file and can shift existing\n"
            f"item ids (confirmed live 2026-09-04 — breaks /ui/track/{{id}} links and\n"
            f"anything else keyed on an item_id). Point this at the specific new\n"
            f"subfolder/file(s) you actually added instead — see MUSIC_STUDIO.md,\n"
            f"'Adding new recordings'. Pass --i-know-this-rescans-everything to\n"
            f"proceed anyway (e.g. after adding an ignore-list entry that needs a\n"
            f"clean re-catalog), or --dry-run to just see the file count.",
            file=sys.stderr,
        )
        sys.exit(1)

    files = list(iter_audio_files(root))
    total = len(files)
    print(f"beet_scan: {total} audio files found under {root} (after exclusions)")
    if dry_run:
        return

    start = time.time()
    imported = skipped = errors = 0
    for i, f in enumerate(files, 1):
        proc = subprocess.run(
            ["beet", "import", "-q", "-A", "-s", f],
            capture_output=True, text=True,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if "already in the library" in out:
            skipped += 1
        elif proc.returncode != 0:
            errors += 1
        else:
            imported += 1
        fixup_db()

        if i % 50 == 0 or i == total:
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            eta_min = (total - i) / rate / 60 if rate > 0 else float("inf")
            print(f"beet_scan: {i}/{total} processed "
                  f"(imported={imported} skipped={skipped} errors={errors}) "
                  f"— {rate:.1f}/s, ~{eta_min:.0f} min remaining", flush=True)

    print(f"beet_scan: DONE — {imported} imported, {skipped} skipped as "
          f"duplicates, {errors} errors, out of {total} files")


if __name__ == "__main__":
    main()
