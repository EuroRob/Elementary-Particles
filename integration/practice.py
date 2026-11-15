"""practice.py — ear training and keyboard drills for the studio partner (2026-10-31).

owner asked for a tutor to get better at the piano: ear training, chords, scales and
progressions, with a visual guide (prompted by github.com/vpavlenko/study-music, a
reading list; its ear trainers are all hosted services, so the drills are built here).

Computed, never generated, like music_theory.py: every exercise is made and graded
here, spelled by letter (F# major's vii° is E#dim). The page in music-assistant only
plays sounds and listens to the keyboard (Web MIDI). Grading uses the exercise's
`spec`, not anything the page says the answer was.

  decks      ear-intervals / ear-chords / ear-progressions: hear it, name it.
             play-chords / play-scales / play-progressions: play it on the N61 (or click).
  weighting  Items missed lately come back more often: weight = 0.25 + 3 × misses in the
             last 5 tries (+0.75 for an item never seen). The item just asked is avoided.
  voicing    Progressions are played and shown in the smoothest close voicing (least
             movement between chords); after a played progression the page says how far
             the owner's hand moved against that, as a voice-leading hint (it never fails him).
  own        the owner's most-played progressions from music.progressions (his recordings) join
             the progression decks from level 2.

No I/O here: studio_partner.py stores attempts (music.practice_attempts) and serves it.
"""
import random
import re
from datetime import date, datetime, timedelta

import music_theory as T

LETTERS = T.LETTERS
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]   # for wrong notes only
MAJOR_ROOTS = T.DEFAULT_TONIC["major"]          # C Db D Eb E F F# G Ab A Bb B
MINOR_ROOTS = T.DEFAULT_TONIC["minor"]          # C C# D Eb E F F# G G# A Bb B
WHITE = ["C", "D", "E", "F", "G", "A", "B"]

INTERVALS = [("m2", 1, "minor 2nd"), ("M2", 2, "major 2nd"), ("m3", 3, "minor 3rd"), ("M3", 4, "major 3rd"),
             ("P4", 5, "perfect 4th"), ("TT", 6, "tritone"), ("P5", 7, "perfect 5th"), ("m6", 8, "minor 6th"),
             ("M6", 9, "major 6th"), ("m7", 10, "minor 7th"), ("M7", 11, "major 7th"), ("P8", 12, "octave")]
INTERVAL = {n: (s, long) for n, s, long in INTERVALS}

# quality -> (semitones above the root, symbol, name). Tones are stacked thirds (letters 1-3-5-7).
QUALITIES = {"maj": ((0, 4, 7), "", "major"), "min": ((0, 3, 7), "m", "minor"),
             "dim": ((0, 3, 6), "dim", "diminished"), "aug": ((0, 4, 8), "aug", "augmented"),
             "maj7": ((0, 4, 7, 11), "maj7", "major 7th"), "7": ((0, 4, 7, 10), "7", "dominant 7th"),
             "m7": ((0, 3, 7, 10), "m7", "minor 7th"), "m7b5": ((0, 3, 6, 10), "m7b5", "half-diminished 7th"),
             "dim7": ((0, 3, 6, 9), "dim7", "diminished 7th")}
INVERSIONS = ["root position", "1st inversion", "2nd inversion", "3rd inversion"]

# scale -> (semitones, letter steps, minor-ish tonic spelling)
SCALES = {"major": ((0, 2, 4, 5, 7, 9, 11), range(7), False),
          "natural minor": ((0, 2, 3, 5, 7, 8, 10), range(7), True),
          "harmonic minor": ((0, 2, 3, 5, 7, 8, 11), range(7), True),
          "dorian": ((0, 2, 3, 5, 7, 9, 10), range(7), True),
          "mixolydian": ((0, 2, 4, 5, 7, 9, 10), range(7), False),
          "lydian": ((0, 2, 4, 6, 7, 9, 11), range(7), False),
          "phrygian": ((0, 1, 3, 5, 7, 8, 10), range(7), True),
          "major pentatonic": ((0, 2, 4, 7, 9), (0, 1, 2, 4, 5), False),
          "minor pentatonic": ((0, 3, 5, 7, 10), (0, 2, 3, 4, 6), True)}
EASY_MAJOR, EASY_MINOR = ["C", "G", "D", "F", "Bb"], ["A", "E", "B", "D", "G"]     # up to two sharps/flats

MAJOR_PROGS = ["I–IV–V–I", "I–V–vi–IV", "vi–IV–I–V", "I–vi–IV–V", "ii–V–I", "I–IV–vi–V",
               "I–bVII–IV–I", "IV–I–V–vi", "I–iii–IV–V"]
MINOR_PROGS = ["i–iv–v–i", "i–bVI–bIII–bVII", "i–bVII–bVI–V", "i–iv–V–i", "ii°–V–i",
               "i–bVI–bVII–i", "i–iv–bVII–bIII"]
FIRST_PROGS = ["ii–V–I", "I–IV–V–I", "I–V–vi–IV", "I–vi–IV–V"]

DECKS = {
    "ear-intervals": {"title": "Intervals", "mode": "ear",
                      "levels": {1: "3rds, 4th, 5th and octave, going up", 2: "every interval, up and down",
                                 3: "every interval, also played together"}},
    "ear-chords": {"title": "Chords", "mode": "ear",
                   "levels": {1: "major or minor", 2: "+ diminished and augmented, any inversion", 3: "7th chords"}},
    "ear-progressions": {"title": "Progressions", "mode": "ear",
                         "levels": {1: "major keys", 2: "major and minor, with your own progressions"}},
    "play-chords": {"title": "Chords", "mode": "play",
                    "levels": {1: "major and minor on white keys", 2: "every root, + diminished and augmented",
                               3: "7th chords"}},
    "play-scales": {"title": "Scales", "mode": "play",
                    "levels": {1: "major and natural minor, up to two sharps or flats",
                               2: "every key, + modes and pentatonics"}},
    "play-progressions": {"title": "Progressions", "mode": "play",
                          "levels": {1: "ii–V–I and the pop four, easy keys", 2: "every major key + your own progressions",
                                     3: "+ minor keys"}},
}

LOW, HIGH, CENTRE = 52, 79, 64       # right-hand range for voicings (E3..G5)


class PracticeError(ValueError):
    pass


# ── spelling ─────────────────────────────────────────────────────────────────

def chord_spelling(root: str, quality: str) -> list[str]:
    """chord_spelling('Eb', 'min') -> ['Eb', 'Gb', 'Bb']; ('C', 'dim7') -> ['C', 'Eb', 'Gb', 'Bbb']."""
    semis = QUALITIES[quality][0]
    i, pc = LETTERS.index(root[0]), T.H.pc(root)
    return [T.spell(LETTERS[(i + 2 * k) % 7], (pc + s) % 12) for k, s in enumerate(semis)]


def chord_symbol(root: str, quality: str) -> str:
    return root + QUALITIES[quality][1]


def scale_spelling(tonic: str, scale: str) -> list[str]:
    """scale_spelling('D', 'dorian') -> D E F G A B C; ('F#', 'major') -> ... E#."""
    semis, steps, _ = SCALES[scale]
    i, pc = LETTERS.index(tonic[0]), T.H.pc(tonic)
    return [T.spell(LETTERS[(i + st) % 7], (pc + s) % 12) for st, s in zip(steps, semis)]


def numeral_chord(numeral: str, key: tuple) -> tuple[str, str, list[int]]:
    """'V' in A minor -> ('E', 'maj', [4, 8, 11]): chord symbol, quality, pitch classes root first."""
    n = T._numeral(numeral, key)
    if not n:
        raise PracticeError(f"unknown numeral {numeral!r}")
    letter, pc, q = n
    return T.chord_name(numeral, key), q, [(pc + s) % 12 for s in QUALITIES[q][0]]


def split_pattern(pattern: str) -> list[str]:
    return [n.strip() for n in re.split(r"[–-]", pattern) if n.strip()]


def note_name(midi: int) -> str:
    return FLAT_NAMES[midi % 12]


# ── voicing ──────────────────────────────────────────────────────────────────

def voicings(pcs: list[int], low: int = LOW, high: int = HIGH) -> list[tuple[int, ...]]:
    """Every close-position voicing (each inversion, each octave) inside the range."""
    out = []
    for inv in range(len(pcs)):
        order = pcs[inv:] + pcs[:inv]
        for base in range(low, low + 12):
            if base % 12 != order[0]:
                continue
            notes, prev = [base], base
            for p in order[1:]:
                prev += (p - prev) % 12 or 12
                notes.append(prev)
            while notes[-1] <= high:
                out.append(tuple(notes))
                notes = [n + 12 for n in notes]
    return out


def movement(a, b) -> int:
    """Semitones the hand moves between two voicings (sorted notes paired up)."""
    a, b = sorted(a), sorted(b)
    extra = abs(len(a) - len(b)) * 3
    return sum(abs(x - y) for x, y in zip(a, b)) + extra


def smoothest(chords: list[list[int]]) -> list[tuple[int, ...]]:
    """The voicing path with the least total movement (starting near the middle of the keyboard)."""
    cands = [voicings(c) for c in chords]
    cost = [{v: abs(sum(v) / len(v) - CENTRE) * 0.5 for v in cands[0]}]
    back: list[dict] = [{}]
    for i in range(1, len(cands)):
        cost.append({}), back.append({})
        for v in cands[i]:
            prev = min(cands[i - 1], key=lambda u: cost[i - 1][u] + movement(u, v))
            cost[i][v] = cost[i - 1][prev] + movement(prev, v)
            back[i][v] = prev
    v = min(cands[-1], key=lambda u: cost[-1][u])
    path = [v]
    for i in range(len(cands) - 1, 0, -1):
        v = back[i][v]
        path.append(v)
    return path[::-1]


def path_movement(path) -> int:
    return sum(movement(a, b) for a, b in zip(path, path[1:]))


# ── items: what a deck at a level can ask ────────────────────────────────────

def _prog_pool(mode: str, own: list[dict]) -> list[str]:
    pool = list(MAJOR_PROGS if mode == "major" else MINOR_PROGS)
    for p in own:
        if p.get("mode") == mode and p["pattern"] not in pool:
            pool.append(p["pattern"])
    return pool


def _valid(pattern: str, mode: str) -> bool:
    key = T.make_key(0, mode)
    try:
        return all(numeral_chord(n, key) for n in split_pattern(pattern))
    except (PracticeError, ValueError):
        return False


def items(deck: str, level: int, own: list[dict] = ()) -> list[str]:
    own = [p for p in own if _valid(p["pattern"], p.get("mode", "major"))]
    if deck == "ear-intervals":
        names = ["m3", "M3", "P4", "P5", "P8"] if level == 1 else [n for n, _, _ in INTERVALS]
        dirs = ["up"] if level == 1 else ["up", "down"] + (["together"] if level >= 3 else [])
        return [f"{n} {d}" for n in names for d in dirs]
    if deck == "ear-chords":
        return {1: ["maj", "min"], 2: ["maj", "min", "dim", "aug"]}.get(level, ["maj7", "7", "m7", "m7b5", "dim7"])
    if deck == "ear-progressions":
        out = [f"major:{p}" for p in (MAJOR_PROGS if level == 1 else _prog_pool("major", own))]
        return out + ([f"minor:{p}" for p in _prog_pool("minor", own)] if level >= 2 else [])
    if deck == "play-chords":
        if level == 1:
            return [chord_symbol(r, q) for r in WHITE for q in ("maj", "min")]
        if level == 2:
            return [chord_symbol(r, q) for q in ("maj", "dim", "aug") for r in MAJOR_ROOTS] + \
                   [chord_symbol(r, "min") for r in MINOR_ROOTS]
        return [chord_symbol(r, q) for q in ("maj7", "7", "m7", "m7b5", "dim7") for r in MAJOR_ROOTS]
    if deck == "play-scales":
        if level == 1:
            return [f"{t} major" for t in EASY_MAJOR] + [f"{t} natural minor" for t in EASY_MINOR]
        return [f"{t} {s}" for s, (_, _, minorish) in SCALES.items() for t in (MINOR_ROOTS if minorish else MAJOR_ROOTS)]
    if deck == "play-progressions":
        if level == 1:
            return [f"{p} in {t} major" for p in FIRST_PROGS for t in ["C", "G", "F", "D"]]
        out = [f"{p} in {t} major" for p in _prog_pool("major", own) for t in MAJOR_ROOTS]
        return out + ([f"{p} in {t} minor" for p in _prog_pool("minor", own) for t in MINOR_ROOTS] if level >= 3 else [])
    raise PracticeError(f"unknown deck {deck!r}")


def check_level(deck: str, level: int) -> int:
    if deck not in DECKS:
        raise PracticeError(f"unknown deck {deck!r}")
    if level not in DECKS[deck]["levels"]:
        raise PracticeError(f"{deck} has levels {sorted(DECKS[deck]['levels'])}")
    return level


# ── choosing what comes next ─────────────────────────────────────────────────

def weight(results: list[bool] | None) -> float:
    if not results:
        return 1.0
    last = results[-5:]
    return 0.25 + 3 * (last.count(False) / len(last))


def choose(candidates: list[str], history: dict[str, list[bool]], rng: random.Random, last_item: str = "") -> str:
    ws = [weight(history.get(c)) * (0.05 if c == last_item and len(candidates) > 1 else 1) for c in candidates]
    return rng.choices(candidates, weights=ws, k=1)[0]


def next_exercise(deck: str, level: int, history: dict[str, list[bool]], rng: random.Random | None = None,
                  last_item: str = "", own: list[dict] = ()) -> dict:
    rng = rng or random.Random()
    check_level(deck, level)
    item = choose(items(deck, level, own), history, rng, last_item)
    return make(deck, level, item, rng, own)


# ── making an exercise ───────────────────────────────────────────────────────

def _base(deck, level, kind, item, prompt, spec, **kw) -> dict:
    return {"deck": deck, "level": level, "kind": kind, "item": item, "prompt": prompt, "spec": spec, **kw}


def _chord_midi(root_midi: int, quality: str, inversion: int) -> list[int]:
    notes = [root_midi + s for s in QUALITIES[quality][0]]
    for _ in range(inversion):
        notes = notes[1:] + [notes[0] + 12]
    return notes


def _key_of(tonic: str, mode: str) -> tuple:
    return T.make_key(T.H.pc(tonic), mode, tonic)


def _prog_chords(pattern: str, key: tuple) -> list[dict]:
    out = []
    for n in split_pattern(pattern):
        sym, q, pcs = numeral_chord(n, key)
        out.append({"numeral": n, "chord": sym, "quality": q, "pcs": pcs, "notes": T.chord_tones(n, q, key)})
    return out


def _with_bass(path, chords) -> list[dict]:
    return [{"notes": [36 + c["pcs"][0]] + list(v), "dur": 1.1} for v, c in zip(path, chords)]


def make(deck: str, level: int, item: str, rng: random.Random, own: list[dict] = ()) -> dict:
    if deck == "ear-intervals":
        name, direction = item.split()
        semis, long = INTERVAL[name]
        root = rng.randint(53, 67) if direction != "down" else rng.randint(60, 74)
        other = root - semis if direction == "down" else root + semis
        play = [{"notes": [root, other], "dur": 1.6}] if direction == "together" else \
               [{"notes": [root], "dur": 0.8}, {"notes": [other], "dur": 1.2}]
        choices = sorted({i.split()[0] for i in items(deck, level)}, key=lambda n: INTERVAL[n][0])
        return _base(deck, level, "ear", item, f"Which interval? ({'played together' if direction == 'together' else direction})",
                     {"semis": semis, "root": root, "direction": direction}, play=play,
                     choices=[{"value": c, "label": c, "title": INTERVAL[c][1]} for c in choices])
    if deck == "ear-chords":
        root = rng.randint(48, 59)
        inversion = rng.randrange(len(QUALITIES[item][0])) if level >= 2 else 0
        notes = _chord_midi(root, item, inversion)
        play = [{"notes": notes, "dur": 1.4}] + [{"notes": [n], "dur": 0.45} for n in notes] + [{"notes": notes, "dur": 1.4}]
        return _base(deck, level, "ear", item, "Which chord quality?", {"quality": item, "root": root, "inversion": inversion},
                     play=play, choices=[{"value": q, "label": QUALITIES[q][2]} for q in items(deck, level)])
    if deck == "ear-progressions":
        mode, pattern = item.split(":", 1)
        tonic = rng.choice(MAJOR_ROOTS if mode == "major" else MINOR_ROOTS)
        key = _key_of(tonic, mode)
        chords = _prog_chords(pattern, key)
        path = smoothest([c["pcs"] for c in chords])
        tonic_chord = _prog_chords("I" if mode == "major" else "i", key)
        context = smoothest([tonic_chord[0]["pcs"]])[0]
        pool = [p for p in _prog_pool(mode, own if level >= 2 else []) if p != pattern]
        same = [p for p in pool if len(split_pattern(p)) == len(chords)]
        distract = rng.sample(same, min(3, len(same)))
        distract += rng.sample([p for p in pool if p not in distract], max(0, 3 - len(distract)))
        options = distract + [pattern]
        rng.shuffle(options)
        mine = {p["pattern"] for p in own if p.get("mode") == mode}
        return _base(deck, level, "ear", item, f"Which progression? It's in a {mode} key; the home chord plays first.",
                     {"pattern": pattern, "tonic": tonic, "mode": mode},
                     play=[{"notes": [36 + key[0]] + list(context), "dur": 1.3}, {"notes": [], "dur": 0.7}] + _with_bass(path, chords),
                     choices=[{"value": p, "label": p, "title": "from your recordings" if p in mine else ""} for p in options])
    if deck == "play-chords":
        m = re.match(r"^([A-G][#b]?)(.*)$", item)
        root, quality = m.group(1), next(q for q, v in QUALITIES.items() if v[1] == m.group(2))
        spelled = chord_spelling(root, quality)
        pcs = [T.H.pc(n) for n in spelled]
        return _base(deck, level, "play-chord", item, f"Play {item} ({QUALITIES[quality][2]}), any inversion",
                     {"root": root, "quality": quality}, targets=[pcs], hint=[spelled],
                     show=[list(smoothest([pcs])[0])])
    if deck == "play-scales":
        tonic, scale = item.split(" ", 1)
        spelled = scale_spelling(tonic, scale)
        pcs = [T.H.pc(n) for n in spelled] + [T.H.pc(tonic)]
        start = 60 + pcs[0] if pcs[0] < 5 else 48 + pcs[0]          # C..E from middle C, F..B below it
        show, cur = [start], start
        for p in pcs[1:]:
            cur += (p - cur) % 12 or 12
            show.append(cur)
        return _base(deck, level, "play-scale", item, f"Play {tonic} {scale}, one octave up from {tonic}",
                     {"tonic": tonic, "scale": scale}, sequence=pcs, hint=[spelled + [tonic]], show=[show])
    if deck == "play-progressions":
        m = re.match(r"^(.+) in ([A-G][#b]?) (major|minor)$", item)
        pattern, tonic, mode = m.groups()
        key = _key_of(tonic, mode)
        chords = _prog_chords(pattern, key)
        path = smoothest([c["pcs"] for c in chords])
        return _base(deck, level, "play-progression", item,
                     f"Play {pattern} in {tonic} {mode}: " + " ".join(c["chord"] for c in chords) + ", one chord at a time",
                     {"pattern": pattern, "tonic": tonic, "mode": mode},
                     targets=[c["pcs"] for c in chords], labels=[f"{c['numeral']} {c['chord']}" for c in chords],
                     hint=[c["notes"] for c in chords], show=[list(v) for v in path],
                     play=_with_bass(path, chords))
    raise PracticeError(f"unknown deck {deck!r}")


# ── grading ──────────────────────────────────────────────────────────────────

def _chord_check(played: list[int], pcs: list[int], names: list[str]) -> tuple[bool, str]:
    got = {n % 12 for n in played}
    want = set(pcs)
    if got == want:
        bass = min(played) % 12
        inv = pcs.index(bass)
        return True, INVERSIONS[inv] + ("" if inv == 0 else f" ({names[inv]} in the bass)")
    spelled = dict(zip(pcs, names))
    missing = [spelled[p] for p in pcs if p not in got]
    extra = sorted({note_name(n) for n in played if n % 12 not in want})
    parts = ([f"missing {', '.join(missing)}"] if missing else []) + ([f"not in the chord: {', '.join(extra)}"] if extra else [])
    return False, "; ".join(parts) or "no notes"


def grade(ex: dict, answer) -> dict:
    """-> {correct, expected, detail[, value, show, movement, smoothest]} from ex['spec'] only.
    value = the right choice (heard exercises), so the page can mark it."""
    kind, spec, deck = ex.get("kind"), ex.get("spec") or {}, ex.get("deck")
    if deck == "ear-intervals":
        name = next(n for n, s, _ in INTERVALS if s == spec["semis"])
        ok = answer == name
        return {"correct": ok, "value": name, "expected": f"{name} — {INTERVAL[name][1]}",
                "detail": "" if ok else f"You said {answer}: {INTERVAL.get(answer, ('', '?'))[1]}."}
    if deck == "ear-chords":
        q = spec["quality"]
        ok = answer == q
        root = FLAT_NAMES[spec["root"] % 12]
        return {"correct": ok, "value": q, "expected": f"{QUALITIES[q][2]} ({chord_symbol(root, q)}, {INVERSIONS[spec.get('inversion', 0)]})",
                "detail": "" if ok else f"You said {QUALITIES.get(answer, ('', '', '?'))[2]}.",
                "show": [_chord_midi(spec["root"], q, spec.get("inversion", 0))]}
    if deck == "ear-progressions":
        key = _key_of(spec["tonic"], spec["mode"])
        chords = _prog_chords(spec["pattern"], key)
        ok = answer == spec["pattern"]
        return {"correct": ok, "value": spec["pattern"], "expected": f"{spec['pattern']} — in {spec['tonic']} {spec['mode']}: " + " ".join(c["chord"] for c in chords),
                "detail": "" if ok else f"You said {answer}.",
                "show": [list(v) for v in smoothest([c["pcs"] for c in chords])]}
    if kind == "play-chord":
        names = chord_spelling(spec["root"], spec["quality"])
        pcs = [T.H.pc(n) for n in names]
        ok, detail = _chord_check(list(answer or []), pcs, names)
        return {"correct": ok, "expected": f"{chord_symbol(spec['root'], spec['quality'])}: {' '.join(names)}",
                "detail": detail, "show": [list(smoothest([pcs])[0])]}
    if kind == "play-scale":
        names = scale_spelling(spec["tonic"], spec["scale"]) + [spec["tonic"]]
        pcs = [T.H.pc(n) for n in names]
        notes = list(answer or [])
        for i, want in enumerate(pcs):
            if i >= len(notes):
                return {"correct": False, "expected": " ".join(names), "detail": f"stopped after {i} notes"}
            if notes[i] % 12 != want:
                return {"correct": False, "expected": " ".join(names),
                        "detail": f"note {i + 1}: you played {note_name(notes[i])}, the scale has {names[i]}"}
            if i and notes[i] <= notes[i - 1]:
                return {"correct": False, "expected": " ".join(names), "detail": f"note {i + 1} went down; play it going up"}
        return {"correct": True, "expected": " ".join(names), "detail": ""}
    if kind == "play-progression":
        key = _key_of(spec["tonic"], spec["mode"])
        chords = _prog_chords(spec["pattern"], key)
        groups = [list(g) for g in (answer or [])]
        details, ok_all = [], len(groups) == len(chords)
        for i, c in enumerate(chords):
            if i >= len(groups):
                details.append(f"{c['chord']}: not played")
                ok_all = False
                continue
            ok, d = _chord_check(groups[i], c["pcs"], c["notes"])
            ok_all &= ok
            details.append(f"{c['chord']}: {'✓ ' + d if ok else d}")
        best = smoothest([c["pcs"] for c in chords])
        out = {"correct": ok_all, "expected": " ".join(c["chord"] for c in chords), "detail": " · ".join(details),
               "show": [list(v) for v in best]}
        if ok_all and len(groups) > 1:
            n = len(chords[0]["pcs"])
            hands = [sorted(g)[-n:] for g in groups]           # the top notes: the right hand
            out["movement"], out["smoothest"] = path_movement(hands), path_movement(best)
        return out
    raise PracticeError(f"cannot grade {deck}/{kind}")


# ── progress ─────────────────────────────────────────────────────────────────

def minutes(times: list[datetime], gap_s: int = 120, first_s: int = 20) -> float:
    """Practice time from attempt times: gaps count up to 2 min each (a pause is not practice)."""
    times = sorted(times)
    if not times:
        return 0.0
    s = first_s + sum(min((b - a).total_seconds(), gap_s) for a, b in zip(times, times[1:]))
    return round(s / 60, 1)


def day_streak(days: set[date], today: date) -> int:
    """Days in a row with practice, ending today (or yesterday, if today isn't done yet)."""
    d = today if today in days else today - timedelta(days=1)
    n = 0
    while d in days:
        n, d = n + 1, d - timedelta(days=1)
    return n
