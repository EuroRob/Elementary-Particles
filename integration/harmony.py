"""
harmony.py — Musician Platform phase 4: meter + key-relative chord
progressions, from the stored analysis (no audio needed). Pure functions;
music_analysis_worker.py / the catalog do the I/O. See
IaC/docs/MUSICIAN_PLATFORM.md -> "Phase 4".

* Chords -> Roman numerals relative to the key in force at that moment
  (local key changes, not one global key), so "Am F C G" in C and
  "Em C G D" in G are the same progression: vi–IV–I–V.
* Loops: the repeating cycle inside a section (e.g. i–bVI–bIII–bVII played
  4x) — the building blocks the catalogue is made of.
* Meter: the bar/beat tracker always assumes 4 beats per bar (all 2,601
  tracked recordings came back 4/4, 2026-10-09), so meter is decided here:
  harmony tends to change on downbeats, so for 3 and 4 beats per bar we
  check how strongly chord changes line up with one beat position.
"""
import re
from collections import Counter

NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
DEGREE_NAMES = {0: "I", 1: "bII", 2: "II", 3: "bIII", 4: "III", 5: "IV", 6: "#IV", 7: "V", 8: "bVI", 9: "VI", 10: "bVII", 11: "VII"}
SHARP_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
FLAT_KEYS = {"F", "Bb", "Eb", "Ab", "Db", "Gb", "Dm", "Gm", "Cm", "Fm", "Bbm", "Ebm"}
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

_CHORD = re.compile(r"^([A-G])([#b]?)(.*?)(?:/([A-G][#b]?))?$")


def pc(name: str) -> int:
    p = NOTE[name[0]]
    for acc in name[1:]:
        p += 1 if acc == "#" else -1 if acc == "b" else 0
    return p % 12


def parse_chord(label: str):
    """'C#m7' -> (1, 'min', '7'); 'Bbmaj7' -> (10, 'maj', 'maj7'); 'N' -> None."""
    m = _CHORD.match(label.strip())
    if not m:
        return None
    root = pc(m.group(1) + m.group(2))
    rest = m.group(3)
    if rest.startswith("m") and not rest.startswith("maj"):
        qual, ext = "min", rest[1:]
    elif rest.startswith("dim"):
        qual, ext = "dim", rest[3:]
    elif rest.startswith("aug"):
        qual, ext = "aug", rest[3:]
    else:
        qual, ext = "maj", rest
    if ext == "7b5" and qual == "min":
        qual, ext = "hdim", ""
    return root, qual, ext


def parse_key(label: str):
    """'D minor' -> (2, 'minor'); 'F# / Gb major' -> (6, 'major')."""
    m = re.match(r"^\s*([A-G][#b]?)(?:\s*/\s*[A-G][#b]?)?\s+(major|minor)", label or "")
    return (pc(m.group(1)), m.group(2)) if m else None


def roman(chord, key) -> tuple:
    """(full, simple): ('vi7', 'vi'). Minor keys are written relative to their
    own tonic with flats (i–bVI–bIII–bVII), the usual pop/rock convention."""
    root, qual, ext = chord
    tonic, _mode = key
    deg = DEGREE_NAMES[(root - tonic) % 12]
    num = deg.lstrip("b#")
    acc = deg[: len(deg) - len(num)]
    if qual in ("min", "dim", "hdim"):
        num = num.lower()
    simple = acc + num + {"dim": "°", "hdim": "ø", "aug": "+"}.get(qual, "")
    full = simple + (ext if qual not in ("hdim",) else "7")
    return full, simple


def key_short(key) -> str:
    tonic, mode = key
    names = FLAT_NAMES if (SHARP_NAMES[tonic] + ("m" if mode == "minor" else "")) in FLAT_KEYS or SHARP_NAMES[tonic] in ("Eb", "Ab", "Bb") else SHARP_NAMES
    return names[tonic] + ("m" if mode == "minor" else "")


def realise(simple: str, key, prefer_flats=None) -> str:
    """Roman (simple) -> chord name in a key: realise('vi', (0,'major')) -> 'Am'."""
    m = re.match(r"^([b#]?)([ivIV]+)([°ø+]?)$", simple)
    if not m:
        return "?"
    acc, num, suf = m.groups()
    base = {"I": 0, "II": 2, "III": 4, "IV": 5, "V": 7, "VI": 9, "VII": 11}[num.upper()]
    semis = base + (-1 if acc == "b" else 1 if acc == "#" else 0)
    root = (key[0] + semis) % 12
    flats = prefer_flats if prefer_flats is not None else key_short(key) in FLAT_KEYS or "b" in key_short(key)
    name = (FLAT_NAMES if flats else SHARP_NAMES)[root]
    if num.islower():
        name += {"°": "dim", "ø": "m7b5"}.get(suf, "m")
    elif suf == "+":
        name += "aug"
    return name


# ---------------------------------------------------------------------------
def _root(label):
    c = parse_chord(label)
    return c[0] if c else None


def smooth(chords, min_len=1.0):
    """Drop 'N', fold chords shorter than min_len into the previous one, and
    merge same-root neighbours (C / Cm / C7 flicker): Chordino often can't
    decide the third on a dense mix, which otherwise shows up as a fake
    'I–i' loop in 148 recordings. The longer of the two labels wins."""
    out = []
    for t, end, label in chords:
        if label == "N":
            continue
        dur = (end if end is not None else t) - t
        if out and (dur < min_len or _root(out[-1][2]) == _root(label)):
            prev = out[-1]
            prev_dur = (prev[1] if prev[1] is not None else prev[0]) - prev[0]
            out[-1] = (prev[0], end, prev[2] if prev_dur >= dur or dur < min_len else label)
        else:
            out.append((t, end, label))
    return out


def key_at(keys, t):
    cur = None
    for k in keys:
        if k[0] <= t + 1e-6:
            cur = k
        else:
            break
    return parse_key(cur[2]) if cur else None


def to_roman(chords, keys, fallback_key=None):
    """[(t, end, label, full, simple, key_short)] for smoothed chords."""
    out = []
    for t, end, label in smooth(chords):
        c = parse_chord(label)
        k = key_at(keys, t) or fallback_key
        if not c or not k:
            continue
        full, simple = roman(c, k)
        out.append((t, end, label, full, simple, key_short(k)))
    return out


def find_loops(seq, min_len=2, max_len=8, min_repeats=2, two_chord_repeats=4):
    """Repeating cycles in a list of symbols: [(start_idx, length, repeats)],
    greedy left to right, shortest period first (so I–V–I–V is a 2-loop, not 4)."""
    loops, i, n = [], 0, len(seq)
    while i < n:
        best = None
        for L in range(min_len, max_len + 1):
            if i + L * min_repeats > n:
                break
            unit = seq[i:i + L]
            if len(set(unit)) < 2:
                continue
            reps = 1
            while seq[i + reps * L: i + (reps + 1) * L] == unit:
                reps += 1
            need = two_chord_repeats if L == 2 else min_repeats   # 2-chord "loops" are mostly Chordino wavering
            if reps >= need and (not best or reps * L > best[2] * best[1]):
                best = (i, L, reps)
        if best:
            loops.append(best)
            i += best[1] * best[2]
        else:
            i += 1
    return loops


def canonical(unit):
    """Rotation-invariant form: start on the tonic if present (I/i), else the
    lexicographically smallest rotation — so 'IV–I–V–vi' and 'I–V–vi–IV'
    land on the same catalogue entry."""
    rots = [unit[k:] + unit[:k] for k in range(len(unit))]
    tonic = [r for r in rots if r[0] in ("I", "i")]
    return tuple(min(tonic) if tonic else min(rots))


def detect_meter(beats, chords, min_changes=12):
    """-> (meter '3/4'|'4/4'|None, confidence 0..1, downbeat phase, lift3, lift4).
    Snap each chord change to its nearest beat; for 3 and 4 beats per bar the
    'lift' is how much more often changes land on the best beat position than
    chance (1.0 = no preference). Picks the clearly stronger one."""
    if len(beats) < 16:
        return None, 0.0, 0, 0.0, 0.0
    ibi = sorted(b - a for a, b in zip(beats, beats[1:]))[len(beats) // 2]
    idx = []
    j = 0
    for t, _, label in smooth(chords):
        while j + 1 < len(beats) and abs(beats[j + 1] - t) < abs(beats[j] - t):
            j += 1
        if abs(beats[j] - t) <= 0.35 * ibi:
            idx.append(j)
    if len(idx) < min_changes:
        return None, 0.0, 0, 0.0, 0.0
    res = {}
    for m in (3, 4):
        cnt = Counter(i % m for i in idx)
        phase, top = cnt.most_common(1)[0]
        res[m] = (top / len(idx) * m, phase)
    l3, l4 = res[3][0], res[4][0]
    # Every 4/4 song also looks "2-ish"; a 3/4 song can't hide in 4. Require a
    # clear margin for 3/4, and a real downbeat preference for either.
    if l3 >= 1.35 and l3 > l4 * 1.15:
        return "3/4", min(1.0, (l3 - l4) / l3 * 3), res[3][1], l3, l4
    if l4 >= 1.35 and l4 >= l3:
        return "4/4", min(1.0, (l4 - max(l3, 1.0)) / l4 * 3), res[4][1], l3, l4
    return None, 0.0, 0, l3, l4


# ---------------------------------------------------------------------------
# Recording level
# ---------------------------------------------------------------------------
HARMONY_VERSION = "h1"


def _dedup_timed(rom):
    """Collapse consecutive equal numerals, keeping the first time and the last end."""
    out = []
    for r in rom:
        if out and out[-1][4] == r[4]:
            out[-1] = (out[-1][0], r[1]) + out[-1][2:]
        else:
            out.append(r)
    return out


def analyse_recording(chords, keys, beats, fallback_key_label=None):
    """-> dict for music.audio_harmony (chords = smoothed + roman, loops with times)."""
    fk = parse_key(fallback_key_label) if fallback_key_label else None
    rom = _dedup_timed(to_roman(chords, keys, fk))
    meter, conf, phase, l3, l4 = detect_meter(beats, chords)
    seq = [r[4] for r in rom]
    loops = []
    for i, L, reps in find_loops(seq):
        unit = seq[i:i + L]
        span = rom[i:i + L * reps]
        loops.append([round(span[0][0], 3), round(span[-1][1] if span[-1][1] is not None else span[-1][0], 3),
                      list(canonical(unit)), reps, rom[i][5], [r[2] for r in rom[i:i + L]]])
    return {"meter": meter, "meter_confidence": round(conf, 3), "downbeat_phase": phase,
            "lift3": round(l3, 3), "lift4": round(l4, 3),
            "chords": [[round(r[0], 3), round(r[1], 3) if r[1] is not None else None, r[2], r[3], r[4], r[5]] for r in rom],
            "loops": loops}


def pattern_str(unit) -> str:
    return "–".join(unit)


def pattern_mode(unit) -> str:
    return "minor" if unit and unit[0][:1].islower() or (unit and unit[0] == "i") else "major"


# ---------------------------------------------------------------------------
# Song sheets (ChordPro) — the human-corrected source of truth
# ---------------------------------------------------------------------------
_DIRECTIVE = re.compile(r"^\{\s*([a-z_]+)\s*(?::\s*(.*?))?\s*\}\s*$", re.I)
_INLINE = re.compile(r"\[([^\]]+)\]")


def parse_chordpro(text: str) -> dict:
    """{key, time, sections:[{label, chords:[...]}]} — chords in order of
    appearance; lines outside any section go into an unnamed one."""
    key = time = None
    sections, cur = [], None
    for line in text.splitlines():
        m = _DIRECTIVE.match(line.strip())
        if m:
            name, val = m.group(1).lower(), (m.group(2) or "").strip()
            if name == "key":
                key = val
            elif name == "time":
                time = val
            elif name.startswith("start_of_") or name in ("soc", "sov", "sob"):
                kind = {"soc": "chorus", "sov": "verse", "sob": "bridge"}.get(name, name[len("start_of_"):])
                cur = {"label": val or kind, "chords": []}
                sections.append(cur)
            elif name.startswith("end_of_") or name in ("eoc", "eov", "eob"):
                cur = None
            continue
        found = [c.strip() for c in _INLINE.findall(line) if parse_chord(c.strip())]
        if found:
            if cur is None:
                cur = {"label": "", "chords": []}
                sections.append(cur)
            cur["chords"].extend(found)
    return {"key": key, "time": time, "sections": [s for s in sections if s["chords"]]}


def key_from_sheet(k: str):
    """'Am' / 'F#m' / 'Bb' -> (pc, mode)."""
    m = re.match(r"^\s*([A-G][#b]?)(m(?!aj))?", k or "")
    return (pc(m.group(1)), "minor" if m.group(2) else "major") if m else None


def song_sections_from_sheet(sheet: dict, fallback_key=None):
    key = key_from_sheet(sheet["key"]) if sheet.get("key") else fallback_key
    if not key:
        return None, []
    out = []
    for s in sheet["sections"]:
        roman_seq = []
        for c in s["chords"]:
            pcd = parse_chord(c)
            if pcd:
                roman_seq.append(roman(pcd, key)[1])
        dedup = [x for i, x in enumerate(roman_seq) if i == 0 or x != roman_seq[i - 1]]
        out.append({"label": s["label"], "chords": s["chords"], "roman": dedup,
                    "loops": [list(canonical(dedup[i:i + L])) for i, L, _ in find_loops(dedup, min_repeats=2, two_chord_repeats=3)]})
    return key, out


# ---------------------------------------------------------------------------
# Search ("find related takes")
# ---------------------------------------------------------------------------
_ROMAN_TOKEN = re.compile(r"^[b#]?(?:VII|VI|V|IV|III|II|I|vii|vi|v|iv|iii|ii|i)[°ø+]?$")


def _qclass(qual: str) -> str:
    """Quality class for chord-name matching: diminished is not minor ("Gdim"
    matched "Am" before this, 2026-10-10); extensions (7, maj7, 6) are ignored."""
    return {"min": "min", "dim": "dim", "hdim": "dim", "aug": "aug"}.get(qual, "maj")


def parse_query(q: str):
    """-> ('roman', [simple...]) or ('chords', [(root, qual)...]) or (None, [])."""
    toks = [t for t in re.split(r"[\s,–\-|>]+", q.strip()) if t]
    if toks and all(_ROMAN_TOKEN.match(t) for t in toks):
        return "roman", toks
    chords = [parse_chord(t) for t in toks]
    if toks and all(chords):
        return "chords", [(c[0], _qclass(c[1])) for c in chords]
    return None, []


def interval_signature(chords):
    """Transposition-invariant: [(interval from previous root, quality)...]."""
    return [((b[0] - a[0]) % 12, b[1]) for a, b in zip(chords, chords[1:])], [c[1] for c in chords]


def find_in_sequence(harmony_chords, kind, query):
    """Matches of the query in one recording's smoothed chords: [(t_start, t_end, chords_as_played)]."""
    n = len(query)
    if n < 2 or len(harmony_chords) < n:
        return []
    hits = []
    if kind == "roman":
        seq = [c[4] for c in harmony_chords]
        for i in range(len(seq) - n + 1):
            if seq[i:i + n] == query:
                hits.append(i)
    else:
        qsig, qqual = interval_signature(query)
        parsed = [parse_chord(c[2]) for c in harmony_chords]
        flat = [(p[0], _qclass(p[1])) if p else None for p in parsed]
        for i in range(len(flat) - n + 1):
            w = flat[i:i + n]
            if None in w:
                continue
            sig, qual = interval_signature(w)
            if sig == qsig and qual == qqual:
                hits.append(i)
    out, last = [], -10
    for i in hits:
        if i <= last:       # overlapping repeats of a loop -> one hit per run
            continue
        span = harmony_chords[i:i + n]
        out.append((span[0][0], span[-1][1] if span[-1][1] is not None else span[-1][0], " ".join(c[2] for c in span), span[0][5]))
        last = i + n - 1
    return out
