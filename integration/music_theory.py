"""music_theory.py — chords, keys and arrangement maths for DOMO's studio partner.

Computed, never generated: the language model explains and suggests, but every
chord name, note and bar count it is given comes from here (built on harmony.py's
Roman-numeral helpers, the same ones the progression catalogue uses).
"""
import re

import harmony as H

NOTE_RE = re.compile(r"\b([A-G])([#b♯♭]?)\s*(major|minor|maj|min|dur|moll|m)?\b(?!\w)", re.I)
QUALITY_TONES = {"maj": (0, 4, 7), "min": (0, 3, 7), "dim": (0, 3, 6), "aug": (0, 4, 8)}

MAJOR = [("I", "maj"), ("ii", "min"), ("iii", "min"), ("IV", "maj"), ("V", "maj"), ("vi", "min"), ("vii°", "dim")]
MINOR = [("i", "min"), ("ii°", "dim"), ("bIII", "maj"), ("iv", "min"), ("v", "min"), ("bVI", "maj"), ("bVII", "maj")]
BORROWED = {"major": [("iv", "from the parallel minor: a soft, wistful IV"), ("bVI", "the 'epic' borrowed chord"),
                      ("bVII", "rock/mixolydian colour, great before I"), ("bIII", "bold, from minor")],
            "minor": [("IV", "dorian lift: brightens the minor"), ("V", "harmonic-minor V: a strong pull home"),
                      ("bII", "Neapolitan: dark and dramatic before V")]}
SECONDARY = {"major": ["ii", "iii", "IV", "V", "vi"], "minor": ["iv", "bVI", "bIII"]}


LETTERS = "CDEFGAB"
LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SCALE = (0, 2, 4, 5, 7, 9, 11)                  # numerals are relative to the major scale (minor: i–bVI–bIII–bVII)
# How a key is spelled when only its pitch is known (the common choice).
DEFAULT_TONIC = {"major": ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"],
                 "minor": ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"]}


def parse_key_text(text: str):
    """'D minor' / 'Dm' / 'F# major' / 'in Bb' -> (pc, mode, tonic spelling) or None. First key named wins."""
    for m in NOTE_RE.finditer(text or ""):
        letter, acc, mode = m.group(1).upper(), (m.group(2) or "").replace("♯", "#").replace("♭", "b"), (m.group(3) or "")
        if not mode and not re.search(rf"\b(?:in|key of|key)\s+{letter}{re.escape(acc)}\b", text, re.I):
            continue                                   # a bare "A" is too often just a word
        minor = mode.lower() in ("minor", "min", "moll", "m")
        return (H.pc(letter + acc), "minor" if minor else "major", letter + acc)
    return None


def make_key(pc: int, mode: str, tonic: str = "") -> tuple:
    return (pc % 12, mode, tonic or DEFAULT_TONIC[mode][pc % 12])


def _key(key) -> tuple:
    return key if len(key) == 3 else make_key(key[0], key[1])


def spell(letter: str, pc: int) -> str:
    """The note `pc` written on `letter`: spell('E', 5) -> 'E#', spell('B', 10) -> 'Bb'."""
    d = (pc - LETTER_PC[letter]) % 12
    d = d - 12 if d > 6 else d
    return letter + ("#" * d if d > 0 else "b" * -d)


def key_name(key) -> str:
    k = _key(key)
    return f"{k[2]} {k[1]}"


def _numeral(numeral: str, key):
    """-> (root letter, root pc, quality)."""
    k = _key(key)
    m = re.match(r"^([b#]?)([ivIV]+)([°ø+]?)", numeral.strip())
    if not m:
        return None
    acc, num, suf = m.groups()
    idx = ["I", "II", "III", "IV", "V", "VI", "VII"].index(num.upper())
    letter = LETTERS[(LETTERS.index(k[2][0]) + idx) % 7]
    pc = (k[0] + SCALE[idx] + (-1 if acc == "b" else 1 if acc == "#" else 0)) % 12
    quality = {"°": "dim", "ø": "dim", "+": "aug"}.get(suf) or ("min" if num.islower() else "maj")
    return letter, pc, quality


def chord_name(numeral: str, key) -> str:
    n = _numeral(numeral, key)
    if not n:
        return "?"
    letter, pc, q = n
    return spell(letter, pc) + {"maj": "", "min": "m", "dim": "dim", "aug": "aug"}[q]


def chord_tones(numeral: str, quality: str, key) -> list:
    """Note names to click in, root first, spelled by letter: vi in C -> ['A','C','E']."""
    letter, pc, _ = _numeral(numeral, key)
    i = LETTERS.index(letter)
    return [spell(LETTERS[(i + step) % 7], (pc + semis) % 12)
            for step, semis in zip((0, 2, 4), QUALITY_TONES[quality])]


def diatonic(key) -> list:
    rows = MAJOR if _key(key)[1] == "major" else MINOR
    return [{"numeral": n, "chord": chord_name(n, key), "notes": chord_tones(n, q, key)} for n, q in rows]


def borrowed(key) -> list:
    out = []
    for n, why in BORROWED[_key(key)[1]]:
        q = "min" if n.lstrip("b#").islower() else "maj"
        out.append({"numeral": n, "chord": chord_name(n, key), "notes": chord_tones(n, q, key), "why": why})
    return out


def secondary_dominants(key) -> list:
    """V/x: the major chord a fifth above x (written as a 7th chord), leading into it."""
    out = []
    for target in SECONDARY[_key(key)[1]]:
        letter, pc, _ = _numeral(target, key)
        five = LETTERS[(LETTERS.index(letter) + 4) % 7]
        out.append({"numeral": f"V/{target}", "chord": spell(five, (pc + 7) % 12) + "7",
                    "resolves_to": chord_name(target, key)})
    return out


def related_keys(key) -> dict:
    """Spelled by letter, like the chords: F# major -> relative D# minor."""
    k = _key(key)

    def at(numeral, mode):
        letter, pc, _ = _numeral(numeral, k)
        return f"{spell(letter, pc)} {mode}"

    if k[1] == "major":
        return {"relative": at("vi", "minor"), "parallel": f"{k[2]} minor",
                "dominant": at("V", "major"), "subdominant": at("IV", "major")}
    return {"relative": at("bIII", "major"), "parallel": f"{k[2]} major",
            "dominant": at("V", "minor"), "subdominant": at("iv", "minor")}


def realise_pattern(pattern: str, key) -> str:
    """'vi–IV–I–V' in C major -> 'Am F C G'. Unknown numerals stay as '?'."""
    return " ".join(chord_name(n.strip(), key) for n in re.split(r"[–-]", pattern) if n.strip())


_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "zwei": 2, "drei": 3, "vier": 4, "fünf": 5}


def parse_minutes(text: str):
    """'about three and a half minutes' / '3:30' / '3.5 min' / 'vier Minuten' -> 3.5 / 4.0, else None."""
    t = (text or "").lower()
    if m := re.search(r"\b(\d{1,2}):([0-5]\d)\b", t):
        return int(m.group(1)) + int(m.group(2)) / 60
    if m := re.search(r"\b(\d{1,2}(?:[.,]\d)?)\s*(?:min|minute|minuten)", t):
        return float(m.group(1).replace(",", "."))
    if m := re.search(r"\b(" + "|".join(_WORD_NUM) + r")(\s+and a half|\s+und einhalb|einhalb)?\s+(?:minutes?|minuten)", t):
        return _WORD_NUM[m.group(1)] + (0.5 if m.group(2) else 0)
    return None


def plan_line(plan: dict) -> str:
    return (f"Closest common form, {plan['length']} at {plan['tempo']:g} BPM ({plan['bar_seconds']} s a bar, "
            f"{plan['total_bars']} bars): " + ", ".join(f"{x['section']} {x['bars']} (bar {x['starts_bar']})"
                                                        for x in plan["sections"]) + ".")


def bar_seconds(tempo: float, time_signature: str = "4/4") -> float:
    n, d = (int(x) for x in time_signature.split("/"))
    return n * (4 / d) * 60 / tempo


# Common pop/rock forms in 8/16-bar phrases, shortest first. FOUND 2026-10-24: scaling
# one form made 12- and 20-bar sections and missed the asked length; musicians think in phrases.
FORMS = [
    [("Intro", 8), ("Verse 1", 16), ("Chorus", 16), ("Verse 2", 16), ("Chorus", 16)],                       # 72
    [("Intro", 8), ("Verse 1", 16), ("Chorus", 8), ("Verse 2", 16), ("Chorus", 8), ("Bridge", 8),
     ("Chorus", 16), ("Outro", 8)],                                                                           # 88
    [("Intro", 8), ("Verse 1", 16), ("Chorus", 16), ("Verse 2", 16), ("Chorus", 16), ("Bridge", 8),
     ("Chorus", 16), ("Outro", 8)],                                                                           # 104
    [("Intro", 8), ("Verse 1", 16), ("Pre-chorus", 8), ("Chorus", 16), ("Verse 2", 16), ("Pre-chorus", 8),
     ("Chorus", 16), ("Bridge", 8), ("Chorus", 16), ("Outro", 8)],                                            # 120
    [("Intro", 8), ("Verse 1", 16), ("Pre-chorus", 8), ("Chorus", 16), ("Verse 2", 16), ("Pre-chorus", 8),
     ("Chorus", 16), ("Instrumental", 16), ("Bridge", 8), ("Chorus", 16), ("Outro", 8)],                      # 136
]


def arrangement_plan(tempo: float, minutes: float = 3.5, time_signature: str = "4/4") -> dict:
    """The common form whose length at this tempo is closest to `minutes`, in bars and time."""
    sec = bar_seconds(tempo, time_signature)
    form = min(FORMS, key=lambda f: abs(sum(b for _, b in f) * sec - minutes * 60))
    out, bar = [], 1
    for name, bars in form:
        out.append({"section": name, "bars": bars, "starts_bar": bar, "starts_at": round((bar - 1) * sec, 1)})
        bar += bars
    return {"tempo": tempo, "bar_seconds": round(sec, 2), "total_bars": bar - 1,
            "length": f"{int((bar - 1) * sec // 60)}:{int((bar - 1) * sec % 60):02d}", "sections": out}


def theory_block(key, own_progressions: list = ()) -> str:
    """The facts lines a session gets for a key."""
    lines = [f"Key {key_name(key)}. Diatonic chords: " + ", ".join(
        f"{c['numeral']} {c['chord']} ({' '.join(c['notes'])})" for c in diatonic(key)) + "."]
    lines.append("Borrowed colours: " + "; ".join(f"{c['numeral']} {c['chord']} – {c['why']}" for c in borrowed(key)) + ".")
    lines.append("Secondary dominants: " + ", ".join(f"{c['chord']} → {c['resolves_to']}" for c in secondary_dominants(key)) + ".")
    rk = related_keys(key)
    lines.append(f"Related keys: relative {rk['relative']}, parallel {rk['parallel']}, dominant {rk['dominant']}.")
    mine = [p for p in own_progressions if p.get("mode") == _key(key)[1]][:5]
    if mine:
        lines.append("Progressions owner plays most (from his own recordings), in this key: " + "; ".join(
            f"{p['pattern']} = {realise_pattern(p['pattern'], key)} ({p['recordings']} recordings)" for p in mine) + ".")
    return "\n".join(lines)


# ── checking what the model wrote ────────────────────────────────────────────
# FOUND in the first live session test (2026-10-24): "bVII–V, so Am to G then
# back to Em" in A minor -- Em is v, not V. The model's chord talk is checked
# against the computed chords before owner hears it.
_PAIR = re.compile(r"(?<![A-Za-z/])([b#]?)(VII|VI|IV|V|III|II|I|vii|vi|iv|v|iii|ii|i)([°ø]?)\s*[(\[:=–-]?\s*"
                   r"([A-G][#b]?(?:m(?!aj)|dim|aug)?)(?![a-z#])")
_CHORD = re.compile(r"(?<![A-Za-z#])([A-G][#b]?)(m(?!aj)|dim|aug)?(7|maj7|sus2|sus4|add9|6|9)?(?![A-Za-z#0-9])")


def _triad(token: str) -> str:
    m = _CHORD.fullmatch(token.strip())
    return (m.group(1) + (m.group(2) or "")) if m else token


def check_chords(text: str, key, allowed: set = frozenset()) -> list:
    """Problems in a reply: a numeral paired with the wrong chord ("V (Em)" in A minor),
    or a chord that is neither in the key's lists nor in `allowed` (chords from the facts
    or his own words). Bare single letters are skipped: "A" is too often a word."""
    k = _key(key)
    problems = []
    for acc, num, suf, chord in _PAIR.findall(text):
        numeral = acc + num + suf
        want = chord_name(numeral, k)
        if want != "?" and _triad(chord) != want:
            problems.append(f"{numeral} is {want} in {key_name(k)}, not {chord}")
    known = {c["chord"] for c in diatonic(k) + borrowed(k)} | {c["resolves_to"] for c in secondary_dominants(k)} \
        | {c["chord"][:-1] for c in secondary_dominants(k)} | {_triad(a) for a in allowed}
    for m in _CHORD.finditer(text):
        token = m.group(0)
        if len(token) < 2 and not re.match(r"\s+(?:major|minor|chord)", text[m.end():m.end() + 7]):
            continue
        if _triad(token) not in known and not any(token in p for p in problems):
            problems.append(f"{token} isn't one of the chords listed for {key_name(k)}")
    return problems
