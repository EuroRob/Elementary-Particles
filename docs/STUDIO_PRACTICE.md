# Studio practice: ear training, keyboard drills and the theory explorer

owner asked (2026-10-31) for a music tutor next to the studio: ear training, better piano
playing through chords, scales and progressions, and a visual guide to progressions. The
starting point was [vpavlenko/study-music](https://github.com/vpavlenko/study-music).
- **What that repo is:** a reading list (143 Markdown files of links, no licence file).
  It's linked from here, not copied.
- **No ear trainer to host:** every ear trainer it lists is a hosted service (ToneGym, Artusi, Teoria, Theta).
- **So two things were built:**
  1. **Theory explorer:** [Chromatone](https://github.com/chromatone/chromatone.center) (MIT), self-hosted.
  2. **Practice page:** drills written here, graded by code like the studio partner's chords.

| What | Where |
|---|---|
| Practice page | https://studio.home.example.org/ui/practice (nav: **Practice**) |
| Theory explorer | https://theory.home.example.org (Chromatone, self-hosted on studio-01): circle of fifths, chord and scale explorers, progressions, MIDI piano roll, tuner, drone. Linked from the practice page. |

The practice page is LIVE (studio-partner + music-assistant), verified 2026-10-31; on studio-01 since 2026-11-01.

## Using it on the Mac

- **Browser:** use Brave or Chrome. Safari has no Web MIDI.
  - Brave asks "use your MIDI devices?" the first time: allow it.
  - If it was blocked, click the icon left of the address bar, allow MIDI devices, and reload.
  - Brave Shields can block it too.
- **Connect:** click **Connect keyboard** once. The page remembers it.
  - It listens to every MIDI input.
  - Channel 10 is ignored, so the TD-25's drum notes never count as piano notes.
  - Ableton and the browser can both listen to the N61 at the same time.
- **Sound:** click **Start** once per visit; browsers only allow sound after a click.
  - Untick **Hear my keys here** if Ableton is already playing the N61, or you hear it twice.
- **Without a keyboard:** click the on-screen keys.
  - For chords, click the notes, then **Enter chord**.
- **Shortcuts:** <kbd>Space</kbd> plays again, <kbd>Enter</kbd> goes to the next exercise.

## The drills

| Deck | Level 1 | Level 2 | Level 3 |
|---|---|---|---|
| Ear · Intervals | 3rds, 4th, 5th, octave going up | every interval, up and down | also played together |
| Ear · Chords | major / minor | + dim, aug, any inversion | 7th chords |
| Ear · Progressions | major keys | + minor keys and **your own progressions** (★) | |
| Play · Chords | major/minor on white keys | every root, + dim and aug | 7th chords |
| Play · Scales | major + natural minor, up to two ♯/♭ | every key, modes, pentatonics | |
| Play · Progressions | ii–V–I and the pop four in C G F D | every major key + your own | + minor keys |

- **Spelling:** every note and chord is spelled by letter (`practice.py`, using `music_theory.py`).
  For example, C dim7 is C E♭ G♭ B𝄫, and F♯ major's 7th note is E♯.
- **Grading:** done by studio-partner from the exercise's spec, never from what the page says.
- **Played chords:** any inversion and any doubling count.
  - A chord is taken the moment the right notes are held, so legato with common tones works.
  - A wrong chord is judged when you let go.
  - The reply names what was missing or extra, and which inversion you played.
- **Played scales:** one octave up from the tonic. The first wrong note ends the try and is named.
- **Played progressions:** one chord at a time; the chips above the prompt show where you are.
  - When you get it right, the page tells you how far your right hand moved across the changes,
    compared with the smoothest close voicing.
  - **See and hear it** plays that smoothest voicing on the keyboard.
  - It's a hint and never fails you.
- **Help buttons:**
  - **Show me** shows the notes. That try counts as a miss, so the item comes back soon.
  - **Hear it** on a play drill is sound only, without lighting the keys.
- **Your own progressions:** the 5 most-played progressions per mode in `music.progressions`
  (from your recordings) join the progression decks from level 2.

### What comes next

- **Weighting:** each item's weight is `0.25 + 3 × (misses in its last 5 tries)`, plus 0.75 for an
  item never seen. "Show me" counts as a miss. The item just asked is almost never repeated at once.
  - Practical effect: what you miss comes back several times as often as what you know.
- **Progress panel:**
  - today's answers and minutes;
  - a day streak (10+ answers a day);
  - accuracy per deck over 7 days;
  - "to work on": items missed in their last tries (30 days, at least 3 tries).

## Wind-down

- **The tick:** 10 or more answers on the practice page between 04:00 and 04:00 tick "play an
  instrument" in tonight's wind-down list (source `practice`).
- **Status 2026-10-31: code and tests done, NOT deployed.** The auto-mode classifier blocked the
  file copy to web-01. A full `deploy_assistant_stack.yml` run would also revert live work that
  isn't on master (domo-router's Apple Watch changes, 5 Traefik route files), so only
  `domo_winddown.py` should go: copy it to `/opt/docker/assistant-stack/domo-pulse/` and
  `docker restart domo-winddown`. The live file was identical to master.
- **How it checks:** `domo-winddown` (web-01) asks studio-partner's `/practice/summary` when it
  builds the list. It's the same idea as the journal item: no extra step needed.
- **Settings:** `WINDDOWN_PRACTICE_MIN` sets the number of answers; `STUDIO_URL` sets where studio-partner is.

## How it's built

| Piece | File |
|---|---|
| Exercises, grading, voicing, weighting (no I/O) | `integration/practice.py` |
| API + storage (`music.practice_attempts`) | `integration/studio_partner.py` → `/practice/*` |
| Page (Web Audio voice, Web MIDI, keyboard) | `integration/music_studio_ui/templates/practice.html` |
| UI routes `/ui/practice`, `/api/practice/*` | `integration/music_assistant.py` |
| Wind-down tick | `integration/DOMO_IOS_App/domo_winddown.py` (`practised`) |
| Tests | `integration/test_practice.py`, `DOMO_IOS_App/test_domo_winddown.py` |
| Chromatone image + compose | `IaC/services/assistant/music-studio/chromatone/` (Dockerfile, nginx.conf, docker-compose.yml) |
| Chromatone deploy | `IaC/Ansible/playbooks/deploy_studio01.yml` (studio-01) |

- **Chromatone runs on studio-01** (VM 283 on pve-03, 10.10.30.59), LIVE 2026-10-31.
  - Its first build on gpu-01 was OOM-killed in a 2 GB cap (gpu-01 had ~3 GB free), so it got
    its own VM, the start of moving the whole music stack there: STUDIO01_MIGRATION.md.
  - Built on studio-01 with no cap (the 4 GB VM is the fence); the build took a few minutes.
- **Chromatone build:**
  - Pinned to commit `6ff42563` (`CHROMATONE_COMMIT` in the Dockerfile).
  - Rebuilt only when the Dockerfile or nginx.conf changes; to update, bump the commit and run
    `deploy_studio01.yml --tags chromatone`.
- **Chromatone at run time:**
  - Its visitor-stats script is removed at build time (verified: no `stats.chromatone` in the page).
  - Cover images still load from `db.chromatone.center` (cosmetic only).
  - nginx on studio-01 :8204, 64 MB cap (uses ~5 MB); Traefik `theory.home.example.org`, no Authelia,
    like studio.home.example.org.
- **DNS:** Pi-hole host `10.10.30.20 theory.home.example.org`, added live via the v6 REST API.

Deploy: practice page = `deploy_studio01.yml --tags music_studio` (see MUSIC_STUDIO.md);
Chromatone = `deploy_studio01.yml --tags chromatone`.

## Not built yet

- **Voice:** "how's my practice going?" to DOMO.
  The data is all in `/practice/stats`; the router would need a route.
- **Keys from your sets:** progression drills in the keys of the sets you're working on.
  `music.sessions.key_signature` and Live 12's scale setting are already read.
- **Rawl-style colour piano roll** of your own MIDI clips from Live 12 sets (step 3 of the evaluation).
  The idea, not the code: Rawl is GPL-3.
- **Audio-only takes:** Basic Pitch (audio → MIDI) on gpu-02, so audio-only takes get the same view.
