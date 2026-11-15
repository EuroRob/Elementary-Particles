"""
studio_drift -- measure the timing gap between the two studio interfaces
(PreSonus Studio 24c and Zoom R24 in the "Studio" aggregate) from a click
recording, and whether that gap drifts over time. IaC/docs/STUDIO_DRIFT.md.

Two tests, same analysis:
  * output loopback: Live plays a click to both interfaces; both headphone outs
    are cabled back into two inputs of ONE interface and recorded. The gap is
    the difference between the two outputs.
  * input split: one click source is split (Y-cable) into a 24c input and an
    R24 input. The gap is the Track Delay to set on R24 tracks.

How it works:
  1. Coarse click times from the qm-vamp-plugins onset detector (built in the
     music-analysis image, next to Chordino), run by vamp-simple-host. Without
     Vamp (e.g. on a laptop) a simple energy detector is used instead.
  2. Around each click, the reference and test channels are cross-correlated;
     a parabola through the peak gives the gap to a fraction of a sample
     (1 sample = 0.023 ms at 44.1 kHz).
  3. A straight line through gap-vs-time gives the fixed offset and the drift
     rate; the leftovers show jitter and jumps (dropouts).

Positive gap = the test channel (R24) is LATER than the reference (24c).

Input: a folder (the newest WAV/AIFF with "24c" and the newest with "R24" as
a separate word in the file name, as Live names recordings after the track),
two files (reference, test), or one stereo file (left = reference).

Writes <name>.csv (time_s, gap_ms) -- open it in Sonic Visualiser over the
recording (File > Import Annotation Layer) to see the gap over time -- and
<name>.json with the summary. Memory stays small: audio is memory-mapped and
only the windows around clicks are read.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np

AUDIO_EXT = (".wav", ".aif", ".aiff")
REF_RE = re.compile(r"(?<![a-z0-9])24c(?![a-z0-9])", re.I)
TEST_RE = re.compile(r"(?<![a-z0-9])r24(?![a-z0-9])", re.I)

STABLE_MS = 0.1        # start-to-end change below this = drift correction working
JUMP_MS = 0.5          # a single click this far off the line = a jump (dropout, buffer slip)


# ── Audio access ─────────────────────────────────────────────────────────────

class Wav:
    """Memory-mapped WAV (PCM 16/24/32-bit or float 32/64, any channel count)."""

    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as f:
            head = f.read(12)
            if head[:4] not in (b"RIFF", b"RF64") or head[8:12] != b"WAVE":
                raise ValueError(f"not a WAV file: {path}")
            fmt = None
            data_off = data_len = None
            ds64_len = None
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                cid, size = hdr[:4], struct.unpack("<I", hdr[4:])[0]
                if cid == b"ds64":
                    body = f.read(size)
                    ds64_len = struct.unpack("<Q", body[8:16])[0]
                elif cid == b"fmt ":
                    body = f.read(size)
                    tag, ch, rate, _, block, bits = struct.unpack("<HHIIHH", body[:16])
                    if tag == 0xFFFE and size >= 40:                  # WAVE_FORMAT_EXTENSIBLE
                        tag = struct.unpack("<H", body[24:26])[0]
                    fmt = (tag, ch, rate, block, bits)
                elif cid == b"data":
                    data_off = f.tell()
                    data_len = ds64_len if size == 0xFFFFFFFF and ds64_len else size
                    break
                else:
                    f.seek(size + (size & 1), 1)
                if cid in (b"fmt ", b"ds64") and size & 1:
                    f.seek(1, 1)
        if not fmt or data_off is None:
            raise ValueError(f"WAV without fmt/data chunk: {path}")
        tag, self.channels, self.rate, self.block, self.bits = fmt
        if tag not in (1, 3):
            raise ValueError(f"unsupported WAV encoding {tag}: {path}")
        self.float = tag == 3
        self.width = self.bits // 8
        self.frames = min(data_len, os.path.getsize(path) - data_off) // self.block
        self.raw = np.memmap(path, dtype=np.uint8, mode="r", offset=data_off, shape=(self.frames * self.block,))

    def read(self, channel: int, start: int, n: int) -> np.ndarray:
        """n frames of one channel from frame `start`, float32 in [-1, 1]; zero-padded outside the file."""
        out = np.zeros(n, dtype=np.float32)
        a, b = max(start, 0), min(start + n, self.frames)
        if b <= a:
            return out
        chunk = np.asarray(self.raw[a * self.block:b * self.block]).reshape(b - a, self.block)
        cell = chunk[:, channel * self.width:(channel + 1) * self.width]
        if self.float:
            vals = cell.copy().view("<f4" if self.width == 4 else "<f8")[:, 0].astype(np.float32)
        elif self.width == 3:
            v = (cell[:, 0].astype(np.int32) | (cell[:, 1].astype(np.int32) << 8) | (cell[:, 2].astype(np.int32) << 16))
            vals = (np.where(v & 0x800000, v - 0x1000000, v) / 8388608.0).astype(np.float32)
        elif self.width == 2:
            vals = (cell.copy().view("<i2")[:, 0] / 32768.0).astype(np.float32)
        elif self.width == 4:
            vals = (cell.copy().view("<i4")[:, 0] / 2147483648.0).astype(np.float32)
        else:
            raise ValueError(f"unsupported sample width {self.bits} bits")
        out[a - start:b - start] = vals
        return out


def open_audio(path: str, tmpdir: str) -> Wav:
    """WAV directly; anything else (AIFF) is converted to a float WAV with ffmpeg first."""
    try:
        return Wav(path)
    except ValueError:
        if not shutil.which("ffmpeg"):
            raise SystemExit(f"{os.path.basename(path)}: not a WAV and ffmpeg isn't installed to convert it")
        dst = os.path.join(tmpdir, os.path.basename(path) + ".wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-c:a", "pcm_f32le", dst], check=True)
        return Wav(dst)


# ── Finding the files ────────────────────────────────────────────────────────

def pick_pair(folder: str) -> tuple[str, str]:
    """Newest recording with "24c" and newest with "R24" in the name, anywhere under folder."""
    best: dict[str, tuple[float, str]] = {}
    for root, _, files in os.walk(folder):
        for name in files:
            if not name.lower().endswith(AUDIO_EXT) or name.startswith("._"):
                continue
            p = os.path.join(root, name)
            for role, rx in (("ref", REF_RE), ("test", TEST_RE)):
                if rx.search(name):
                    m = os.path.getmtime(p)
                    if role not in best or m > best[role][0]:
                        best[role] = (m, p)
    missing = [r for r in ("ref", "test") if r not in best]
    if missing:
        raise SystemExit(f"no recording with {' and '.join('24c' if r == 'ref' else 'R24' for r in missing)} "
                         f"in its name under {folder}; name the two Live tracks e.g. 'Loop 24c' and 'Loop R24'")
    return best["ref"][1], best["test"][1]


def resolve(arg: str, root: Optional[str]) -> str:
    """A path as given, or relative to the recordings root, or a Live set name under Ableton Live/Projects."""
    cands = [arg]
    if root:
        cands += [os.path.join(root, arg), os.path.join(root, "Ableton Live", "Projects", arg),
                  os.path.join(root, "Ableton Live", "Projects", arg + " Project")]
    for c in cands:
        if os.path.exists(c):
            return c
    raise SystemExit(f"not found: {arg}")


# ── Onsets ───────────────────────────────────────────────────────────────────

_VAMP_LINE = re.compile(r"^\s*(-?\d+(?:\.\d+)?):")


def onsets_vamp(path: str) -> Optional[list[float]]:
    """qm-vamp-plugins onset detector via vamp-simple-host; None if Vamp isn't available."""
    if not shutil.which("vamp-simple-host"):
        return None
    try:
        r = subprocess.run(["vamp-simple-host", "qm-vamp-plugins:qm-onsetdetector:onsets", path],
                           capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    times = [float(m.group(1)) for line in r.stdout.splitlines() if (m := _VAMP_LINE.match(line))]
    return times or None


def onsets_energy(w: Wav, channel: int, min_gap_s: float = 0.1) -> list[float]:
    """Rising edges of a 1 ms peak envelope over 30 % of the loud clicks' level."""
    hop = max(1, w.rate // 1000)
    env = []
    step = hop * 60000                                   # one minute per read
    for s in range(0, w.frames, step):
        x = np.abs(w.read(channel, s, min(step, w.frames - s)))
        k = len(x) // hop
        env.append(x[:k * hop].reshape(k, hop).max(axis=1) if k else np.zeros(0, np.float32))
    e = np.concatenate(env) if env else np.zeros(0)
    if not len(e) or e.max() <= 0:
        return []
    thr = 0.3 * np.percentile(e, 99.9)
    above = e > thr
    edges = np.flatnonzero(above[1:] & ~above[:-1]) + 1
    out, last = [], -1e9
    for i in edges:
        t = i * hop / w.rate
        if t - last >= min_gap_s:
            out.append(t)
            last = t
    return out


# ── Measuring ────────────────────────────────────────────────────────────────

@dataclass
class Click:
    t: float          # seconds into the recording
    gap_ms: float     # test minus reference
    corr: float       # normalised correlation at the peak, 0..1


def measure(ref: Wav, rch: int, test: Wav, tch: int, times: list[float],
            max_lag_ms: float = 50.0, min_corr: float = 0.5) -> tuple[list[Click], int]:
    rate = ref.rate
    pre, post = int(0.010 * rate), int(0.040 * rate)
    lag = int(max_lag_ms / 1000 * rate)
    n = pre + post
    nfft = 1 << int(np.ceil(np.log2(n + 2 * lag + n)))
    clicks, rejected = [], 0
    for t in times:
        c = int(round(t * rate))
        a = ref.read(rch, c - pre, n).astype(np.float64)
        b = test.read(tch, c - pre - lag, n + 2 * lag).astype(np.float64)
        a -= a.mean()
        ea = np.sqrt(np.sum(a * a))
        if ea < 1e-6:
            rejected += 1
            continue
        # cross-correlation r[k] = sum a[i] * b[i + k], k = 0 .. 2*lag (k = lag means no shift)
        r = np.fft.irfft(np.conj(np.fft.rfft(a, nfft)) * np.fft.rfft(b, nfft), nfft)[:2 * lag + 1]
        k = int(np.argmax(r))
        eb = np.sqrt(np.sum(b[k:k + n] ** 2))
        corr = float(r[k] / (ea * eb)) if eb > 0 else 0.0
        if corr < min_corr or k in (0, 2 * lag):
            rejected += 1
            continue
        y0, y1, y2 = r[k - 1], r[k], r[k + 1]
        den = y0 - 2 * y1 + y2
        frac = 0.5 * (y0 - y2) / den if den else 0.0
        clicks.append(Click(t, (k - lag + frac) / rate * 1000.0, corr))
    return clicks, rejected


@dataclass
class Summary:
    clicks: int
    rejected: int
    minutes: float
    offset_start_ms: float
    offset_end_ms: float
    change_ms: float
    drift_ms_per_hour: float
    drift_ppm: float
    jitter_ms: float
    max_dev_ms: float
    jumps: int
    verdict: str
    advice: str


def summarise(clicks: list[Click], rejected: int) -> Summary:
    if len(clicks) < 5:
        raise SystemExit(f"only {len(clicks)} usable clicks ({rejected} rejected): check the levels and that both "
                         f"files hold the same click track")
    t = np.array([c.t for c in clicks])
    g = np.array([c.gap_ms for c in clicks])
    keep = np.abs(g - np.median(g)) <= max(5 * np.median(np.abs(g - np.median(g))), 0.05)
    slope, icpt = np.polyfit(t[keep], g[keep], 1) if keep.sum() >= 2 else (0.0, float(np.median(g)))
    resid = g - (slope * t + icpt)
    jumps = int(np.sum(np.abs(resid) > JUMP_MS))
    start, end = icpt + slope * t[0], icpt + slope * t[-1]
    change = end - start
    if jumps or rejected > 0.05 * (len(clicks) + rejected):
        verdict = "jumpy"
        advice = ("The gap jumps: clicks or dropouts. Raise the buffer, keep both interfaces on the dock (not the hub), "
                  "and tick drift correction on the R24 row only.")
    elif abs(change) <= STABLE_MS:
        verdict = "stable"
        advice = (f"Drift correction is working. Only a fixed offset of {np.median(g):+.2f} ms is left"
                  f" (positive = R24 later). For the input-split test, that is the Track Delay for R24 tracks.")
    else:
        verdict = "drifting"
        advice = ("The gap grows over time: drift correction isn't doing its job. Check Audio MIDI Setup: clock "
                  "source = Studio 24c, drift correction ticked on the R24 row only, both at 44,100 Hz.")
    return Summary(len(clicks), rejected, round((t[-1] - t[0]) / 60, 2), round(start, 3), round(end, 3),
                   round(change, 3), round(slope * 3600, 3), round(slope * 1000, 3),
                   round(float(np.std(resid[keep])), 4), round(float(np.max(np.abs(resid))), 3), jumps, verdict, advice)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Measure the 24c/R24 timing gap and drift from a click recording.")
    ap.add_argument("paths", nargs="+", help="folder | reference file + test file | one stereo file")
    ap.add_argument("--root", default=os.environ.get("RECORDINGS_ROOT"), help="recordings root for relative names")
    ap.add_argument("--out", default=".", help="folder for the .csv and .json")
    ap.add_argument("--max-lag-ms", type=float, default=50.0)
    ap.add_argument("--onsets", choices=("auto", "vamp", "energy"), default="auto")
    args = ap.parse_args(argv)

    paths = [resolve(p, args.root) for p in args.paths]
    if len(paths) == 1 and os.path.isdir(paths[0]):
        paths = list(pick_pair(paths[0]))
    with tempfile.TemporaryDirectory() as tmp:
        if len(paths) == 1:
            ref = test = open_audio(paths[0], tmp)
            if ref.channels < 2:
                raise SystemExit("one file given but it is mono: give the 24c and R24 files, or a folder")
            rch, tch = 0, 1
        elif len(paths) == 2:
            ref, test = open_audio(paths[0], tmp), open_audio(paths[1], tmp)
            rch = tch = 0
            if ref.rate != test.rate:
                raise SystemExit(f"sample rates differ ({ref.rate} vs {test.rate} Hz)")
        else:
            raise SystemExit("give one folder, two files or one stereo file")

        times, method = None, "energy"
        if args.onsets in ("auto", "vamp"):
            times = onsets_vamp(ref.path)
            method = "qm-vamp-plugins:qm-onsetdetector"
            if times is None and args.onsets == "vamp":
                raise SystemExit("vamp-simple-host / qm-vamp-plugins not available")
        if times is None:
            times, method = onsets_energy(ref, rch), "energy envelope"
        clicks, rejected = measure(ref, rch, test, tch, times, args.max_lag_ms)
        s = summarise(clicks, rejected)

    base = os.path.splitext(os.path.basename(paths[0]))[0]
    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, f"drift - {base}.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        for c in clicks:
            f.write(f"{c.t:.6f},{c.gap_ms:.4f}\n")
    json_path = os.path.join(args.out, f"drift - {base}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"reference": paths[0], "test": paths[-1], "sample_rate": ref.rate, "onsets": method,
                   **asdict(s)}, f, indent=2)

    print(f"Reference (24c): {os.path.basename(paths[0])}")
    print(f"Test (R24):      {os.path.basename(paths[-1]) if len(paths) > 1 else '(right channel)'}")
    print(f"Clicks found by: {method}")
    print(f"Clicks used:     {s.clicks} over {s.minutes} min ({s.rejected} rejected)")
    print(f"Gap at start:    {s.offset_start_ms:+.3f} ms   (positive = R24 later)")
    print(f"Gap at end:      {s.offset_end_ms:+.3f} ms")
    print(f"Change:          {s.change_ms:+.3f} ms  = {s.drift_ms_per_hour:+.3f} ms/hour, {s.drift_ppm:+.3f} ppm")
    print(f"Jitter:          {s.jitter_ms:.4f} ms (std), worst {s.max_dev_ms:.3f} ms, jumps > {JUMP_MS} ms: {s.jumps}")
    print(f"Verdict:         {s.verdict.upper()}. {s.advice}")
    print(f"Wrote:           {csv_path}  (Sonic Visualiser: File > Import Annotation Layer)")
    return 0 if s.verdict == "stable" else 2


if __name__ == "__main__":
    sys.exit(main())
