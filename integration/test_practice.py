"""Unit tests for the practice drills (practice.py): spelling, grading, voicing and
what comes next. No database, no browser.

Run:  pytest -q test_practice.py
"""
import random
from datetime import date, datetime, timedelta

import pytest

import practice as P


# ── spelling ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("root,quality,want", [
    ("Eb", "min", ["Eb", "Gb", "Bb"]),
    ("F#", "dim", ["F#", "A", "C"]),
    ("Bb", "maj7", ["Bb", "D", "F", "A"]),
    ("C", "dim7", ["C", "Eb", "Gb", "Bbb"]),
    ("F#", "aug", ["F#", "A#", "C##"]),
    ("G#", "min", ["G#", "B", "D#"]),
])
def test_chord_spelling_by_letter(root, quality, want):
    assert P.chord_spelling(root, quality) == want


def test_scale_spelling():
    assert P.scale_spelling("D", "dorian") == ["D", "E", "F", "G", "A", "B", "C"]
    assert P.scale_spelling("F#", "major")[-1] == "E#"
    assert P.scale_spelling("A", "minor pentatonic") == ["A", "C", "D", "E", "G"]
    assert P.scale_spelling("E", "harmonic minor")[-1] == "D#"


def test_numeral_chord_minor_key_V_is_major():
    key = P._key_of("A", "minor")
    assert P.numeral_chord("V", key)[:2] == ("E", "maj")
    assert P.numeral_chord("v", key)[:2] == ("Em", "min")


# ── voicing ──────────────────────────────────────────────────────────────────

def test_voicings_stay_in_range_and_close():
    for v in P.voicings([0, 4, 7]):
        assert P.LOW <= v[0] and v[-1] <= P.HIGH
        assert v[-1] - v[0] < 12


def test_smoothest_ii_V_I_moves_little():
    key = P._key_of("C", "major")
    chords = P._prog_chords("ii–V–I", key)
    path = P.smoothest([c["pcs"] for c in chords])
    assert [sorted(n % 12 for n in v) for v in path] == [[2, 5, 9], [2, 7, 11], [0, 4, 7]]
    assert P.path_movement(path) == 7          # Dm -> G/D -> C/E: common tone D held, then G
    root_positions = [(62, 65, 69), (67, 71, 74), (60, 64, 67)]
    assert P.path_movement(path) < P.path_movement(root_positions)


# ── grading: played ──────────────────────────────────────────────────────────

def _ex(deck, item, level=3):
    return P.make(deck, level, item, random.Random(1))


def test_play_chord_any_inversion_is_right():
    ex = _ex("play-chords", "Ebm")
    r = P.grade(ex, [58, 63, 66])              # Bb Eb Gb: 2nd inversion
    assert r["correct"] and "2nd inversion" in r["detail"] and "Bb in the bass" in r["detail"]


def test_play_chord_wrong_note_named():
    r = P.grade(_ex("play-chords", "Ebm"), [63, 67, 70])      # Eb G Bb = Eb major
    assert not r["correct"]
    assert "missing Gb" in r["detail"] and "not in the chord: G" in r["detail"]


def test_play_chord_doubling_is_fine():
    assert P.grade(_ex("play-chords", "C"), [48, 60, 64, 67, 72])["correct"]


def test_play_scale():
    ex = _ex("play-scales", "D dorian", level=2)
    assert P.grade(ex, [62, 64, 65, 67, 69, 71, 72, 74])["correct"]
    r = P.grade(ex, [62, 64, 66, 67, 69, 71, 72, 74])
    assert not r["correct"] and "note 3" in r["detail"] and "F" in r["detail"]
    assert "went down" in P.grade(ex, [62, 64, 65, 55, 69, 71, 72, 74])["detail"]
    assert "stopped after 3" in P.grade(ex, [62, 64, 65])["detail"]


def test_play_progression_and_voice_leading_hint():
    ex = _ex("play-progressions", "ii–V–I in C major")
    jumpy = [[62, 65, 69], [67, 71, 74], [72, 76, 79]]          # root positions, hand jumps
    r = P.grade(ex, jumpy)
    assert r["correct"] and r["movement"] > r["smoothest"]
    smooth = [[38, 62, 65, 69], [43, 62, 67, 71], [36, 60, 64, 67]]   # bass + right hand
    r2 = P.grade(ex, smooth)
    assert r2["correct"] and r2["movement"] <= r["movement"]
    bad = P.grade(ex, [[62, 65, 69], [67, 71, 74]])
    assert not bad["correct"] and "C: not played" in bad["detail"]


def test_grading_ignores_what_the_page_claims():
    ex = _ex("play-chords", "C")
    ex["targets"] = [[1, 2, 3]]                 # tampered; grade uses spec
    assert P.grade(ex, [60, 64, 67])["correct"]


# ── grading: heard ───────────────────────────────────────────────────────────

def test_ear_interval():
    ex = _ex("ear-intervals", "P5 down")
    assert ex["play"][0]["notes"][0] - ex["play"][1]["notes"][0] == 7
    assert P.grade(ex, "P5")["correct"]
    assert "perfect 4th" in P.grade(ex, "P4")["detail"]


def test_ear_chord_and_inversion_shown():
    ex = P.make("ear-chords", 2, "dim", random.Random(3))
    r = P.grade(ex, "dim")
    assert r["correct"] and "dim" in r["expected"]
    assert not P.grade(ex, "min")["correct"]


def test_ear_progression_choices_include_answer_once():
    own = [{"pattern": "I–bVI–bVII", "mode": "major"}]
    for seed in range(30):
        ex = P.make("ear-progressions", 2, "major:I–V–vi–IV", random.Random(seed), own)
        values = [c["value"] for c in ex["choices"]]
        assert values.count("I–V–vi–IV") == 1 and len(set(values)) == 4
    assert P.grade(ex, "I–V–vi–IV")["correct"]


# ── every item at every level builds and grades ─────────────────────────────

def test_every_item_builds_and_grades():
    own = [{"pattern": "I–V–IV", "mode": "major"}, {"pattern": "i–iv–bvii", "mode": "minor"},
           {"pattern": "I–?–IV", "mode": "major"}]                 # unparseable own pattern is dropped
    rng = random.Random(7)
    for deck, d in P.DECKS.items():
        for level in d["levels"]:
            its = P.items(deck, level, own)
            assert its, (deck, level)
            assert not any("?" in i for i in its)
            for item in its:
                ex = P.make(deck, level, item, rng, own)
                answer = (ex["spec"].get("pattern") if deck == "ear-progressions" else
                          ex["choices"][0]["value"] if ex["kind"] == "ear" else
                          ex["show"][0] if ex["kind"] == "play-scale" else
                          ex["show"][0] if ex["kind"] == "play-chord" else ex["show"])
                r = P.grade(ex, answer)
                if ex["kind"] != "ear" or deck == "ear-progressions":
                    assert r["correct"], (deck, level, item, r)


def test_unknown_deck_and_level():
    with pytest.raises(P.PracticeError):
        P.check_level("ear-nope", 1)
    with pytest.raises(P.PracticeError):
        P.check_level("ear-chords", 9)


# ── what comes next ──────────────────────────────────────────────────────────

def test_missed_items_come_back_more():
    cands = ["a", "b", "c", "d"]
    hist = {"a": [False, False, True], "b": [True] * 5, "c": [True] * 5, "d": [True] * 5}
    rng = random.Random(0)
    picks = [P.choose(cands, hist, rng) for _ in range(2000)]
    assert picks.count("a") > 3 * picks.count("b")


def test_unseen_beats_mastered_and_last_item_avoided():
    assert P.weight(None) > P.weight([True] * 5)
    rng = random.Random(0)
    picks = [P.choose(["a", "b"], {}, rng, last_item="a") for _ in range(500)]
    assert picks.count("a") < 50


# ── progress ─────────────────────────────────────────────────────────────────

def test_minutes_caps_pauses():
    t0 = datetime(2026, 10, 31, 18, 0)
    times = [t0 + timedelta(seconds=s) for s in (0, 10, 25, 40, 3600)]   # an hour's pause counts 2 min
    assert P.minutes(times) == pytest.approx((20 + 40 + 120) / 60, abs=0.1)
    assert P.minutes([]) == 0


def test_day_streak():
    today = date(2026, 10, 31)
    days = {today - timedelta(days=i) for i in (1, 2, 3)}
    assert P.day_streak(days, today) == 3              # today not done yet: still 3
    assert P.day_streak(days | {today}, today) == 4
    assert P.day_streak({today - timedelta(days=2)}, today) == 0
