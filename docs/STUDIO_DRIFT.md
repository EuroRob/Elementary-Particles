# Studio drift check (24c + R24)

The "Studio" aggregate joins two interfaces with separate clocks: the PreSonus
Studio 24c (clock source) and the Zoom R24 (drift correction on). This check
measures, from a recording, how far apart the two interfaces are and whether
that gap changes over time.

| Test | What it measures | What you do with the number |
|---|---|---|
| **Output loopback** | The gap between the 24c's and the R24's outputs (both headphone outs) | Confirms drift correction works; a fixed gap of a few ms is fine |
| **Input split** | The gap between a 24c input and an R24 input fed the same signal | Set it as **Track Delay** on R24 tracks, save in the template |

## 1. Record

**Output loopback (30 minutes):**

1. Output Config in Live: 1/2 and 3/4 on. Master → 1/2 (24c). A track **Phones 2**:
   Audio From *Resampling*, Monitor *In*, Audio To *3/4* (R24).
2. Cable the 24c headphone out → R24 input 5, the R24 headphone out → R24 input 6.
   Both return to the **same** interface, so only the outputs differ.
3. Two audio tracks named **Loop 24c** (input 7, aggregate numbering) and
   **Loop R24** (input 8). Monitor **Off** on both, or it feeds back.
4. Metronome on (or a short percussive clip looping), headphone volumes low, input gain
   up from zero until the clicks peak around −12 dB.
5. Record both tracks for 30 minutes. Save the set as **Drift Test**.

**Input split (2 minutes):** a click source (phone, H2n headphone out) through a
3.5 mm → 2 × 6.3 mm Y-cable into a 24c input and an R24 input. Tracks **Split 24c** and
**Split R24**, Monitor Off, record two minutes. Save as **Input Split**.

The names matter: the check takes the newest file with *24c* and the newest
with *R24* as a separate word in its name, and Live names recordings after the track.

## 2. Run

Syncthing copies the set to nas-02 (My Recordings/Ableton Live/Projects). On studio-01:

```bash
sudo studio-drift "Drift Test"            # the set name, as in Live
sudo studio-drift "Input Split"
sudo studio-drift "Ableton Live/Projects/Drift Test Project/Samples/Recorded"   # or a folder
```

Or just say "run the drift check on Drift Test" to Claude Code. It takes about 10 seconds for 30 minutes.

```
Clicks found by: qm-vamp-plugins:qm-onsetdetector
Clicks used:     3599 over 29.99 min (0 rejected)
Gap at start:    +4.370 ms   (positive = R24 later)
Gap at end:      +4.400 ms
Change:          +0.030 ms  = +0.060 ms/hour, +0.017 ppm
Jitter:          0.0001 ms (std), worst 0.000 ms, jumps > 0.5 ms: 0
Verdict:         STABLE. Drift correction is working. ...
```

| Verdict | Meaning | Do |
|---|---|---|
| **STABLE** | Gap changed ≤ 0.1 ms over the take | Nothing. For the split test, use the gap as Track Delay |
| **DRIFTING** | Gap grows steadily | Audio MIDI Setup: clock source = Studio 24c, drift correction on the R24 row only, both 44,100 Hz |
| **JUMPY** | Single clicks > 0.5 ms off the line, or > 5 % of clicks unusable | Dropouts: raise the buffer, both interfaces on the dock, not the hub |

## 3. Look at it (optional)

Each run writes `drift - <file>.csv` (time, gap in ms) and `.json` to
`/opt/music-studio/drift-reports/` on studio-01. In **Sonic Visualiser** open the
24c recording, then *File › Import Annotation Layer* and pick the CSV: the gap is
drawn over the waveform, so a jump lines up with what happened at that moment.

## How it works

`integration/studio_drift.py`, run by `/usr/local/bin/studio-drift`
in the `music-analysis:local` image (role `music_studio`, tag `studio_drift`), no network,
recordings mounted read-only, memory capped at 1.2 GB (a 30-min pair peaks near 0.5 GB).

1. **Find the clicks:** the qm-vamp-plugins onset detector (built in the same image
   as Chordino, from the Centre for Digital Music, who also make Sonic Visualiser) via
   `vamp-simple-host`. On a machine without Vamp it falls back to an energy detector.
2. **Measure each click:** cross-correlate 50 ms of the 24c recording with the R24
   recording (±50 ms search) and fit a parabola through the peak, so the gap is
   measured to a fraction of a sample (one sample is 0.023 ms at 44.1 kHz).
3. **Fit a line** through gap over time: its start is the fixed offset, its slope the
   drift (ms/hour, ppm), and what's left is jitter and jumps.

Accepts WAV (16/24/32-bit, float, mono or stereo; AIFF via ffmpeg), a folder, two
files, or one stereo file (left = 24c). Tests: `integration/test_studio_drift.py`
(synthetic clicks with known offset, drift and a buffer slip).
