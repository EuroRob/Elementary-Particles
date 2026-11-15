# Studio partner: DOMO in the studio

`studio-partner` (studio-01 since 2026-11-01, music-studio stack, port 8202) makes DOMO a partner while owner makes
music in Ableton Live:
- a chronicle of every set and every save, with no notes on paper;
- a creative journal: ideas and to-dos said while working;
- chords, keys and arrangement computed in code, not by the model;
- a conversation grounded in the set that is open.

Everything stays in the house: DATAGRID over NFS, pg-01, studio-01, and DOMO's model on gpu-01.

Code:
- `integration/studio_partner.py`
- `integration/als_reader.py` (reads a set)
- `integration/music_theory.py` (chords and bar maths)
- UI: `music_studio_ui/templates/studio*.html`, at studio.home.example.org → **Studio**

Tests: `integration/test_studio_partner.py`. Particle: `IaC/services/assistant/studio-partner/service.yaml`.

Practice (ear training and keyboard drills at /ui/practice, plus the Chromatone theory explorer)
is served by studio-partner too: see [STUDIO_PRACTICE.md](STUDIO_PRACTICE.md).

## Household decisions (2026-10-24)

| Question | Decision |
|---|---|
| Where sets are saved | On DATAGRID, under My Recordings. **Recommended since then (open):** record on the Mac's SSD and mirror one way to DATAGRID with Syncthing. See below. |
| Live | Live 12 Suite on the Intel MacBook |
| How far DOMO goes | **Read + suggest; owner makes every change himself.** A live link (AbletonOSC Remote Script on the Mac) is not built yet. |
| Creative journal | **The studio chronicle** in Music Studio's database, not the private diary |

## What you say to DOMO

| Say | What happens |
|---|---|
| "studio session" / "let's make some music" | Opens a conversation on the set saved last (within 3 h). While it's open, every sentence goes there. It ends with "end studio session", "that's it for today" or after 30 min of silence; a summary goes into the chronicle. |
| "idea: half-time bridge" / "song idea: …" / "studio todo: …" | Filed under the set you saved last. Inside a session, a bare "note:" / "todo:" works too. Outside a session it doesn't, because "todo: buy milk" is not music. |
| "what did I do in the studio today?" | Today's saves (what changed) and notes |
| "where are the files for Grand Times?" | The set's path on DATAGRID, and how many of its recordings are in the catalogue |

## The chronicle

- **Watching:**
  - every `.als` under `/mnt/nasty-music` (= nas-02 `DATAGRID/Multimedia/Music/My Recordings`, read-only);
  - a full walk every 15 min finds new sets; known sets are stat'ed every 20 s;
  - skipped: `Backup`, `Samples`, Ableton's own `Lessons`/`Factory Packs`/`User Library`.
- **First run (2026-10-24):** 63 sets.
  - 12 are Live 9 XML and fully read;
  - 51 are Live ≤7 binary sets, known as files only, but linked to their `<name> Sounds/` audio;
  - the 14 skipped were Ableton's bundled Lessons.
- **Each save:**
  - becomes a `music.studio_snapshots` row (the `als_reader` summary) and a `save` event with what changed, e.g.
    "Added midi track 'Bass' (Operator)", "Tempo 120 → 96 BPM", "New loop 'Sub' (2 bars) on 'Bass'",
    "Arrangement 32 → 48 bars", "Locator 'Chorus' at bar 9";
  - mixing and automation edits show as "no structural change".
- **History:** on first sight of a set, Live's own `Backup/<name> [date].als` copies are read
  first, so the chronicle starts before DOMO did.
- **Projects** are `music.sessions` rows. Title, status and notes are yours; DOMO never
  overwrites them.
- **Files:** `music.studio_files` links a set to its audio in the catalogue (`audio_locations`).
  - A `… Project/` folder counts as a whole; otherwise `<name> Sounds/` and `Samples/Recorded/` next to the set.
  - The set's own sample references are resolved relative to the project. A set saved on the Mac
    (absolute `/Users/…` paths) still finds its files in the DATAGRID mirror.

## The session

The model is `gpu01-primary` with `num_ctx` 16384. It gets these facts:
- the open set (tracks, devices, session loops with lengths, arrangement clips, locators, scenes, the set note);
- the last 8 chronicle events and the open to-dos;
- the key: Live 12's scale setting, the session's key, or a key owner names;
- for that key: diatonic, borrowed and secondary-dominant chords, spelled by letter (F# major's vii° is E#dim);
- related keys;
- the owner's most-played progressions from `music.progressions` (his own recordings), realised in the key;
- the Live 12 Suite instruments and his plugins.

**Checked by code, because the model got it wrong in testing (2026-10-24):**
- **Chords.** In A minor the model wrote "V Em" (Em is v).
  - `music_theory.check_chords` checks every "numeral + chord" pair and every chord name against the computed lists.
  - On a mismatch, the model rewrites once, from an internal system note. Sent as "user", the
    note made it apologise to owner.
  - Anything still wrong is flagged in the reply.
- **Bar plan.** The model invented bar counts ("fits your 64-bar set perfectly").
  - For an arrangement question, code writes the plan into the reply: the common form, in 8/16-bar
    phrases, closest to the length asked ("three and a half minutes", "3:30"). At 130 BPM that is 3:41 / 120 bars.
  - Scaling a single form gave 12- and 20-bar sections.
- **No pretend changes.** The prompt forbids saying anything was changed in Live. In the test,
  "Did you add the pad?" got "No, I didn't change anything", plus the real Pad track's preset.

Typical latency: 4–12 s a reply.

## Recording: local SSD, mirrored to DATAGRID (recommended, not set up)

Once a set is saved, Live records into its `Samples/Recorded`. On an SMB share that means every
take streams over Wi-Fi to the NAS, so there's a risk of dropouts and "disk too slow" errors.
- **Server-side ZFS caching doesn't fix this.** nas-02's ARC already absorbs writes, and an
  L2ARC/SLOG speeds up the NAS but not the network hop.
- **Recommended:**
  1. Keep active projects on the Mac's internal SSD, or a Thunderbolt NVMe "studio scratch" drive.
  2. Mirror them with **Syncthing**, one way: Mac *send only* → nas-02 *receive only*, into
     My Recordings. It's local, peer-to-peer, with no relay or discovery servers (disable global
     discovery and relays).
  3. Turn on Syncthing **file versioning** on nas-02, so every earlier `.als` is kept. These are
     history the chronicle can read later (`.stversions` is skipped by the walker today).
- **Nothing in the partner changes.** It watches the mirror, and saves arrive within seconds.

## Not built yet

- **Live link (AbletonOSC).** The free Remote Script on the Mac would let DOMO read the open set
  live: tempo, what's playing, the selected track, loop points. It is needed for "where am I"
  answers between saves. owner installs it; it needs the Mac's IP and a VLAN30 → Mac UDP 11000/11001
  firewall rule. Read-only by decision.
- **Syncthing mirror:** see above; the user decides.
- **Ticking to-dos off** in the UI (`done` column exists).
- **Linking a set to a song** in the songbook (`music.session_songs`).
