#!/usr/bin/env python3
"""
music_analysis_worker.py — Musician Platform phase 2: chord / beat / key /
tempo / section analysis of every recording (>= 6 s), keyed on content
(music.audio_files.id). Long-running service in the music-analysis container
(IaC/services/assistant/music-studio/analysis/Dockerfile); see
IaC/docs/MUSICIAN_PLATFORM.md.

Per job: decode with ffmpeg to a temporary mono 44.1 kHz WAV (any format
ffmpeg reads), run each Vamp plugin with vamp-simple-host, parse, write
music.analysis_events (chords, sections, key changes) and one
music.audio_analysis summary row (global key, tempo, tuning, chord set,
beats/bars, waveform peaks). The source audio is only ever read.

Queue: music.analysis_runs rows, status 'queued' -> 'running' -> 'done' /
'failed' / 'skipped', claimed with FOR UPDATE SKIP LOCKED so CONCURRENCY
jobs (and more containers, if ever) never collide. The enqueue pass adds a
row for every recording >= MIN_SECONDS that has none for ANALYSIS_VERSION —
bump the version to re-analyse everything. Recordings linked to a song get
priority 10 (default 100) so the songbook fills in first.

    docker exec music-analysis python3 /app/music_analysis_worker.py --once <audio_id>   # one file, verbose
"""
import argparse
import asyncio
import json
import os
import re
import socket
import statistics
from array import array
import sys
import tempfile
import time
import uuid
from collections import defaultdict

import asyncpg
import httpx

import lead_sheet_draft

ANALYSIS_VERSION = os.getenv("ANALYSIS_VERSION", "v1")
MIN_SECONDS      = float(os.getenv("MUSIC_MIN_SECONDS", "6"))
CONCURRENCY      = int(os.getenv("ANALYSIS_CONCURRENCY", "2"))
SEGMENT_MAX_SEC  = float(os.getenv("SEGMENTINO_MAX_SECONDS", "1200"))   # sections are meaningless for 3-h field recordings
PLUGIN_TIMEOUT   = int(os.getenv("PLUGIN_TIMEOUT", "1800"))
MAX_ATTEMPTS     = 2
ENQUEUE_EVERY    = 600
WORKER           = socket.gethostname()
PEAKS_MAX        = 4000
# Only one long recording at a time: a 3-h WAV needs ~1.1 GB (measured), two
# at once plus page cache from the temp WAVs would brush the 3 GB mem_limit.
LONG_SECONDS     = 1200
# Phase 3 (draft lead sheets) — GPU work happens on gpu-02.
STEM_API_URL     = os.getenv("STEM_API_URL", "http://10.0.30.51:8020")
WHISPER_URL      = os.getenv("WHISPER_URL", "http://10.0.30.51:5000")
WHISPER_MODEL    = os.getenv("WHISPER_MODEL", "mobiuslabsgmbh/faster-whisper-large-v3-turbo")
WHISPER_FALLBACK_LANGUAGE = os.getenv("WHISPER_FALLBACK_LANGUAGE", "en")
STEMS_DIR        = os.getenv("STEMS_DIR", "/music/stems")
VOICED_MIN       = 0.03   # measured 2026-10-08: instrumental stem 0 %, sung vocal stem 25 %
_long_lock       = None   # asyncio.Lock, created in main()

PLUGINS = {   # name -> vamp-simple-host id
    "chords":   "nnls-chroma:chordino:simplechord",
    "tuning":   "nnls-chroma:tuning:tuning",
    "beats":    "qm-vamp-plugins:qm-barbeattracker:beats",
    "key":      "qm-vamp-plugins:qm-keydetector:key",
    "tempo":    "qm-vamp-plugins:qm-tempotracker:tempo",
    "sections": "segmentino:segmentino:segmentation",
}

# vamp-simple-host output lines:  " 12.345: label"   or   " 12.345, 6.789: value label"
LINE = re.compile(r"^\s*(-?[\d.]+)(?:,\s*([\d.]+))?:\s*(.*)$")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


async def run(cmd, timeout):
    p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        raise RuntimeError(f"timeout after {timeout}s: {cmd[0]} {cmd[1] if len(cmd) > 1 else ''}")
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} rc={p.returncode}: {err.decode(errors='replace')[-300:]}")
    return out


async def vamp(plugin_id, wav):
    with tempfile.NamedTemporaryFile(suffix=".txt") as out:
        await run(["vamp-simple-host", plugin_id, wav, "-o", out.name], PLUGIN_TIMEOUT)
        rows = []
        for line in open(out.name, encoding="utf-8", errors="replace"):
            m = LINE.match(line)
            if m:
                rows.append((float(m.group(1)), float(m.group(2)) if m.group(2) else None, m.group(3).strip()))
        return rows


async def peaks(wav, duration):
    """Envelope for the UI waveform: max |sample| per bucket, normalised to 0..1.
    Decoded at 4 kHz (enough for an envelope; resampling much lower would
    low-pass the music away), buckets via max()/min() on array slices — C speed."""
    raw = await run(["ffmpeg", "-v", "error", "-nostdin", "-i", wav, "-ac", "1", "-ar", "4000", "-f", "s16le", "-"], 900)
    samples = array("h"); samples.frombytes(raw[: len(raw) // 2 * 2])
    n = len(samples)
    if not n or not duration:
        return [], 0.0
    points = min(PEAKS_MAX, max(1, int(duration * 10)))
    step = max(1, n // points)
    env = []
    for i in range(0, n, step):
        chunk = samples[i:i + step]
        env.append(max(max(chunk), -min(chunk)))
    top = max(env) or 1
    return [round(v / top, 3) for v in env], len(env) / duration


def summarise(res, duration):
    s = {}
    # key: the one held longest
    keys = res.get("key") or []
    if keys:
        held = defaultdict(float)
        for i, (t, _, label) in enumerate(keys):
            end = keys[i + 1][0] if i + 1 < len(keys) else duration
            held[re.sub(r"^\d+\s+", "", label)] += max(0.0, end - t)
        k, d = max(held.items(), key=lambda kv: kv[1])
        s["key"], s["key_share"] = k, round(d / duration, 3) if duration else None
    tempos = []
    for t, _, label in res.get("tempo") or []:
        m = re.match(r"([\d.]+)", label)
        if m:
            tempos.append(float(m.group(1)))
    s["tempo_bpm"] = round(statistics.median(tempos), 2) if tempos else None
    tun = res.get("tuning") or []
    m = re.match(r"([\d.]+)", tun[0][2]) if tun else None
    s["tuning_hz"] = float(m.group(1)) if m else None
    chords = res.get("chords") or []
    held = defaultdict(float)
    for i, (t, _, label) in enumerate(chords):
        end = chords[i + 1][0] if i + 1 < len(chords) else duration
        if label != "N":
            held[label] += max(0.0, end - t)
    s["chords"] = [c for c, _ in sorted(held.items(), key=lambda kv: -kv[1])]
    s["n_sections"] = len(res["sections"]) if res.get("sections") is not None else None
    beats = res.get("beats") or []
    s["beats"] = [round(t, 3) for t, _, _ in beats]
    s["beat_numbers"] = [int(l) if l.isdigit() else 0 for _, _, l in beats]
    # Bars = downbeats. The tracker's separate "bars" output is the same run
    # again (+55 s on a 3-h file) for the same answer.
    s["bars"] = [b for b, n in zip(s["beats"], s["beat_numbers"]) if n == 1]
    return s


def events(res, duration):
    ev = []
    for kind, src in (("chord", "chords"), ("key", "key")):
        rows = res.get(src) or []
        for i, (t, _, label) in enumerate(rows):
            end = rows[i + 1][0] if i + 1 < len(rows) else duration
            if kind == "key":
                label = re.sub(r"^\d+\s+", "", label)
            ev.append((kind, round(t, 3), round(end, 3), label))
    for t, dur, label in res.get("sections") or []:
        # " 20.35, 20.95: 1 A" -> label "A"
        ev.append(("section", round(t, 3), round(t + (dur or 0), 3), label.split()[-1] if label else "?"))
    return ev


async def analyse(con, job, verbose=False):
    loc = await con.fetchrow(
        """SELECT l.path, f.duration_sec FROM music.audio_locations l JOIN music.audio_files f ON f.id = l.audio_id
           WHERE l.audio_id = $1 AND l.missing_since IS NULL ORDER BY l.path LIMIT 1""", job["audio_id"])
    if not loc:
        return "skipped", "no live location", None, None
    duration = float(loc["duration_sec"] or 0)
    timings, res = {}, {}
    with tempfile.TemporaryDirectory(prefix="ana-") as tmp:
        wav = os.path.join(tmp, "a.wav")
        t = time.time()
        p = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "warning", "-nostdin", "-i", loc["path"], "-ac", "1", "-ar", "44100", "-f", "wav", wav,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(p.communicate(), 1800)
        timings["decode"] = int((time.time() - t) * 1000)
        # ffmpeg exits 0 even when most packets fail (DRM-protected WMA in
        # Friends/James decoded 0.2 s of 435 s) — so check what actually came out.
        decoded = max(0, os.path.getsize(wav) - 44) / (2 * 44100) if os.path.exists(wav) else 0.0
        if duration and decoded < 0.5 * duration:
            why = "DRM-protected" if b"DRM protected" in err else "decode failed or file truncated"
            return "skipped", f"{why}: decoded {decoded:.1f}s of {duration:.1f}s", None, timings
        for name, pid in PLUGINS.items():
            if name == "sections" and duration > SEGMENT_MAX_SEC:
                continue
            t = time.time()
            try:
                res[name] = await vamp(pid, wav)
            except Exception as e:      # one plugin failing doesn't lose the others
                res[name] = None
                timings[f"{name}_error"] = str(e)[:200]
            timings[name] = int((time.time() - t) * 1000)
        t = time.time()
        pk, pk_rate = await peaks(wav, duration)
        timings["peaks"] = int((time.time() - t) * 1000)
    if verbose:
        log(json.dumps(timings))
    if all(v is None for v in res.values()):
        raise RuntimeError("every plugin failed: " + json.dumps({k: v for k, v in timings.items() if k.endswith("_error")}))
    return "done", None, (summarise(res, duration), events(res, duration), pk, pk_rate), timings


async def save(con, job, result, timings):
    summary, ev, pk, pk_rate = result
    async with con.transaction():
        await con.execute("DELETE FROM music.analysis_events WHERE run_id = $1", job["id"])
        await con.copy_records_to_table(
            "analysis_events", schema_name="music",
            columns=["run_id", "audio_id", "kind", "t_start", "t_end", "label"],
            records=[(job["id"], job["audio_id"], k, a, b, l) for k, a, b, l in ev])
        await con.execute(
            """INSERT INTO music.audio_analysis (audio_id, run_id, key, key_share, tempo_bpm, tuning_hz, chords,
                   n_sections, beats, beat_numbers, bars, peaks, peaks_rate, analyzed_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,NOW())
               ON CONFLICT (audio_id) DO UPDATE SET run_id = EXCLUDED.run_id, key = EXCLUDED.key,
                   key_share = EXCLUDED.key_share, tempo_bpm = EXCLUDED.tempo_bpm, tuning_hz = EXCLUDED.tuning_hz,
                   chords = EXCLUDED.chords, n_sections = EXCLUDED.n_sections, beats = EXCLUDED.beats,
                   beat_numbers = EXCLUDED.beat_numbers, bars = EXCLUDED.bars, peaks = EXCLUDED.peaks,
                   peaks_rate = EXCLUDED.peaks_rate, analyzed_at = NOW()""",
            job["audio_id"], job["id"], summary.get("key"), summary.get("key_share"), summary.get("tempo_bpm"),
            summary.get("tuning_hz"), summary["chords"], summary["n_sections"], summary["beats"],
            summary["beat_numbers"], summary["bars"], pk, pk_rate)
        await con.execute(
            "UPDATE music.analysis_runs SET status='done', finished_at=NOW(), error=NULL, timings=$2::jsonb WHERE id=$1",
            job["id"], json.dumps(timings))


async def enqueue(pool):
    async with pool.acquire() as con:
        n = await con.execute(
            """INSERT INTO music.analysis_runs (audio_id, source, version, priority)
               SELECT f.id, 'auto', $1,
                      CASE WHEN EXISTS (SELECT 1 FROM music.song_recordings r WHERE r.audio_id = f.id) THEN 10 ELSE 100 END
               FROM music.audio_files f
               WHERE f.duration_sec >= $2
                 AND EXISTS (SELECT 1 FROM music.audio_locations l WHERE l.audio_id = f.id AND l.missing_since IS NULL)
               ON CONFLICT (audio_id, version) WHERE source = 'auto' DO NOTHING""", ANALYSIS_VERSION, MIN_SECONDS)
        # songs accepted since enqueue -> move their queued recordings up
        await con.execute(
            """UPDATE music.analysis_runs r SET priority = 10 WHERE r.status = 'queued' AND r.priority > 10
               AND EXISTS (SELECT 1 FROM music.song_recordings s WHERE s.audio_id = r.audio_id)""")
        return int(n.split()[-1])


async def claim(con):
    return await con.fetchrow(
        """UPDATE music.analysis_runs SET status = 'running', started_at = NOW(), worker = $1, attempts = attempts + 1
           WHERE id = (SELECT id FROM music.analysis_runs WHERE status = 'queued' AND source = 'auto'
                       ORDER BY priority, created_at FOR UPDATE SKIP LOCKED LIMIT 1)
           RETURNING *""", WORKER)


async def worker_loop(pool, wid, stop):
    while not stop.is_set():
        async with pool.acquire() as con:
            job = await claim(con)
        if not job:
            await asyncio.sleep(30)
            continue
        t0 = time.time()
        try:
            dur = float(await pool.fetchval("SELECT duration_sec FROM music.audio_files WHERE id = $1", job["audio_id"]) or 0)
            lock = _long_lock if dur > LONG_SECONDS else None
            if lock:
                await lock.acquire()
            try:
                async with pool.acquire() as con:
                    status, note, result, timings = await analyse(con, job)
                    if status == "done":
                        await save(con, job, result, timings)
                    else:
                        await con.execute("UPDATE music.analysis_runs SET status=$2, error=$3, finished_at=NOW() WHERE id=$1",
                                          job["id"], status, note)
            finally:
                if lock:
                    lock.release()
            log(f"[w{wid}] {status} {job['audio_id']} ({dur:.0f}s audio) in {time.time() - t0:.1f}s")
        except Exception as e:
            async with pool.acquire() as con:
                await con.execute(
                    "UPDATE music.analysis_runs SET status = CASE WHEN attempts >= $3 THEN 'failed' ELSE 'queued' END, "
                    "error = $2, finished_at = NOW() WHERE id = $1", job["id"], str(e)[:1000], MAX_ATTEMPTS)
            log(f"[w{wid}] FAILED {job['audio_id']}: {str(e)[:200]}")


# ============================================================================
# Phase 3 — draft lead sheets (music.lead_sheet_drafts), one at a time.
# ============================================================================

def voiced_share(path_8k_pcm: bytes) -> float:
    """Share of 0.5 s windows above -40 dBFS (8 kHz mono s16)."""
    a = array("h"); a.frombytes(path_8k_pcm[: len(path_8k_pcm) // 2 * 2])
    win, n, loud = 4000, 0, 0
    thr = (32768 * 10 ** (-40 / 20)) ** 2 * win
    for i in range(0, len(a) - win + 1, win):
        n += 1
        if sum(x * x for x in a[i:i + win]) > thr:
            loud += 1
    return loud / n if n else 0.0


async def _analysis_for(con, audio_id, duration):
    """Effective events + summary in the same shape the catalog API serves."""
    summ = await con.fetchrow("SELECT key, tempo_bpm FROM music.audio_analysis WHERE audio_id = $1", audio_id)
    meter = await con.fetchval("SELECT meter FROM music.audio_harmony WHERE audio_id = $1", audio_id)   # phase 4
    ev = await con.fetch("SELECT kind, t_start, t_end, label FROM music.analysis_events_effective WHERE audio_id = $1 "
                         "ORDER BY kind, t_start", audio_id)
    events = {"chord": [], "section": [], "key": []}
    for e in ev:
        events[e["kind"]].append([float(e["t_start"]), float(e["t_end"]) if e["t_end"] is not None else None, e["label"]])
    return {"duration_sec": duration, "events": events,
            "summary": {"key": summ["key"], "tempo_bpm": float(summ["tempo_bpm"]) if summ["tempo_bpm"] else None,
                        "meter": meter} if summ else {}}


async def _whisper(client, vocals: str) -> dict:
    data = {"model": WHISPER_MODEL, "response_format": "verbose_json", "vad_filter": "true",
            "timestamp_granularities[]": ["word", "segment"]}
    for attempt in (None, WHISPER_FALLBACK_LANGUAGE):
        if attempt:
            data["language"] = attempt
        with open(vocals, "rb") as f:
            r = await client.post(f"{WHISPER_URL}/v1/audio/transcriptions", data=data,
                                  files={"file": ("vocals.flac", f, "audio/flac")}, timeout=1800)
        if r.status_code == 200:
            return r.json()
        # faster-whisper-server 500s when VAD leaves nothing to detect a
        # language from (max() of an empty list) — retry with a fixed language.
    r.raise_for_status()


async def process_draft(pool, d):
    timings = {}
    async with pool.acquire() as con:
        async def stage(name):
            await con.execute("UPDATE music.lead_sheet_drafts SET stage = $2 WHERE id = $1", d["id"], name)
        song = await con.fetchrow("SELECT id, title, project FROM music.songs WHERE id = $1", d["song_id"])
        loc = await con.fetchrow(
            """SELECT l.path, f.duration_sec FROM music.audio_locations l JOIN music.audio_files f ON f.id = l.audio_id
               WHERE l.audio_id = $1 AND l.missing_since IS NULL ORDER BY l.path LIMIT 1""", d["audio_id"])
        if not loc:
            raise RuntimeError("recording not found on disk")
        duration = float(loc["duration_sec"] or 0)

        # 1. chords/sections — analyse now if the batch hasn't reached it yet
        if not await con.fetchval("SELECT 1 FROM music.audio_analysis WHERE audio_id = $1", d["audio_id"]):
            await stage("analyse")
            job = await con.fetchrow(
                """INSERT INTO music.analysis_runs (audio_id, source, version, status, started_at, worker, attempts)
                   VALUES ($1,'auto',$2,'running',NOW(),$3,1)
                   ON CONFLICT (audio_id, version) WHERE source = 'auto'
                   DO UPDATE SET status='running', started_at=NOW(), worker=EXCLUDED.worker RETURNING *""",
                d["audio_id"], ANALYSIS_VERSION, WORKER)
            status, note, result, t = await analyse(con, job)
            if status != "done":
                raise RuntimeError(f"analysis {status}: {note}")
            await save(con, job, result, t)

        # 2. vocals (cached on the music-studio share — Demucs is the slow part)
        vocals = os.path.join(STEMS_DIR, str(d["audio_id"]), "vocals.flac")
        async with httpx.AsyncClient() as client:
            if not os.path.exists(vocals):
                await stage("separate")
                t = time.time()
                with tempfile.TemporaryDirectory(prefix="draft-") as tmp:
                    src = os.path.join(tmp, "in.flac")
                    await run(["ffmpeg", "-v", "error", "-nostdin", "-i", loc["path"], "-ac", "2", "-ar", "44100",
                               "-c:a", "flac", src], 1800)
                    with open(src, "rb") as f:
                        r = await client.post(f"{STEM_API_URL}/separate", files={"file": ("in.flac", f, "audio/flac")},
                                              timeout=3600)
                    r.raise_for_status()
                    timings["separate_device"] = r.headers.get("x-stem-device")
                os.makedirs(os.path.dirname(vocals), exist_ok=True)
                with open(vocals + ".part", "wb") as f:
                    f.write(r.content)
                os.replace(vocals + ".part", vocals)
                timings["separate"] = int((time.time() - t) * 1000)

            # 3. is anyone singing?
            pcm = await run(["ffmpeg", "-v", "error", "-nostdin", "-i", vocals, "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], 600)
            share = voiced_share(pcm)
            whisper = None
            if share >= VOICED_MIN:
                await stage("transcribe")
                t = time.time()
                whisper = await _whisper(client, vocals)
                timings["transcribe"] = int((time.time() - t) * 1000)

        # 4. align
        await stage("align")
        analysis = await _analysis_for(con, d["audio_id"], duration)
        cho = lead_sheet_draft.build(dict(song), analysis, whisper, loc["path"].rsplit("/", 1)[-1], share)
        await con.execute(
            """UPDATE music.lead_sheet_drafts SET status='done', stage=NULL, finished_at=NOW(), chordpro=$2,
                   vocals_path=$3, voiced_share=$4, language=$5, whisper=$6::jsonb, timings=$7::jsonb, error=NULL
               WHERE id=$1""",
            d["id"], cho, vocals, round(share, 3), (whisper or {}).get("language"),
            json.dumps(whisper) if whisper else None, json.dumps(timings))
    return share, timings


async def draft_loop(pool, stop):
    # Must never take the batch workers down with it: deploy_gpu.yml applies
    # the schema in post_tasks, AFTER the role restarts this container, so on
    # a deploy that adds a table the first poll can run before the table
    # exists (happened 2026-10-09: crash-loop until the schema step ran).
    try:
        async with pool.acquire() as con:
            await con.execute("UPDATE music.lead_sheet_drafts SET status='queued', stage=NULL WHERE status='running'")
    except Exception as e:
        log(f"[draft] startup reset skipped: {e}")
    while not stop.is_set():
        try:
            async with pool.acquire() as con:
                d = await con.fetchrow(
                    """UPDATE music.lead_sheet_drafts SET status='running', started_at=NOW(), stage='start'
                       WHERE id = (SELECT id FROM music.lead_sheet_drafts WHERE status='queued'
                                   ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *""")
        except Exception as e:
            log(f"[draft] queue poll failed, retrying in 30s: {str(e)[:200]}")
            await asyncio.sleep(30)
            continue
        if not d:
            await asyncio.sleep(10)
            continue
        t0 = time.time()
        try:
            share, timings = await process_draft(pool, d)
            log(f"[draft] done {d['id']} voiced={share:.2f} {json.dumps(timings)} in {time.time() - t0:.0f}s")
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            if isinstance(e, httpx.ConnectError):
                msg = f"gpu-02 not reachable ({e}) — is it awake?"
            async with pool.acquire() as con:
                await con.execute("UPDATE music.lead_sheet_drafts SET status='failed', stage=NULL, error=$2, "
                                  "finished_at=NOW() WHERE id=$1", d["id"], msg[:1000])
            log(f"[draft] FAILED {d['id']}: {msg[:200]}")


async def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--once", metavar="AUDIO_ID", help="analyse one recording now (verbose), outside the queue order")
    a = ap.parse_args()
    global _long_lock
    _long_lock = asyncio.Lock()
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=CONCURRENCY + 2)

    if a.once:
        async with pool.acquire() as con:
            job = await con.fetchrow(
                """INSERT INTO music.analysis_runs (audio_id, source, version, status, started_at, worker, attempts)
                   VALUES ($1,'auto',$2,'running',NOW(),$3,1)
                   ON CONFLICT (audio_id, version) WHERE source = 'auto'
                   DO UPDATE SET status='running', started_at=NOW(), worker=EXCLUDED.worker RETURNING *""",
                uuid.UUID(a.once), ANALYSIS_VERSION, WORKER)
            status, note, result, timings = await analyse(con, job, verbose=True)
            if status == "done":
                await save(con, job, result, timings)
                s = result[0]
                log(f"key={s.get('key')} ({s.get('key_share')}) tempo={s.get('tempo_bpm')} tuning={s.get('tuning_hz')} "
                    f"chords={s['chords'][:8]} sections={s['n_sections']} beats={len(s['beats'])} events={len(result[1])} peaks={len(result[2])}")
            else:
                log(status, note)
        await pool.close()
        return 0

    async with pool.acquire() as con:
        reset = await con.execute(
            "UPDATE music.analysis_runs SET status='queued' WHERE status='running' AND worker=$1", WORKER)
    log(f"music-analysis {ANALYSIS_VERSION}: concurrency {CONCURRENCY}, min {MIN_SECONDS:g}s, "
        f"reset {reset.split()[-1]} interrupted jobs, enqueued {await enqueue(pool)}")
    stop = asyncio.Event()
    workers = [asyncio.create_task(worker_loop(pool, i, stop)) for i in range(CONCURRENCY)]
    workers.append(asyncio.create_task(draft_loop(pool, stop)))   # also re-queues interrupted drafts
    while True:
        await asyncio.sleep(ENQUEUE_EVERY)
        try:
            n = await enqueue(pool)
            if n:
                log(f"enqueued {n} new recordings")
        except Exception as e:
            log(f"enqueue failed: {e}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
