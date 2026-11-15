# Music Studio Architecture

Living reference for the music catalog, Beets library, and Studio Assistant
web UI. Last verified against the real running system: **2026-09-04**.


> **Moved to studio-01 (2026-11-01):** the whole stack (six containers) now runs on studio-01
> (VM 283 on pve-03, 10.0.30.59), compose dir `/opt/music-studio`, deployed by
> `deploy_studio01.yml --tags music_studio`. gpu-01 runs none of it. Where this page says gpu-01 /
> `10.0.30.50` / `/opt/docker/music-studio` below, read studio-01 / `10.0.30.59` / `/opt/music-studio`.
> See `STUDIO01_MIGRATION.md`.

> **Studio partner (2026-10-24):** DOMO in the studio. A Live-set chronicle, ideas and to-dos,
> studio sessions, and the Studio page in the UI. See `STUDIO_PARTNER.md`.

## Overview

A personal-assistant-grade catalog for original recordings — the "Immich for
music" the homelab didn't have. Three containers on `gpu-01` (10.0.30.50),
Postgres on `pg-01`, and the actual audio files never leave `nasty.internal`
(the legacy SMB NAS) — nothing is copied or duplicated, matching Immich's
"external library" philosophy.

```
nasty.internal (SMB, 10.0.40.30)              gpu-01 (10.0.30.50)
  Multimedia/Music/My Recordings/                music-catalog :8200 ---+
    MP3'd, Recordings, Rekorder,      (ro mount)  music-assistant :8201 |  both talk to
    WaveLab'd, FieldRecordings,      ------------>  music-beets :8337  |  pg-01 (Postgres)
    ZOOM, VoiceRecordings, Cubase,                       ^             |  + each other
    Reason S, Ableton Live/Recordings,                   |             |
    Audio Tape Conversions, ...                    /import/nasty (ro)  |
    (Factory Packs/Presets/Samples/etc             = same SMB mount    |
     EXCLUDED — see "Ignore list" below)                               v
                                                    elementary_particles DB (pg-01)
                                                      music.* schema (sessions/songs/
                                                      annotations/track_engagement/...)
                                                              |
                                                              v
                                    Traefik (web-01) --> music.home.example.org (catalog API)
                                                      --> studio.home.example.org (the actual UI)
                                                      --> beets.home.example.org (tagging engine)
```

## Components

| Component | Where | Purpose |
|---|---|---|
| `music-catalog` (Docker) | studio-01, `/opt/music-studio/` | FastAPI — sessions/songs/tracks/plugins/annotations/track_engagement, proxies+merges Beets' own API |
| `music-assistant` (Docker) | studio-01, same compose project | FastAPI — natural-language query intents (`/music/query`) **and** the Studio Assistant web UI (`/ui/*`) |
| `music-beets` (Docker) | studio-01, same compose project | Beets — library tagging, MusicBrainz fingerprinting, the actual audio-serving endpoint (`/item/{id}/file`) |
| Postgres `elementary_particles` / `music.*` schema | pg-01 (10.0.40.10:5432) | Sessions, songs, tracks, clips, plugins, instruments, references, field recordings, annotations, track_engagement |
| Beets SQLite `beets.db` | studio-01, `/mnt/music-studio/beets-config/` (NFS, nas-01) | The actual per-track catalog (artist/title/tags/path) — **separate store from Postgres**, see "Two separate catalogs" below |
| NASTY SMB mount | gpu-01, `/mnt/nasty-music` → containers' `/import/nasty` | Read-only, PERMANENT — canonical audio storage, Beets streams playback directly from here |
| Traefik route (web-01) | `music.home.example.org`, `studio.home.example.org`, `beets.home.example.org` | Cross-VM routes registered by `deploy_gpu.yml` post_tasks, real Let's Encrypt wildcard cert (not `.internal`/self-signed) |

### Access points

| URL | What | On Homepage? |
|---|---|---|
| `https://studio.home.example.org/ui` | **The actual app** — browse, unified search, ratings, notes, history, inline playback | **Yes** — "Studio Assistant" tile |
| `https://studio.home.example.org/music/query` (POST) | Natural-language query endpoint (voice/chat assistant intents) | No — API only |
| `https://music.home.example.org/docs` | Interactive FastAPI/Swagger reference for the catalog API | No — bookmark directly if needed |
| `https://beets.home.example.org` | Beets' own bundled web UI — tagging/library management, not day-to-day browsing | **Yes**, but demoted — "underlying tagging engine" in its Homepage description |

## Two separate catalogs — why, and how they connect

This is the single most important thing to understand about this system:

- **Postgres `music.*` schema** — a rich Ableton-inspired data model
  (sessions → songs → tracks → clips, plus instruments, plugins, cover-song
  references, field recordings). Currently almost empty except one manually
  created test session — this models *composition/production* structure,
  not the raw audio archive.
- **Beets' own SQLite `beets.db`** — one row per actual playable audio file
  (artist, title, album, path, format...). This is where all 3,462 cataloged
  tracks from the NASTY archive live. Beets has zero concept of "sessions"
  or "songs" — just files.

**These do not sync with each other.** Importing a file into Beets does not
create a Postgres `music.songs` row, and vice versa. The Studio Assistant UI
bridges them at the *query* layer instead: `music-catalog`'s
`/api/v1/tracks*` endpoints proxy Beets' own `/item/*` API and merge in
Postgres `track_engagement` (rating/status/notes) live, per request — no
sync job, no cache, always current. `/api/v1/search/all` (the browse page's
actual search) queries both stores and returns one combined result list,
each item tagged `source: "catalog"` or `source: "beets"`.

If you ever want session/song-level Postgres rows *and* real audio, either
build a bridge that creates a `music.songs` row per interesting Beets item,
or just don't worry about it — the current design was a deliberate choice
to avoid a sync job, not an oversight.

## Storage architecture — nothing is duplicated

- **The recordings archive is canonical.** Originally NASTY
  (`nasty.internal`, SMB); since 2026-09-28 it is NFS from nas-02
  (`nas-02.internal:/Multimedia/Music/My Recordings`, DATAGRID). Same mount
  point and container path as before, so Beets' stored paths didn't change.
  Mounted **read-only** at `/mnt/nasty-music` on gpu-01 (host),
  bind-mounted into `music-beets` (and, since 2026-10-08, `music-catalog`)
  at `/import/nasty`. `music_beets_config.yaml`: `copy: no, move: no,
  write: no` — Beets catalogs files *in place*, never copies, never writes
  tags back (the mount is read-only anyway). Same philosophy as Immich's
  "external library" feature.
- This mount is a **permanent playback dependency**, not a one-time import
  source — `music-beets`'s `/item/{id}/file` endpoint streams directly from
  it on every play. Do not unmount or remove it once anything has been
  cataloged.
- **The nas-01 NFS export** (`/mnt/music-studio`, backing the
  `music_sessions`/`music_field`/`music_midi`/`music_stems`/`music_library`/
  `beets_config` Docker volumes) holds almost nothing — confirmed
  2026-09-04: `beets-config` is 398K (just the SQLite DB + config.yaml),
  everything else is an empty directory. `df` on this mount shows the
  *shared ZFS pool's* overall usage (a well-known ZFS/NFS quirk — other,
  unrelated nas-01 datasets share the same pool), not music-studio's own
  footprint. The 127GB of actual cataloged audio all still lives on NASTY.
- `music_library`/`music_sessions`/`music_field`/`music_midi`/`music_stems`
  exist for the *other* half of the original design (Basic Pitch MIDI
  transcription output, Demucs stem-separation output, uploaded field
  recordings) — see "Not yet built" below; they're currently just empty,
  ready-to-use mount points.

## Database schema (`music` schema, `elementary_particles` DB on pg-01)

| Table | Purpose |
|---|---|
| `sessions`, `songs`, `tracks`, `clips` | Ableton-inspired composition model — session→song→track→clip, each with a `status` enum (draft→in_progress→needs_more→finished→archived) and a single overwritable `notes` column |
| `instruments`, `plugins` | Gear catalog — 10 plugins seeded by default (Ableton builtins + FOSS VSTs: Helm, VITAL, Surge XT, BYOD, Dragonfly Reverb, TDR Nova) |
| `references` | Cover-song wishlist (wishlist→studying→in_progress→covered→arranged→abandoned) |
| `field_recordings` | Raw capture inbox — has `analysis_id`/`midi_path`/`stems_dir` columns for the not-yet-built audio-analysis/Basic-Pitch/Demucs pipeline |
| `library` | A Postgres mirror of "finished tracks" — **currently always empty**, nothing writes to it; `music.search_all`'s `library` arm never returns rows. Not the same thing as Beets' library. |
| `annotations` | **Running notes history** — polymorphic (`entity_type` + `entity_id` as TEXT, no FK), append-only, never overwritten. Covers `session/song/track/clip/reference/field_recording/library/beets_track`. `entity_id` was widened from UUID to TEXT specifically so it could also hold Beets' integer item ids. |
| `track_engagement` | **Per-user current snapshot** — `(item_id, user_id)` PK, `status` (new/reviewing/needs_work/approved), 1-5★ `rating`, single overwritable `notes`, `position_seconds`. Mirrors podcast.home.example.org's `episode_engagement` table exactly. `item_id` is a **Beets** integer id, no FK (separate database entirely). Since 2026-10-08 also carries `audio_id` (→ `audio_files`), set on every write and backfilled nightly — the durable link if Beets renumbers. `annotations` got the same column for `beets_track` rows. |
| `audio_files` | **Stable identity for recordings** (added 2026-10-08), one row per unique *content*: xxh3_128 hash (same algorithm as labcat), size, duration/format. See "Audio identity" below. |
| `audio_locations` | Where each `audio_files` row lives: path (UNIQUE), size + mtime, `hash_source` (`labcat`/`computed`), the **current** `beets_item_id` (refreshed every sync, never a key), `missing_since`. One content can have many locations (230 duplicate paths today). |
| `search_all` (view) | `UNION ALL` across sessions/songs/references/library for `/api/v1/search` — does **not** include Beets tracks or annotations/engagement; `/api/v1/search/all` layers Beets search on top of this for the real unified search |

**`annotations` vs `track_engagement` — both exist on purpose, not
duplication**: `annotations` is a dated trail ("needs a better mix" today,
"fixed" next week, both kept forever); `track_engagement` is your current
overall verdict on a track (one status, one rating, one notes field,
overwritten on every edit). The track detail page shows both: the
engagement form at the top, the annotation history underneath.

## Key files

All under `integration/` in this repo, deployed by
`Roles/music_studio/tasks/main.yml` (Ansible copies each file into
`/opt/docker/music-studio/<service>/` on gpu-01 — these are **bind-mounted**,
not baked into an image):

| File | Role |
|---|---|
| `music_catalog_api.py` | FastAPI — all Postgres CRUD, Beets proxy/merge, unified search, engagement, annotations |
| `music_assistant.py` | FastAPI — NLP intent routing (`INTENTS` list + `_dispatch`) **and** the Jinja2 `/ui/*` web routes + thin same-origin API proxies |
| `music_schema.sql` | Full `music.*` schema — applied via `psql -f` on every deploy (not idempotent CREATE TABLE by default in most of this file; re-running errors on already-existing tables but continues past them, which is how new additions like `track_engagement` get applied to a live DB without a real migration tool) |
| `music_beets_config.yaml` | Beets config — `copy:no/write:no`, trimmed plugin list, `ignore:` list (see below), `paths:` templates. Seeded once (`force: false` in Ansible — never overwritten automatically after first deploy; a config change needs a manual push, see "Deploying changes") |
| `music_studio_ui/templates/{base,browse,history,track}.html` | The actual UI — Jinja2, navy theme, no JS framework |
| `beet_import_safe.sh` | Wrapper: single file → simple import+fixup; directory → delegates to `beet_scan.py` |
| `beet_scan.py` | Recursive per-file scanner — see "Adding new recordings" below, this is the one that matters going forward |
| `song_suggest.py` | Local-LLM (gpu01-primary) song suggestions from folder/file names → `music.song_suggestions`; see `MUSICIAN_PLATFORM.md` |
| `music_studio_ui/templates/{songs,song,suggestions}.html`, `music_studio_ui/static/` | Musician platform pages + vendored ChordSheetJS |
| `music_analysis_worker.py` | Recording analysis (Vamp: chords/beats/key/tempo/sections) — `music-analysis` container; see `MUSICIAN_PLATFORM.md` |
| `audio_identity_sync.py` | Content-hash identity sync — runs in `music-catalog`, nightly via `/etc/cron.d/music-audio-identity-sync` on studio-01. See "Audio identity" below |

## Deploying changes

```bash
# studio-01 since 2026-11-01 (was deploy_gpu.yml on gpu-01).
set -a; source /opt/vault/.env; set +a
cd IaC/Ansible
ansible-playbook -i inventory/services.yml playbooks/deploy_studio01.yml \
  --tags music_studio
```

- `music_catalog_api.py`/`music_assistant.py`/UI templates are **bind-mounted**
  — the role explicitly `docker restart`s the affected container when its
  code/templates actually changed (registered + `when:` on the copy tasks).
  **This was a real bug** (found 2026-08-22): `docker_compose_v2`'s
  `recreate: auto` does not react to a bind-mounted file's *contents*
  changing, only to the compose config itself — without the explicit
  restart, updated code silently kept the old process running.
- `music_beets_config.yaml` has `force: false` in its copy task — **never
  auto-overwritten** after the first deploy, so a config change (like the
  `ignore:` list) needs a manual push:
  ```bash
  scp integration/music_beets_config.yaml ansible@10.0.30.59:/tmp/config.yaml.new
  ssh ansible@10.0.30.59 "sudo cp /mnt/music-studio/beets-config/config.yaml{,.bak-\$(date +%Y%m%d%H%M%S)} && \
    sudo cp /tmp/config.yaml.new /mnt/music-studio/beets-config/config.yaml && \
    sudo chown 1000:1000 /mnt/music-studio/beets-config/config.yaml"
  ```
  A `music-beets` container restart is **not required** for import-time
  config changes (each `beet import`/`beet_scan.py` invocation is a fresh
  CLI process that reads config.yaml fresh) — only needed if you want the
  long-running `beet web` server itself to pick up a change.
- `music_schema.sql` changes are applied automatically on every
  `deploy_gpu.yml` run (post_tasks: copy to pg-01, `psql -f`, GRANT
  `app_user` on the schema). **Any new table needs an explicit GRANT** —
  tables created this way are owned by `postgres` (the psql connection
  user), not the app's actual role; found live 2026-08-21 when
  `track_engagement`'s predecessor 500'd every request with
  `InsufficientPrivilegeError` until the GRANT step was added.

## Web UI (`studio.home.example.org/ui`)

Server-rendered Jinja2, same pattern as podcast/chronicle's UIs — no
separate JS framework or build step. Deliberately fixed **navy blue theme**
(`#0a1930` background, not the system Canvas/CanvasText colors chronicle/
echo/podcast use) added 2026-09-04 so Music Studio reads as visually
distinct at a glance.

| Page | Route | What |
|---|---|---|
| Browse & search | `/ui` | No query: lists all Beets tracks (capped at 50 — **worth raising now the catalog is 3,000+ tracks**, was fine at single digits). With a query: Immich-style unified search across Beets tracks *and* Postgres sessions/songs/references in one result list, each card tagged with its type. A separate "Search Notes" box (searches `track_engagement.notes`) — deliberately placed **above** the main search box, not below the results, so both boxes stay visible without scrolling as the catalog grows (fixed 2026-09-04, was originally at the bottom). |
| Track detail | `/ui/track/{item_id}` | Inline `<audio>` player (speed controls, streams from `beets.home.example.org/item/{id}/file` — the **public** URL, not the internal docker-network address; same Authelia session covers both domains) + engagement form (status/rating/notes) + annotation history underneath |
| History | `/ui/history` | Every `track_engagement` entry across every user, most recent first, filterable by status |

**Engagement writes go through thin same-origin proxies** on
`music-assistant` (`PUT /api/engagement/{id}`, `POST /api/annotations`) that
forward server-side to `music-catalog` — the browser's JS only ever talks
to `studio.home.example.org`'s own origin, so there's never a CORS concern, same
reasoning as podcast's single-app design (we just have catalog/assistant
split into two containers, so this proxy is the seam).

## Adding new recordings — **do not re-scan the whole tree**

The one-time full-archive scan (2026-09-04, 3,572 files, ~2h10m) was a
bootstrap operation, not something to repeat. Two real reasons:

1. **Slow for no reason** — re-processing 3,000+ already-cataloged files to
   pick up a handful of new ones wastes ~2 hours for nothing.
2. **Item ids are not guaranteed stable across a big re-scan of
   already-cataloged content.** Confirmed live: a track's Beets item id
   changed from `1` to `1326` somewhere across the full scan. Any bookmarked
   `/ui/track/{id}` link, or anything else keyed on an `item_id`
   (`track_engagement`, `beets_track`-typed `annotations` rows) breaks if
   the id it was keyed on shifts under it.

**Instead, point the scanner at only the new content**:

```bash
# One new file:
ssh ansible@10.0.30.59 "sudo docker exec music-beets bash /etc/beets/beet_import_safe.sh \
  \"/import/nasty/<path to the new file>\""

# A new folder (recurses, one file at a time — see beet_scan.py docstring
# for why per-file matters: untagged files with blank artist/title all
# collide as false "duplicates" if a whole folder goes through beets' own
# batch duplicate-detection in one pass):
ssh ansible@10.0.30.59 "sudo docker exec music-beets bash /etc/beets/beet_import_safe.sh \
  \"/import/nasty/<new subfolder>\""
```

`beet_scan.py` now has a **built-in safeguard** (added 2026-09-04): it
refuses to run against `/import/nasty` itself (the whole tree) unless you
pass `--i-know-this-rescans-everything` explicitly. Use `--dry-run` first
on any new target to see the file count before committing to a real run.

### Ignore list — what's deliberately excluded

`music_beets_config.yaml`'s `ignore:` (replaces Beets' own default list,
not merged — see the file's comment) excludes, on top of Beets' own
defaults (`.*`, `*~`, `System Volume Information`, `lost+found`):

`Factory Packs`, `Acapellas4U`, `Waveforms`, `Template Project`,
`User Library`, `Clips`, `Lessons`, `Presets` (all found to be Ableton's
own bundled install/library folders — `Ableton Live/` is Ableton's
application data structure, not a folder of the user's own recordings;
only `Ableton Live/Recordings` is real personal content), plus the
top-level third-party sample/VST folders `Samples`, `Native Instruments`,
`Image-Line`, `Steinberg`, `Roland`. `beet_scan.py` prunes the same names
during its own `os.walk` (kept in sync manually — it bypasses Beets' own
directory walk/ignore-config entirely, doing its own traversal for the
per-file duplicate-avoidance reason above).

**If NASTY gains a new top-level junk folder later**, add it to both
`music_beets_config.yaml`'s `ignore:` list *and* `beet_scan.py`'s
`IGNORED_DIR_NAMES` set — they're two independent copies of the same
exclusion list, not read from a shared source.

## Audio identity (Musician Platform phase 0, 2026-10-08)

Beets item ids are not stable (the 2026-09-04 full scan renumbered item 1 →
1326) and paths move (NASTY → nas-02). Everything the musician platform
attaches to a recording — chord/beat/section analysis, lyric alignment,
song links — keys on `music.audio_files.id` instead. See
`IaC/docs/MUSICIAN_PLATFORM.md` for the overall plan.

- **Identity = xxh3_128 over the whole file, hex.** Identical to labcat's
  `consolidate.py full_hash()`, so the two indexes are directly comparable.
- **Seeded from labcat, not re-hashed.** labcat scan 14 (2026-10-06,
  `/DATAGRID/Multimedia`) already full-hashed all 21,973 files under
  `Music/My Recordings`. Exported to
  `/mnt/music-studio/beets-config/labcat_seed.tsv` (path relative to the
  recordings root, size, mtime, hash); 6/6 spot-checks recomputed on gpu-01
  matched, incl. a 2 GB WAV. Seeded run: 3,462 Beets items → 3,232 unique
  recordings, 0 computed, 3 s.
- **Nightly** 04:15 (`/etc/cron.d/music-audio-identity-sync`, log
  `/mnt/music-studio/beets-config/audio_identity_sync.log`): unchanged
  size+mtime → reuse hash (a `stat()` each, ~2 s total); new/changed files are
  hashed (~110 MB/s over NFS). Exit 1 = drift found (see below).
- **Run by hand** after importing new recordings, so they get an identity
  before the next night:
  ```bash
  ssh ansible@10.0.30.59 'sudo docker exec music-catalog python3 /app/audio_identity_sync.py'
  # re-seed from a fresh labcat export (first run / after a big import):
  #   ... audio_identity_sync.py --seed /beets/labcat_seed.tsv [--dry-run]
  ```
- **Drift is reported, never rewritten**: a rating/note whose Beets id now
  resolves to different audio than it was recorded against prints a `DRIFT`
  line. Re-pointing is a deliberate manual step.
- **API**: `GET /api/v1/audio-files/{audio_id}`, `/by-beets/{item_id}`,
  `/by-hash/{content_hash}` — each returns the file plus all its locations.
- **Orphan from the 2026-09-04 renumbering — resolved 2026-10-08**: the
  item-1 rating (owner, 5★ approved) and note ("Consider re-mastering the low
  end") were moved to item 1326 ("130 Breakin' the Beat - Rough Mix") on the
  owner's confirmation, with audio_id set.

## Known issues / history

17 real bugs found building this, worth remembering (see
`~/.claude/projects/.../memory/project_music_studio_deployment.md` for the
full blow-by-blow — this is the condensed version):

1. **Resource sizing**: web-01 had no CPU/mem/disk headroom for this
   service — deployed to gpu-01 instead, matching `service.yaml`'s own
   `deployment_target` comment flagging gpu-vm as preferred for
   Demucs/Basic Pitch.
2. **`music_assistant.py`'s `standalone_app`** was built inside
   `if __name__ == "__main__"` — invisible to `uvicorn
   music_assistant:standalone_app` (the actual startup command). Moved to
   module level.
3. **Healthchecks used `curl`**, absent from `python:3.12-slim`. Switched
   to `python3 -c "import urllib.request..."`.
4. **Beets native deps**: `chroma` (pyacoustid) needs `libchromaprint-dev`;
   transitively pulled `pycairo` (needs gcc+libcairo2-dev) and `pygobject`
   (needs `libgirepository-2.0-dev` + `gobject-introspection`) — none
   buildable on bare `python:3.12-slim`.
5. **`beet web` needs the `web` extra** (Flask) — missing from the pip
   install list, container exited immediately.
6. **replaygain plugin needs the `ffmpeg` binary**, not just a Python
   package.
7. **`beet web` takes positional `[host] [port]` args**, not `--host`/
   `--port` flags.
8. **Beets' own bundled web UI JS bug**: always reads `data['results']`
   from search responses, but an *empty* query hits a different backend
   route keyed `items` — empty-box "browse everything" silently showed
   nothing. Patched via `sed` on every container start (third-party code,
   not ours to edit directly).
9. **Plugin seed data wasn't idempotent**: `UNIQUE(name, manufacturer,
   version)` with `version` nullable — Postgres treats NULL≠NULL, so
   `ON CONFLICT DO NOTHING` never fired. 4 schema re-applies → 40 duplicate
   plugin rows before caught. Fixed: `version TEXT NOT NULL DEFAULT ''`.
10. **Domain switch**: originally on `.internal` (Traefik self-signed
    cert) — switched to the `home.example.org` wildcard per user request; bonus
    fix, this also resolved an Authelia cookie-domain issue that still
    affects `ollama.internal`/`yolo.internal` (`.internal` hosts apparently
    aren't covered by Authelia's session cookie at all).
11. **Relative-path storage**: singleton imports under `copy:yes` stored a
    relative `item.path`, 500ing the player. Fixed retroactively; became
    moot once `copy:no` (scan-in-place) removed the copy/move step
    entirely.
12. **`Content-Disposition: attachment`** on the file-serving endpoint
    forced downloads instead of inline playback. Patched to `inline`
    (same `sed`-on-start pattern as #8).
13. **Blank-title duplicate collision**: singleton dedup keys on
    `(artist, title)`; untagged files (most raw personal recordings) all
    share blank/blank, so only the *first* wins the slot and everything
    else false-positive "duplicates" it. Root cause of both the original
    76-file pilot's low yield and, at scale, why a naive full-tree
    `beet import` doesn't work at all (see #17).
14. **FastAPI route-registration order**: `/api/v1/engagement/{item_id}`
    (typed `int`) registered before `/api/v1/engagement/history` — a
    request for `/history` got captured as `item_id="history"` and 422'd.
15. **A blind scan pulled in commercial content**: `Ableton Live/`'s
    `Factory Packs`/`Acapellas4U` subfolders are Ableton's own bundled
    content and downloaded remix resources, not personal recordings — two
    actual commercial tracks briefly got cataloged before caught. See
    "Ignore list" above.
16. **Item id instability across a full re-scan** — see "Adding new
    recordings" above. The reason that section exists.
17. **`docker_compose_v2` `recreate: auto` doesn't restart on bind-mount
    content changes** — see "Deploying changes" above.

## Not yet built

The musician platform (ChordPro song sheets, alphaTab tab/notation, Chordino /
Sonic Visualiser analysis) is planned in `IaC/docs/MUSICIAN_PLATFORM.md` and
supersedes the audio-analysis / Basic Pitch / Demucs items below — those get
built as part of its analysis worker, not separately.

- **`audio-analysis` service**: `music_catalog_api.py` calls out to it for
  BPM/key/mood on field recordings (`AUDIO_ANALYSIS_URL`) — only a
  `service.yaml` spec exists, never actually deployed. Field recordings
  import fine; that enrichment step just silently no-ops.
- **Basic Pitch (audio→MIDI) / Demucs (stem separation)**: the catalog API
  has `/api/v1/field-recordings/{id}/transcribe` and `/separate` endpoints,
  but the container never installs `basic-pitch`/`demucs` — would fail
  immediately if called.
- **Session/song detail pages** in the new UI — browse renders Postgres
  entities as read-only cards, no drill-in page yet (they're empty except
  one manually-created test session anyway).
- **No delete/reset for a rating once set** — `track_engagement` upserts
  via `COALESCE`, matching podcast's exact pattern: a new value overwrites,
  there's no way to null one back out via the UI.
- **Beets↔Postgres bridge** — see "Two separate catalogs" above; not
  planned unless actually wanted.
