# Field recordings: Zoom H2n, Zoom R16, Tascam, iPhone Voice Memos → Music Studio

Cards from the field recorders come into Music Studio through the Mac. A card is only erased
once two checks pass. Studio then turns the takes into sessions it has analyzed and enriched.
Everything stays in the house: the Mac, nas-02, studio-01, and Whisper on gpu-02.

```
 SD card ──field import──▶ ~/Music/Field/<device>/<batch>/   check 1: sha256 copy = card
                              │ Syncthing (send only → receive only, deletes ignored)
                              ▼
              nas-02 My Recordings/Field Recordings/<device>/<batch>/   (final place, never moved)
                              │ field-ingest (studio-01) re-hashes vs the manifest   check 2
                              ▼
   card: only the copied files deleted ── then: register → Beets (one folder) → analysis
                                           → spoken notes (Whisper) → sessions → /ui/field
   Mac copy: kept until "processed" ── field clean
```

## Household decisions (2026-10-25)

| Question | Decision |
|---|---|
| Where it lands | **nas-02** (DATAGRID: Syncthing, Studio, snapshots and the cold-drive backup are there) |
| How import starts | A notification when a card mounts; **you run `field import`** |
| Sessions | **A new session after 30 minutes without recording**, per recorder |
| Spoken notes | **Yes.** Say "take three, chorus idea…" at the start of a take |
| Old recordings | **Backfilled:** ZOOM/, FieldRecordings/ and the R16 projects in Projects/, in place, nothing copied |
| H2n vs R16 | The R16 is **multitrack**: one project = one **live session** (up to 8 tracks at once). A handheld (H2n/Tascam) session may **join** an R16 session, automatically when it overlaps in time or by hand on its page. **Never the other way round.** |

## What the files carry (FOUND 2026-10-25)

| Recorder | On the card | Metadata in the WAV |
|---|---|---|
| Zoom H2n | `STEREO/FOLDERnn/ZOOMnnnn.WAV`; 4-channel mode `4CH/FOLDERnn/SRnnnMS.WAV` + `SRnnnXY.WAV` (= **one take**, two files) | `bext`: originator "ZOOM Handy Recorder H2n" + the recorder's clock. Often **2011-01-01**, meaning the clock was never set |
| Zoom R16 | `PROJnnn/AUDIO/MONO-nnn.WAV` (+ `STE-nnn`), project files `PRJDATA.ZDT`, `HST_nn.ZDT` | **nothing** (no `bext`), file dates **2010-01-01** |
| Tascam DR-07MK2 | `MUSIC/*.wav` | `bext` "TASCAM PCM Recoder DR-07MK2", clock **2000-01-01** |
| iPhone Voice Memos | in the Finder backup, read by `field memos` | `.m4a`. The title and start time come from Voice Memos' own database (CloudRecordings.db). The file's `mvhd` time is when the memo **ended**, in UTC: "20120409 230055.m4a" (336 s) says 03:06:31 UTC, i.e. US Eastern. Older names `yyyymmdd hhmmss` / `yyyymmdd-hhmmss` use the phone's local clock |

**Times:**
- A clock reading before 2012 counts as "not set". The card's file time is then used as an
  approximate end of the take, shown with ≈ in the UI.
- With no time at all, takes group by folder.
- **Set the clocks on the H2n and the R16.** Only then can sessions be cut by time, and H2n
  takes join an R16 session automatically.

**Backfill, first run:**
- 342 files: 215 H2n, 77 Tascam, 50 R16 tracks (7 live sessions).
- 67 sessions in all.
- 144 takes with a set clock.
- 353 of the 371 cataloged were already analyzed (key, tempo, chords).
- Voice Memos (added the same day): 25 from `FieldRecordings/` (2012) and `VoiceRecordings/` (2020),
  all with real times. The m4a files inside `Friends/` and `ABOM/` project folders are left out.

## Mac

See MACOS_STUDIO_SETUP.md, section 7. In short:

| Command | Does |
|---|---|
| `field import` | Finds the card (H2n: STEREO/4CH/MS/SPATIAL; R16: PRJDATA.ZDT; Tascam: MUSIC/) and copies everything into `~/Music/Field/<device>/<date time card>/`. The card is read once and hashed while copying; the copy is then re-hashed (**check 1**). The manifest `.field-manifest.tsv` is written last. It waits until field-ingest has every file with the same sha256 (**check 2**), asks, deletes **only those files** (and only if unchanged since the import), and ejects. It never formats the card. |
| `field finish <device/batch>` | When nas-02 hadn't confirmed in time (30 min), do the wait-and-erase later |
| `field memos` | After a Finder backup, with `iphone-vault` mounted: reads the newest backup and takes every Voice Memo not taken before (by sha256). It names each one `<date time> <title>.m4a`, writes the same manifest as a card and waits for check 2. **Nothing is erased:** the memos stay on the phone, and the backup isn't changed. An encrypted backup asks for its password, which is never stored. |
| `field status` / `field clean` | Is each batch processed on nas-02 yet? Move processed batches to the Trash |

**"Processed"** means all of these:
- registered;
- in the catalog (Beets);
- analyzed, or under 6 s (too short to analyze);
- arrived more than 65 minutes ago, so an hourly DATAGRID snapshot has run since.

## Server

| Piece | Where |
|---|---|
| `field-ingest` (:8203), `integration/field_ingest.py` | studio-01, music-studio stack; reads My Recordings read-only |
| `field-catalogue.path/.service` + `/usr/local/sbin/field-catalogue` | studio-01 host: runs Beets' safe import of ONE requested folder under `Field Recordings/`, then the identity sync |
| Syncthing folder `field-recordings` | nas-02, receive only, ignoreDelete (`host_vars/nas-02.yml`) |
| Tables | `music.field_recordings` (+ kind/take/track/clock/recorder/format/batch/spoken note), `music.field_sessions` (key → `music.sessions`, `attached_to` only handheld → multitrack) |
| UI | studio.home.example.org → **Field** |
| ep-probe | `field-ingest` |

Why the trigger file: the nightly `audio_identity_sync` marks every catalog location Beets
doesn't know as missing. So field-ingest never writes `audio_locations` itself. It asks for a
Beets import of the new folder, and a container can't `docker exec` into another one.

**Spoken notes:**
- Whisper (`vad_filter` on) runs over the first 20 s of track 1 / MS / the stereo take, 5 per
  round, since gpu-02 also serves DOMO's voice.
- Phantom captions ("Thank you.") and single words are dropped.
- Voice Memos (m4a) are trimmed with ffmpeg; field-ingest runs on the `music-analysis:local`
  image, which has it.
- The notes also catch **sung lyrics**, which help find a take later.

## Not built yet

- Tested end to end only up to Syncthing: the Mac script against a mock server, and field-ingest
  on the real archive. **No real card has been imported yet.**
- Splitting a session by hand. The 66 untimed Tascam takes sit in one folder, so they are one session.
- DOMO voice ("what did I record on Saturday?"), and song suggestions for field takes.
- Moving a non-musical memo (family moments) out of Studio into another area, for example the
  Personal Chronicle. Decision 2026-10-25: **all memos stay cataloged in Studio for now**,
  including the speech-only ones ("could be fun to include them in songs someday").
