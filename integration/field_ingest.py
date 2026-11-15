"""field_ingest.py — field recorders into Music Studio (2026-10-25).

the owner's Zoom H2n (handheld), Zoom R16 (8-track multitrack), an older Tascam
DR-07MK2 and -- since 2026-10-25 -- iPhone Voice Memos (m4a, taken from the
Finder backup by the Mac's `field memos`) write files whose only metadata is a broadcast-WAV `bext` header
(recorder name + its clock) or nothing at all (the R16). This particle turns
them into sessions Music Studio can analyse and enrich:

  arrive    The Mac's `field import` copies a card to ~/Music/Field/<device>/<batch>/
            with a sha256 manifest; Syncthing (send only -> receive only, deletes
            ignored) puts it in My Recordings/Field Recordings/ on nas-02 -- its
            final place, never moved again. /field/verify re-hashes what arrived
            against the manifest: only then does the Mac erase the card.
  register  Every WAV under Field Recordings/ (and, as backfill, the older ZOOM/,
            FieldRecordings/ and the R16 projects under Projects/) becomes a
            music.field_recordings row: recorder, take, track, length, format,
            and a time -- the bext clock only when it was set (FOUND: H2n files
            dated 2011-01-01, the R16 2010-01-01, the Tascam 2000-01-01).
  catalogue New batches are handed to Beets' safe single-folder import through a
            trigger file a host unit on gpu-01 runs (the nightly identity sync
            marks anything Beets doesn't know as missing, so this never writes
            audio_locations itself); the analysis worker then finds key/tempo/chords.
  notes     The first 20 s of each take go to Whisper on gpu-02 (local, VAD on):
            "take three, chorus idea" becomes the take's spoken note.
  sessions  Household decisions 2026-10-25: handheld takes form a new session
            after a 30-minute gap (per recorder); an R16 project is a live session
            of its own; a handheld session may attach to an R16 session (it
            overlaps it, or owner says so) -- never the other way round.

No egress: nas-02 over NFS (read-only), pg-01, Whisper on gpu-02.
"""
from __future__ import annotations

import asyncio
import hashlib
import subprocess
import io
import json
import logging
import os
import re
import struct
import wave
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("field-ingest")
TZ = ZoneInfo("Europe/Berlin")

DB_DSN = os.environ.get("DATABASE_URL", "")
API_PORT = int(os.environ.get("API_PORT", "8203"))
ROOT = os.environ.get("RECORDINGS_ROOT", "/import/nasty")                 # = My Recordings
FIELD_DIR = os.environ.get("FIELD_DIR", "Field Recordings")                # new imports land here
BACKFILL = json.loads(os.environ.get("FIELD_BACKFILL_JSON", '["ZOOM", "FieldRecordings", "VoiceRecordings", "Projects"]'))
TRIGGERS = os.environ.get("TRIGGER_DIR", "/triggers")
WHISPER_URL = os.environ.get("WHISPER_URL", "http://10.0.30.51:5000").rstrip("/")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "mobiuslabsgmbh/faster-whisper-large-v3-turbo")
NOTE_SECS = 20
NOTES_PER_ROUND = int(os.environ.get("NOTES_PER_ROUND", "5"))             # Whisper is shared with DOMO's voice
SCAN_EVERY_S = int(os.environ.get("SCAN_EVERY_S", "120"))
SESSION_GAP = timedelta(minutes=int(os.environ.get("SESSION_GAP_MIN", "30")))
SNAPSHOT_GRACE = timedelta(minutes=65)       # an hourly DATAGRID snapshot has run since it arrived
MANIFEST = ".field-manifest.tsv"
CLOCK_FLOOR = datetime(2012, 1, 1, tzinfo=TZ)   # earlier = the recorder's clock was never set

_pool: Optional[asyncpg.Pool] = None
_status: dict[str, Any] = {}
_sha_cache: dict[tuple, str] = {}

# ── reading a WAV header (never the audio) ───────────────────────────────────


def wav_info(path: str) -> dict:
    """fmt/bext/iXML/data from the chunk headers; seeks past the audio."""
    out: dict = {}
    with open(path, "rb") as f:
        head = f.read(12)
        if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            return {"error": "not a WAV"}
        while True:
            h = f.read(8)
            if len(h) < 8:
                break
            cid, size = h[:4], struct.unpack("<I", h[4:])[0]
            if cid == b"fmt ":
                d = f.read(size)
                _, ch, rate, _, _, bits = struct.unpack("<HHIIHH", d[:16])
                out.update(channels=ch, rate=rate, bits=bits)
            elif cid == b"bext":
                d = f.read(size)
                out["originator"] = d[256:288].split(b"\0")[0].decode("latin1").strip()
                out["bext_time"] = d[320:338].decode("latin1", "replace")
                out["bext_desc"] = d[:256].split(b"\0")[0].decode("latin1").strip()
            elif cid == b"iXML":
                out["ixml"] = f.read(min(size, 65536)).decode("utf-8", "replace")
                f.seek(size - min(size, 65536), 1)
            elif cid == b"data":
                out["data_offset"], out["data_bytes"] = f.tell(), size
                f.seek(size, 1)
            else:
                f.seek(size, 1)
            if size & 1:
                f.seek(1, 1)
    if out.get("rate") and out.get("channels") and out.get("bits") and "data_bytes" in out:
        out["seconds"] = round(out["data_bytes"] / (out["rate"] * out["channels"] * out["bits"] / 8), 2)
    return out


MP4_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)
AUDIO_EXT = (".wav", ".m4a")


def mp4_info(path: str) -> dict:
    """Duration + creation time from an m4a's moov/mvhd (Voice Memos write the recording
    time there, UTC since 1904). Walks atom headers only; the audio is never read."""
    def atoms(f, end):
        while f.tell() + 8 <= end:
            start = f.tell()
            h = f.read(8)
            size, kind = struct.unpack(">I", h[:4])[0], h[4:]
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
            elif size == 0:
                size = end - start
            if size < 8:
                return
            yield kind, start, size, f.tell()
            f.seek(start + size)
    out: dict = {}
    with open(path, "rb") as f:
        end = os.fstat(f.fileno()).st_size
        for kind, start, size, body in atoms(f, end):
            if kind != b"moov":
                continue
            f.seek(body)
            for k2, s2, z2, b2 in atoms(f, start + size):
                if k2 == b"mvhd":
                    f.seek(b2)
                    version = f.read(1)[0]
                    f.read(3)
                    if version == 1:
                        created, _, scale, dur = struct.unpack(">QQIQ", f.read(28))
                    else:
                        created, _, scale, dur = struct.unpack(">IIII", f.read(16))
                    out["seconds"] = round(dur / scale, 2) if scale else None
                    out["created"] = MP4_EPOCH + timedelta(seconds=created) if created else None
                    return out
    return out


def media_info(path: str) -> dict:
    return mp4_info(path) if path.lower().endswith(".m4a") else wav_info(path)


def bext_time(s: str) -> Optional[datetime]:
    """'2025-06-0619:20:19' (bext date + time, local clock) -> datetime, else None."""
    m = re.match(r"(\d{4})[-:/](\d{2})[-:/](\d{2})\s*(\d{2})[-:.](\d{2})[-:.](\d{2})", s or "")
    if not m:
        return None
    try:
        return datetime(*map(int, m.groups()), tzinfo=TZ)
    except ValueError:
        return None


def _iso(s: str) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(s) if s else None
    except ValueError:
        return None
    return t.astimezone(TZ) if t and t.tzinfo else (t.replace(tzinfo=TZ) if t else None)


def _name_time(path: str) -> Optional[datetime]:
    m = OLD_MEMO.search(path or "")
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=TZ) if m else None
    except ValueError:
        return None


def plausible(dt: Optional[datetime], now: Optional[datetime] = None) -> bool:
    return dt is not None and CLOCK_FLOOR <= dt <= (now or datetime.now(TZ)) + timedelta(days=1)


# ── which recorder, which take ───────────────────────────────────────────────

R16_TRACK = re.compile(r"/AUDIO/(MONO|STE)-(\d{3})\.wav$", re.I)
H2N_4CH = re.compile(r"/SR(\d{3})(MS|XY)\.wav$", re.I)
H2N_STEREO = re.compile(r"/ZOOM(\d{4})\.wav$", re.I)
DEVICES = {"zoom-h2n": ("Zoom H2n", "handheld"), "zoom-r16": ("Zoom R16", "multitrack"),
           "tascam-dr07mk2": ("Tascam DR-07MK2", "handheld"), "iphone-voicememo": ("iPhone Voice Memos", "handheld"),
           "handheld": ("handheld recorder", "handheld")}
# The iPhone's own names: "20120601 213251.m4a", "20200806-181719.m4a" (local clock, time zone unknown).
OLD_MEMO = re.compile(r"/(\d{8})[ -](\d{6})[^/]*\.m4a$", re.I)


def classify(path: str, info: dict, manifest_device: str = "") -> dict:
    """{device, kind, take, track}. The R16 by its project layout (no metadata at all);
    the rest by the bext originator, then the Mac's manifest, then the file name."""
    orig = (info.get("originator") or "").lower()
    if path.lower().endswith(".m4a"):                 # Voice Memos (m4a from no other recorder here)
        return {"device": "iphone-voicememo", "kind": "handheld", "take": path.rsplit(".", 1)[0], "track": None}
    if m := R16_TRACK.search(path):
        project = path[:m.start()]
        n = int(m.group(2))
        return {"device": "zoom-r16", "kind": "multitrack", "take": project,
                "track": f"{'stereo' if m.group(1).upper() == 'STE' else 'track'} {n + 1}"}
    if "h2n" in orig or manifest_device == "zoom-h2n" or H2N_STEREO.search(path) or H2N_4CH.search(path):
        device = "zoom-h2n"
    elif "tascam" in orig and "dr-07" in orig:
        device = "tascam-dr07mk2"
    else:
        device = manifest_device if manifest_device in DEVICES else "handheld"
    if m := H2N_4CH.search(path):                     # 4-channel mode: MS + XY files = one take
        return {"device": device, "kind": "handheld", "take": path[:m.start()] + f"/SR{m.group(1)}",
                "track": m.group(2).upper()}
    return {"device": device, "kind": "handheld", "take": path.rsplit(".", 1)[0], "track": None}


def take_time(info: dict, mtime: Optional[float], recorded: str = "", path: str = "") -> tuple[Optional[datetime], bool]:
    """(start, clock_ok). In order: the time Voice Memos recorded (Mac manifest), the m4a's own
    creation time, an old memo's "yyyymmdd hhmmss" name, the bext clock -- each only when
    plausible; else the file's own time (the card's FAT time = roughly when the take ended)."""
    # An m4a's mvhd time is when the file was finished, i.e. the END of the memo (FOUND 2026-10-25:
    # "20120409 230055.m4a", 336 s long, says 03:06:31 UTC = 23:06:31 US Eastern).
    m4a_start = info["created"] - timedelta(seconds=float(info.get("seconds") or 0)) if info.get("created") else None
    for t in (_iso(recorded), m4a_start and m4a_start.astimezone(TZ),
              _name_time(path), bext_time(info.get("bext_time", ""))):
        if plausible(t):
            return t, True
    if mtime:
        end = datetime.fromtimestamp(mtime, TZ)
        start = end - timedelta(seconds=float(info.get("seconds") or 0))
        if plausible(start):
            return start, False
    return None, False


# ── sessions ─────────────────────────────────────────────────────────────────


def group_sessions(rows: list[dict]) -> list[dict]:
    """rows: {path, device, kind, take, start, seconds, folder, field_session}. Returns sessions
    {key, kind, device, members, start, end, attached_to}. Keys are reused from members so a
    session keeps its music.sessions row as takes are added."""
    out = []
    multi: dict[str, list] = {}
    for r in rows:
        if r["kind"] == "multitrack":
            multi.setdefault(r["take"], []).append(r)
    for project, members in sorted(multi.items()):
        times = [m["start"] for m in members if m["start"]]
        ends = [m["start"] + timedelta(seconds=float(m["seconds"] or 0)) for m in members if m["start"]]
        out.append({"key": _reuse(members, f"m:{project}"), "kind": "multitrack", "device": "zoom-r16",
                    "members": members, "start": min(times) if times else None,
                    "end": max(ends) if ends else None, "attached_to": None})
    by_device: dict[str, list] = {}
    for r in rows:
        if r["kind"] == "handheld":
            by_device.setdefault(r["device"], []).append(r)
    for device, members in sorted(by_device.items()):
        timed = sorted((m for m in members if m["start"]), key=lambda m: m["start"])
        cluster, end = [], None
        for m in timed:
            m_end = m["start"] + timedelta(seconds=float(m["seconds"] or 0))
            if cluster and m["start"] - end > SESSION_GAP:
                out.append(_handheld(device, cluster))
                cluster, end = [], None
            cluster.append(m)
            end = max(end, m_end) if end else m_end
        if cluster:
            out.append(_handheld(device, cluster))
        untimed: dict[str, list] = {}
        for m in members:
            if not m["start"]:
                untimed.setdefault(m["folder"], []).append(m)
        for folder, ms in sorted(untimed.items()):        # clock never set: one session per folder
            out.append({"key": _reuse(ms, f"h:{device}:{folder}"), "kind": "handheld", "device": device,
                        "members": ms, "start": None, "end": None, "attached_to": None})
    # A handheld session joins the R16 session it overlaps; an R16 session never joins anything.
    lives = [s for s in out if s["kind"] == "multitrack" and s["start"]]
    for s in out:
        if s["kind"] == "handheld" and s["start"]:
            hit = next((l for l in lives if s["start"] <= l["end"] + SESSION_GAP and l["start"] - SESSION_GAP <= s["end"]), None)
            s["attached_to"] = hit["key"] if hit else None
    return out


def _handheld(device: str, cluster: list) -> dict:
    end = max(m["start"] + timedelta(seconds=float(m["seconds"] or 0)) for m in cluster)
    return {"key": _reuse(cluster, f"h:{device}:{cluster[0]['start'].isoformat()}"), "kind": "handheld",
            "device": device, "members": cluster, "start": cluster[0]["start"], "end": end, "attached_to": None}


def _reuse(members: list, fresh: str) -> str:
    known = Counter(m["field_session"] for m in members if m.get("field_session"))
    return known.most_common(1)[0][0] if known else fresh


def can_attach(child: dict, parent: dict) -> Optional[str]:
    """None if a session may be attached to another, else why not (decision 2026-10-25)."""
    if child["kind"] != "handheld":
        return "only a handheld (H2n/Tascam) session can be attached; an R16 live session stands on its own"
    if parent["kind"] != "multitrack":
        return "a handheld session can only join an R16 live session"
    return None


def session_title(s: dict) -> str:
    name = DEVICES.get(s["device"], (s["device"], ""))[0]
    takes = len({m["take"] for m in s["members"]})
    if s["kind"] == "multitrack":
        return f"Live · {Path(s['members'][0]['take']).name} · {name} · {len(s['members'])} tracks"
    folder = Path(s["members"][0]["folder"])
    when = f"{s['start']:%Y-%m-%d %H:%M}" if s["start"] else f"{folder.parent.name}/{folder.name} (clock not set)"
    return f"Field · {when} · {name} · {takes} take{'s' if takes != 1 else ''}"


# ── spoken notes ─────────────────────────────────────────────────────────────

PHANTOMS = {"thank you", "thank you.", "thanks for watching", "thanks for watching!", "you", "bye", "bye.",
            "thank you for watching", "so", "okay", "amen", "music", "[music]"}


def first_seconds_wav(path: str, info: dict, secs: int = NOTE_SECS) -> bytes:
    """The first `secs` of the first channel as 16-bit mono WAV bytes (16- and 24-bit PCM)."""
    ch, bits, rate = info["channels"], info["bits"], info["rate"]
    width = bits // 8
    frames = min(int(rate * secs), int(info["data_bytes"] // (ch * width)))
    with open(path, "rb") as f:
        f.seek(info["data_offset"])
        raw = f.read(frames * ch * width)
    mono = bytearray()
    step = ch * width
    for i in range(0, len(raw) - step + 1, step):
        s = raw[i:i + width]
        mono += s if width == 2 else s[width - 2:width] if width > 2 else bytes([0, s[0] ^ 0x80])
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(mono))
    return buf.getvalue()


def first_seconds_ffmpeg(path: str, secs: int = NOTE_SECS) -> bytes:
    """The first seconds of a compressed file (m4a) as 16 kHz mono WAV, through ffmpeg
    (field-ingest runs on the music-analysis image, which has it)."""
    try:
        return subprocess.run(["ffmpeg", "-v", "error", "-t", str(secs), "-i", path, "-ac", "1", "-ar", "16000",
                               "-f", "wav", "-"], capture_output=True, timeout=60, check=True).stdout
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("ffmpeg on %s failed: %s", path, e)
        return b""


def clean_note(text: str) -> Optional[str]:
    t = re.sub(r"\s+", " ", text or "").strip()
    if t.lower() in PHANTOMS or len(t.split()) < 2:
        return None
    return t[:300]


async def spoken_note(path: str, info: dict) -> tuple[Optional[str], bool]:
    """(note, tried). tried=False means Whisper couldn't be reached: retry later."""
    if float(info.get("seconds") or 0) < 2:
        return None, True
    if path.lower().endswith(".m4a"):
        clip = await asyncio.to_thread(first_seconds_ffmpeg, path)
        if not clip:
            return None, True
    elif not info.get("data_bytes") or info.get("bits") not in (8, 16, 24, 32):
        return None, True
    else:
        clip = await asyncio.to_thread(first_seconds_wav, path, info)
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{WHISPER_URL}/v1/audio/transcriptions",
                             data={"model": WHISPER_MODEL, "response_format": "json", "vad_filter": "true"},
                             files={"file": ("slate.wav", clip, "audio/wav")})
    except httpx.HTTPError as e:
        log.warning("whisper unreachable: %s", e)
        return None, False
    if r.status_code >= 500:                 # VAD found no speech at all (known server behaviour)
        return None, True
    if r.status_code != 200:
        return None, False
    return clean_note(r.json().get("text", "")), True


# ── storage ──────────────────────────────────────────────────────────────────


async def pool() -> asyncpg.Pool:
    """Tables: music.field_recordings (+ columns) and music.field_sessions in music_schema.sql."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=4)
    return _pool


def rel(p: str) -> str:
    return p[len(ROOT):].lstrip("/") if p.startswith(ROOT) else p


def wav_files(top: str) -> list[str]:
    out = []
    for d, dirs, files in os.walk(top):
        dirs[:] = [x for x in dirs if not x.startswith(".") and x != "Backup"]
        out += [os.path.join(d, f) for f in files if f.lower().endswith(AUDIO_EXT) and not f.startswith("._")]
    return sorted(out)


def read_manifest(batch_dir: str) -> dict[str, dict]:
    """relpath -> {sha256, size, mtime, device}; tab-separated with a header line."""
    p = os.path.join(batch_dir, MANIFEST)
    out = {}
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split("\t")
        for line in f:
            row = dict(zip(head, line.rstrip("\n").split("\t")))
            if row.get("path"):
                out[row["path"]] = row
    return out


def batch_of(path: str) -> Optional[str]:
    """Field Recordings/<device>/<batch>/... -> that batch dir (full path), else None."""
    r = rel(path).split("/")
    if len(r) >= 4 and r[0] == FIELD_DIR:
        return os.path.join(ROOT, *r[:3])
    return None


async def register(path: str) -> bool:
    st = await asyncio.to_thread(os.stat, path)
    if datetime.now().timestamp() - st.st_mtime < 120:            # still arriving
        return False
    info = await asyncio.to_thread(media_info, path)
    if info.get("error"):
        return False
    batch = batch_of(path)
    man = (await asyncio.to_thread(read_manifest, batch)).get(os.path.relpath(path, batch)) if batch else None
    if batch and man and int(man.get("size") or -1) != st.st_size:
        return False                                              # manifest says more is coming
    c = classify(path, info, (man or {}).get("device", ""))
    start, clock_ok = take_time(info, float(man["mtime"]) if man and man.get("mtime") else st.st_mtime,
                                (man or {}).get("recorded", ""), path)
    p = await pool()
    aid = await p.fetchval("SELECT audio_id FROM music.audio_locations WHERE path=$1 AND missing_since IS NULL", path)
    await p.execute(
        "INSERT INTO music.field_recordings (file_path, duration_sec, recorded_at, device, location, status, kind, "
        "take, track, clock_ok, size_bytes, mtime_ns, batch, audio_id, channels, sample_rate, bits, recorder, description) "
        "VALUES ($1,$2,$3,$4,$5,'inbox',$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18) ON CONFLICT (file_path) DO NOTHING",
        path, info.get("seconds"), start, c["device"], "live" if c["kind"] == "multitrack" else "field", c["kind"],
        c["take"], c["track"], clock_ok, st.st_size, st.st_mtime_ns, rel(batch) if batch else None, aid,
        info.get("channels"), info.get("rate"), info.get("bits"), info.get("originator") or None,
        (man or {}).get("title") or None)                 # a Voice Memo's own title
    return True


def request_catalogue(batch_dir: str) -> bool:
    """A trigger file for the host unit on gpu-01 (Beets' safe import of this one folder)."""
    os.makedirs(TRIGGERS, exist_ok=True)
    name = hashlib.sha1(batch_dir.encode()).hexdigest()[:16]
    req, done = os.path.join(TRIGGERS, name + ".req"), os.path.join(TRIGGERS, name + ".done")
    if os.path.exists(req) or (os.path.exists(done) and datetime.now().timestamp() - os.path.getmtime(done) < 6 * 3600):
        return False
    with open(req + ".tmp", "w") as f:
        f.write(batch_dir + "\n")
    os.replace(req + ".tmp", req)
    return True


async def regroup() -> int:
    p = await pool()
    rows = [dict(r) for r in await p.fetch(
        "SELECT file_path AS path, device, kind, take, recorded_at AS start, duration_sec AS seconds, field_session "
        "FROM music.field_recordings WHERE kind IS NOT NULL")]
    for r in rows:
        r["folder"] = str(Path(r["path"]).parent)
        r["start"] = r["start"].astimezone(TZ) if r["start"] else None
    sessions = group_sessions(rows)
    changed = 0
    for s in sessions:
        sid = await p.fetchval("SELECT session_id FROM music.field_sessions WHERE key=$1", s["key"])
        notes = await p.fetch("SELECT spoken_note FROM music.field_recordings WHERE file_path = ANY($1) "
                              "AND spoken_note IS NOT NULL ORDER BY recorded_at NULLS LAST, file_path",
                              [m["path"] for m in s["members"]])
        summary = "Spoken notes: " + " | ".join(n["spoken_note"] for n in notes) if notes else None
        if sid is None:
            sid = await p.fetchval(
                "INSERT INTO music.sessions (title, session_dir, daw, status, recorded_at, location, source_device, notes) "
                "VALUES ($1,$2,'none','draft',$3,$4,$5,$6) RETURNING id", session_title(s),
                str(Path(s["members"][0]["path"]).parent), s["start"], "live" if s["kind"] == "multitrack" else "field",
                s["device"], summary)
            changed += 1
        else:
            await p.execute("UPDATE music.sessions SET recorded_at=$2, notes=COALESCE($3, notes), updated_at=now() "
                            "WHERE id=$1", sid, s["start"], summary)
        await p.execute(
            "INSERT INTO music.field_sessions (key, session_id, kind, device, starts_at, ends_at, attached_to) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (key) DO UPDATE SET starts_at=EXCLUDED.starts_at, "
            "ends_at=EXCLUDED.ends_at, attached_to=COALESCE(music.field_sessions.attached_to, EXCLUDED.attached_to)",
            s["key"], sid, s["kind"], s["device"], s["start"], s["end"], s["attached_to"])
        await p.execute("UPDATE music.field_recordings SET field_session=$1, session_id=$2 WHERE file_path = ANY($3) "
                        "AND (field_session IS DISTINCT FROM $1 OR session_id IS DISTINCT FROM $2)",
                        s["key"], sid, [m["path"] for m in s["members"]])
    return changed


async def scan() -> dict:
    p = await pool()
    known = {r["file_path"] for r in await p.fetch("SELECT file_path FROM music.field_recordings")}
    tops = [os.path.join(ROOT, FIELD_DIR)] + [os.path.join(ROOT, b) for b in BACKFILL]
    found = []
    for top in tops:
        if os.path.isdir(top):
            found += await asyncio.to_thread(wav_files, top)
    # Projects/ holds Live sets too: from there only R16 tracks are field material.
    found = [f for f in found if not rel(f).startswith("Projects/") or R16_TRACK.search(f)]
    new = 0
    for path in found:
        if path not in known:
            try:
                new += await register(path)
            except Exception:  # noqa: BLE001 -- one bad file must not stop the rest
                log.exception("register %s failed", path)
    # Catalogue: new-import batches whose files Beets doesn't know yet.
    asked = 0
    for b in {r["batch"] for r in await p.fetch(
            "SELECT DISTINCT batch FROM music.field_recordings f WHERE batch IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM music.audio_locations l WHERE l.path=f.file_path AND l.missing_since IS NULL)")}:
        asked += request_catalogue(os.path.join(ROOT, b))
    await p.execute("UPDATE music.field_recordings f SET audio_id=l.audio_id FROM music.audio_locations l "
                    "WHERE f.audio_id IS NULL AND l.path=f.file_path AND l.missing_since IS NULL")
    # Spoken notes, a few per round (Whisper on gpu-02 also serves DOMO's voice).
    noted = 0
    for r in await p.fetch("SELECT file_path FROM music.field_recordings WHERE spoken_tried_at IS NULL "
                           "AND (track IS NULL OR track IN ('track 1','MS','stereo 1')) ORDER BY recorded_at DESC NULLS LAST LIMIT $1",
                           NOTES_PER_ROUND):
        info = await asyncio.to_thread(media_info, r["file_path"])
        note, tried = await spoken_note(r["file_path"], info)
        if tried:
            await p.execute("UPDATE music.field_recordings SET spoken_note=$2, spoken_tried_at=now(), "
                            "description=COALESCE(description, $2) WHERE file_path=$1", r["file_path"], note)
            noted += 1
    sessions = await regroup() if new or noted else 0
    _status.update(at=datetime.now(TZ).isoformat(), files=len(found), new=new, catalogue_requests=asked,
                   notes=noted, new_sessions=sessions)
    return dict(_status)


async def _loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await scan()
        except Exception:  # noqa: BLE001
            log.exception("field scan failed")
        await asyncio.sleep(SCAN_EVERY_S)


# ── checks the Mac's `field` script asks for ─────────────────────────────────


def sha256(path: str) -> str:
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime_ns)
    if key not in _sha_cache:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _sha_cache[key] = h.hexdigest()
    return _sha_cache[key]


def verify_batch(batch_dir: str) -> tuple[str, list[str]]:
    """Every file in the Mac's manifest is here with the same sha256 -> 'ok'."""
    man = read_manifest(batch_dir)
    if not man:
        return "wait", ["WAIT manifest not here yet"]
    lines, state = [], "ok"
    for relp, row in sorted(man.items()):
        p = os.path.join(batch_dir, relp)
        if not os.path.exists(p) or os.path.getsize(p) != int(row["size"]):
            lines.append(f"WAIT {relp}")
            state = "wait" if state == "ok" else state
        elif sha256(p) != row["sha256"]:
            lines.append(f"BAD {relp}")
            state = "bad"
        else:
            lines.append(f"OK {relp}")
    return state, lines


def _batch_dir(batch: str) -> str:
    p = os.path.normpath(os.path.join(ROOT, FIELD_DIR, batch))
    if not p.startswith(os.path.join(ROOT, FIELD_DIR) + "/"):
        raise HTTPException(400, "batch outside Field Recordings")
    return p


app = FastAPI(title="field-ingest")


@app.on_event("startup")
async def _startup() -> None:
    await pool()
    if os.environ.get("FIELD_WATCH", "on") == "on":
        asyncio.get_event_loop().create_task(_loop())


@app.get("/health")
async def health():
    await (await pool()).fetchval("SELECT 1")
    return {"status": "ok", **_status}


@app.get("/field/verify", response_class=PlainTextResponse)
async def ep_verify(batch: str):
    """batch = '<device>/<batch>' below Field Recordings. Plain text for the Mac."""
    state, lines = await asyncio.to_thread(verify_batch, _batch_dir(batch))
    return f"STATE={state}\n" + "\n".join(lines) + "\n"


@app.get("/field/status", response_class=PlainTextResponse)
async def ep_status(batch: str):
    """processed = registered + catalogued + analysed (or too short to analyse) + an hourly
    snapshot has run since it arrived. Plain text for the Mac's `field clean`."""
    d = _batch_dir(batch)
    man = await asyncio.to_thread(read_manifest, d)
    if not man:
        return "STATE=unknown\n"
    p = await pool()
    lines, done = [], 0
    for relp in sorted(man):
        path = os.path.join(d, relp)
        if not relp.lower().endswith(AUDIO_EXT):
            done += 1
            continue
        r = await p.fetchrow(
            "SELECT f.duration_sec, f.created_at, f.audio_id, l.missing_since IS NULL AND l.path IS NOT NULL AS catalogued, "
            "EXISTS (SELECT 1 FROM music.audio_analysis a WHERE a.audio_id = f.audio_id) AS analysed "
            "FROM music.field_recordings f LEFT JOIN music.audio_locations l ON l.path = f.file_path WHERE f.file_path=$1", path)
        if not r:
            lines.append(f"PENDING {relp}: not registered yet")
        elif not r["catalogued"]:
            lines.append(f"PENDING {relp}: waiting for the catalogue")
        elif not r["analysed"] and float(r["duration_sec"] or 0) >= 6:
            lines.append(f"PENDING {relp}: waiting for analysis")
        elif datetime.now(timezone.utc) - r["created_at"] < SNAPSHOT_GRACE:
            lines.append(f"PENDING {relp}: waiting for the next snapshot")
        else:
            done += 1
    return f"STATE={'processed' if done == len(man) else 'pending'}\nDONE={done}/{len(man)}\n" + "\n".join(lines) + "\n"


@app.get("/field/sessions")
async def ep_sessions(limit: int = 100):
    rows = await (await pool()).fetch(
        "SELECT fs.key, fs.kind, fs.device, fs.starts_at, fs.attached_to, s.id AS session_id, s.title, s.notes, "
        "(SELECT count(*) FROM music.field_recordings f WHERE f.field_session = fs.key) AS files "
        "FROM music.field_sessions fs JOIN music.sessions s ON s.id = fs.session_id "
        "ORDER BY fs.starts_at DESC NULLS LAST LIMIT $1", limit)
    return [{**dict(r), "session_id": str(r["session_id"]),
             "starts_at": r["starts_at"].isoformat() if r["starts_at"] else None} for r in rows]


@app.get("/field/sessions/{key:path}/takes")
async def ep_takes(key: str):
    rows = await (await pool()).fetch(
        "SELECT f.file_path, f.take, f.track, f.duration_sec, f.recorded_at, f.clock_ok, f.spoken_note, f.recorder, "
        "f.description AS title, "
        "f.channels, f.sample_rate, f.bits, a.key AS musical_key, a.tempo_bpm, l.beets_item_id "
        "FROM music.field_recordings f LEFT JOIN music.audio_analysis a ON a.audio_id = f.audio_id "
        "LEFT JOIN music.audio_locations l ON l.path = f.file_path AND l.missing_since IS NULL "
        "WHERE f.field_session=$1 ORDER BY f.recorded_at NULLS LAST, f.take, f.track", key)
    return [{**dict(r), "file": rel(r["file_path"]), "recorded_at": r["recorded_at"].isoformat() if r["recorded_at"] else None,
             "tempo_bpm": float(r["tempo_bpm"]) if r["tempo_bpm"] else None,
             "duration_sec": float(r["duration_sec"]) if r["duration_sec"] else None} for r in rows]


@app.post("/field/sessions/{key:path}/attach")
async def ep_attach(key: str, to: str):
    """Put a handheld session into an R16 live session (never the other way round)."""
    p = await pool()
    child = await p.fetchrow("SELECT * FROM music.field_sessions WHERE key=$1", key)
    parent = await p.fetchrow("SELECT * FROM music.field_sessions WHERE key=$1", to)
    if not child or not parent:
        raise HTTPException(404, "no such session")
    if why := can_attach(dict(child), dict(parent)):
        raise HTTPException(409, why)
    await p.execute("UPDATE music.field_sessions SET attached_to=$2 WHERE key=$1", key, to)
    return {"attached": key, "to": to}


@app.post("/field/scan")
async def ep_scan():
    return await scan()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)
