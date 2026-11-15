"""Tests for studio_drift.py with synthetic click recordings (known offset, drift and jumps)."""

import json
import os
import struct

import numpy as np
import pytest

import studio_drift as sd

RATE = 44100


def burst(t: np.ndarray) -> np.ndarray:
    """A click as a function of continuous time (s): Gaussian-windowed 3 kHz tone, so any delay is exact."""
    return np.exp(-((t - 0.002) / 0.0006) ** 2) * np.sin(2 * np.pi * 3000 * t)


def clicks(seconds: float, delay_ms=lambda t: 0.0, every: float = 0.5, noise: float = 0.002, seed: int = 1) -> np.ndarray:
    n = int(seconds * RATE)
    x = np.zeros(n)
    rng = np.random.default_rng(seed)
    for k in np.arange(0.25, seconds - 0.1, every):
        t0 = k + delay_ms(k) / 1000.0
        i0 = int(t0 * RATE) - 10
        idx = np.arange(i0, i0 + 300)
        x[idx] += 0.6 * burst(idx / RATE - t0)
    return x + noise * rng.standard_normal(n)


def write_wav(path, chans, bits=24, float_=False):
    data = np.stack(chans, axis=1)
    if float_:
        raw = data.astype("<f4").tobytes()
        tag, width = 3, 4
    elif bits == 24:
        v = np.clip(np.round(data * 8388607), -8388608, 8388607).astype("<i4")
        raw = v.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
        tag, width = 1, 3
    else:
        raw = np.clip(np.round(data * 32767), -32768, 32767).astype("<i2").tobytes()
        tag, width = 1, 2
    ch = data.shape[1]
    fmt = struct.pack("<HHIIHH", tag, ch, RATE, RATE * ch * width, ch * width, width * 8)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(raw)) + b"WAVE")
        f.write(b"fmt " + struct.pack("<I", len(fmt)) + fmt)
        f.write(b"LIST" + struct.pack("<I", 4) + b"INFO")          # an extra chunk Live-style files carry
        f.write(b"data" + struct.pack("<I", len(raw)) + raw)


def run(tmp_path, *paths):
    out = tmp_path / "out"
    code = sd.main([*map(str, paths), "--out", str(out), "--onsets", "energy"])
    js = [p for p in os.listdir(out) if p.endswith(".json")]
    return code, json.load(open(out / js[0]))


def test_stable_offset_two_files(tmp_path):
    a, b = clicks(120), clicks(120, lambda t: 3.217, seed=2)
    write_wav(tmp_path / "1-Loop 24c 0001.wav", [a])
    write_wav(tmp_path / "2-Loop R24 0001.wav", [b])
    code, s = run(tmp_path, tmp_path)                          # folder: picks the pair by name
    assert s["verdict"] == "stable" and code == 0
    assert s["offset_start_ms"] == pytest.approx(3.217, abs=0.005)
    assert abs(s["change_ms"]) < 0.01
    assert s["clicks"] >= 230


def test_drift_detected_stereo_float(tmp_path):
    secs = 180
    a, b = clicks(secs), clicks(secs, lambda t: -1.5 + 0.8 * t / secs, seed=3)   # 0.8 ms over 3 min
    write_wav(tmp_path / "loop.wav", [a, b], float_=True)
    code, s = run(tmp_path, tmp_path / "loop.wav")
    assert s["verdict"] == "drifting" and code == 2
    assert s["change_ms"] == pytest.approx(0.8, abs=0.02)
    assert s["drift_ms_per_hour"] == pytest.approx(0.8 * 3600 / secs, rel=0.05)
    assert s["offset_start_ms"] == pytest.approx(-1.5, abs=0.02)


def test_jump_detected_16bit(tmp_path):
    a, b = clicks(60), clicks(60, lambda t: 2.0 if t < 30 else 4.9, seed=4)       # buffer slip half-way
    write_wav(tmp_path / "x 24c.wav", [a], bits=16)
    write_wav(tmp_path / "x R24.wav", [b], bits=16)
    code, s = run(tmp_path, tmp_path / "x 24c.wav", tmp_path / "x R24.wav")
    assert s["verdict"] == "jumpy" and s["jumps"] > 0


def test_names_need_whole_word():
    assert sd.TEST_RE.search("2-Loop R24 0001 [2026-11-07].wav")
    assert not sd.TEST_RE.search("Return 24c.wav")
    assert not sd.REF_RE.search("Loop R24.wav")
    assert sd.REF_RE.search("1-Loop 24c 0001.wav")


def test_vamp_output_parsing():
    line = " 1.234500000: "
    assert sd._VAMP_LINE.match(line).group(1) == "1.234500000"
