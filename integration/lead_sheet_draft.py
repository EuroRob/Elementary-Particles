"""
lead_sheet_draft.py — Musician Platform phase 3: build a draft ChordPro lead
sheet from one recording's analysis (Chordino chords, Segmentino sections,
detected key/tempo) and Whisper word timestamps from a Demucs vocal stem.
Pure functions, no I/O — music_analysis_worker.py does the fetching.

The draft is a STARTING POINT: chords sit on the word sung when they
change, lines follow Whisper's phrases, blocks follow the detected
sections. Expect wrong words and chords — it's meant to be corrected in the
editor and saved by a human.
"""
import re

# Whisper's stock hallucinations on near-silence / instrumental passages —
# seen live 2026-10-08 ("Thank you." x7 on an instrumental, "Transcription
# by CastingWords" on every gap of a vocal track without VAD).
_HALLUCINATIONS = re.compile(
    r"^\s*(thank you\.?|thanks for watching!?|transcription by castingwords|castingwords|"
    r"subtitles by .*|untertitel .*|untertitelung .*|copyright .*|♪+|\.+)\s*$", re.I)

GAP_INSTRUMENTAL = 4.0      # seconds without words that get their own chord line
MAX_WORDS_PER_LINE = 12
INSTRUMENTAL_CHORDS = 8     # chords shown per instrumental line


def clean_segments(whisper: dict) -> list:
    """[(start, end, [(word, start, end), ...]), ...] without hallucinated segments."""
    out = []
    for s in whisper.get("segments") or []:
        if _HALLUCINATIONS.match(s.get("text", "")):
            continue
        words = [(w["word"].strip(), float(w["start"]), float(w["end"]))
                 for w in (s.get("words") or []) if w.get("word", "").strip()]
        if not words:
            # some servers put words only at the top level
            words = [(w["word"].strip(), float(w["start"]), float(w["end"]))
                     for w in (whisper.get("words") or [])
                     if float(s["start"]) - 0.05 <= float(w["start"]) < float(s["end"]) + 0.05 and w.get("word", "").strip()]
        if words:
            out.append((float(s["start"]), float(s["end"]), words))
    # split over-long phrases at their largest pause
    lines = []
    for start, end, words in out:
        stack = [words]
        while stack:
            w = stack.pop(0)
            if len(w) <= MAX_WORDS_PER_LINE:
                lines.append(w)
                continue
            gaps = [(w[i + 1][1] - w[i][2], i) for i in range(len(w) - 1)]
            _, cut = max(gaps)
            stack[0:0] = [w[: cut + 1], w[cut + 1:]]
    return lines


def _active(chords, t):
    cur = None
    for c in chords:
        if c[0] <= t + 1e-6:
            cur = c
        else:
            break
    return cur


def _dedupe(labels):
    out = []
    for l in labels:
        if not out or out[-1] != l:
            out.append(l)
    return out


def _chord_line(chords, t0, t1):
    labels = _dedupe([c[2] for c in chords if t0 <= c[0] < t1 and c[2] != "N"])
    return " ".join(f"[{l}]" for l in labels[:INSTRUMENTAL_CHORDS]) + (" …" if len(labels) > INSTRUMENTAL_CHORDS else "")


def lyric_line(words, chords, last_chord):
    """Insert [Chord] before the word sung when the chord changes."""
    t0, t1 = words[0][1], words[-1][2]
    changes = []
    a = _active(chords, t0)
    if a and a[2] != "N" and a[2] != last_chord:
        changes.append((t0, a[2]))
    changes += [(c[0], c[2]) for c in chords if t0 < c[0] < t1 and c[2] != "N"]
    slots = {}
    for t, label in changes:
        i = next((k for k, w in enumerate(words) if w[1] >= t - 0.15), len(words) - 1)
        slots.setdefault(i, []).append(label)
    out, last = [], last_chord
    for k, (text, _, _) in enumerate(words):
        for label in _dedupe(slots.get(k, [])):
            if label != last:
                out.append(f"[{label}]")
                last = label
        out.append(text + " ")
    return "".join(out).strip(), last


MIN_CHORD_SECONDS = 1.0


def smooth_chords(chords, min_len=MIN_CHORD_SECONDS):
    """Chordino flickers on dense mixes ("[Baug]thought [Bb][B7]disappear" on a
    produced track): fold any chord shorter than min_len into its predecessor,
    then merge neighbours that ended up identical. Readability over detail —
    the full-resolution chords stay in the analysis lane on the song page."""
    out = []
    for t, end, label in chords:
        if out and ((end if end is not None else t) - t < min_len or out[-1][2] == label):
            out[-1] = (out[-1][0], end, out[-1][2])
        else:
            out.append((t, end, label))
    return out


def _fmt_time(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def build(song: dict, analysis: dict, whisper: dict, recording_name: str, voiced_share: float) -> str:
    """analysis = {'summary': {...}, 'events': {'chord': [[t, end, label]], 'section': [...]}, 'duration_sec'}."""
    chords = smooth_chords([tuple(c) for c in analysis["events"].get("chord", [])])
    sections = [tuple(s) for s in analysis["events"].get("section", [])] or [(0.0, analysis["duration_sec"], "Song")]
    s = analysis.get("summary") or {}
    lines = clean_segments(whisper) if whisper else []

    head = [f"{{title: {song['title']}}}"]
    if song.get("project"):
        head.append(f"{{artist: {song['project']}}}")
    if s.get("key"):
        head.append(f"{{key: {_short_key(s['key'])}}}")
    if s.get("tempo_bpm"):
        head.append(f"{{tempo: {round(s['tempo_bpm'])}}}")
    if s.get("meter"):              # detected (phase 4) — only set when clearly 3/4 or 4/4
        head.append(f"{{time: {s['meter']}}}")
    head.append(f"{{meta: song_id {song['id']}}}")
    head.append(f"{{comment: Automatic draft from {recording_name} — chords by Chordino, "
                + ("lyrics by Whisper on a Demucs vocal stem" if lines else
                   "no vocals detected" if voiced_share < 0.03 else "no usable lyrics found")
                + ". Check everything.}")
    out = head + [""]

    last_chord = None
    for i, (st, en, label) in enumerate(sections):
        en = en if en is not None else analysis["duration_sec"]
        sec_lines = [w for w in lines if st <= w[0][1] < en]
        out.append(f"{{x_time: {st:.1f}}}")
        out.append(f"{{start_of_verse: {label} ({_fmt_time(st)})}}")
        if not sec_lines:
            cl = _chord_line(chords, st, en)
            out.append(cl if cl else "{comment: (no chords detected)}")
        else:
            cursor = st
            for w in sec_lines:
                if w[0][1] - cursor >= GAP_INSTRUMENTAL:
                    cl = _chord_line(chords, cursor, w[0][1])
                    if cl:
                        out.append(cl)
                        last_chord = cl.split("]")[-2].lstrip(" [") if "]" in cl else last_chord
                text, last_chord = lyric_line(w, chords, last_chord)
                out.append(text)
                cursor = w[-1][2]
            if en - cursor >= GAP_INSTRUMENTAL:
                cl = _chord_line(chords, cursor, en)
                if cl:
                    out.append(cl)
        out.append("{end_of_verse}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _short_key(k: str) -> str:
    """'D minor' -> 'Dm', 'Bb major' -> 'Bb' (ChordPro {key} convention)."""
    m = re.match(r"^([A-G][b#]?)\s*(major|minor)?", k.strip(), re.I)
    if not m:
        return k
    return m.group(1) + ("m" if (m.group(2) or "").lower() == "minor" else "")
