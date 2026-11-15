#!/usr/bin/env python3
"""
music_catalog_api.py — Elementary Particles: Music Studio Catalog API

FastAPI REST service for the music.* schema.  Provides the session/song/track
data model inspired by Ableton's Session View.

Endpoints
─────────
  GET/POST  /api/v1/sessions          — list / create sessions
  GET/PUT   /api/v1/sessions/{id}     — fetch / update session
  GET/POST  /api/v1/sessions/{id}/tracks   — tracks within a session
  GET/POST  /api/v1/songs             — composition catalog
  GET/PUT   /api/v1/songs/{id}        — update song (status, notes, etc.)
  GET/POST  /api/v1/references        — covers & arrangements wishlist
  GET/PATCH /api/v1/references/{id}   — update status / notes
  GET/POST  /api/v1/field-recordings  — raw capture inbox
  POST      /api/v1/field-recordings/{id}/transcribe  — Basic Pitch audio→MIDI
  POST      /api/v1/field-recordings/{id}/separate    — Demucs stem separation
  GET/POST  /api/v1/plugins           — plugin catalog
  GET/POST  /api/v1/instruments       — instrument catalog
  GET       /api/v1/library           — Beets-managed finished tracks
  GET       /api/v1/search            — unified full-text search
  GET       /api/v1/dashboard         — statistics view
  GET       /health                   — liveness probe

Run:
  uvicorn music_catalog_api:app --host 0.0.0.0 --port 8200 --reload
"""

import os
import sys
import uuid
import json
import shutil
import logging
import asyncio
import time
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field
import asyncpg
import httpx

# ============================================================================
# Configuration
# ============================================================================

DB_DSN              = os.getenv("DATABASE_URL",
                                f"postgresql://{os.getenv('MUSIC_DB_USER','music_studio')}"
                                f":{os.getenv('MUSIC_DB_PASSWORD','')}"
                                f"@{os.getenv('MUSIC_DB_HOST','pg-01.internal')}"
                                f":{os.getenv('MUSIC_DB_PORT','5432')}"
                                f"/{os.getenv('MUSIC_DB_NAME','music_studio')}")

SESSION_FILES_DIR   = Path(os.getenv("SESSION_FILES_DIR",   "/music/sessions"))
FIELD_DIR           = Path(os.getenv("FIELD_RECORDINGS_DIR","/music/field"))
MIDI_OUTPUT_DIR     = Path(os.getenv("MIDI_OUTPUT_DIR",     "/music/midi"))
STEMS_OUTPUT_DIR    = Path(os.getenv("STEMS_OUTPUT_DIR",    "/music/stems"))
# Files this service may hand to a subprocess (basic-pitch, demucs): only what lives
# under its own mounts. A file_path row is data from the network, not a trusted path.
ALLOWED_AUDIO_ROOTS = [Path(r) for r in os.getenv("ALLOWED_AUDIO_ROOTS", "/music").split(":") if r]


def _safe_upload_name(name: Optional[str]) -> str:
    """Basename only, no path separators, no empty/dot names: a filename is user input."""
    base = Path(name or "").name.replace("\\", "_").strip()
    if not base or base in (".", "..") or base.startswith("."):
        raise HTTPException(400, "invalid filename")
    return base


def _audio_path_or_400(file_path: Optional[str]) -> Path:
    """Resolve a recorded file_path and refuse anything outside ALLOWED_AUDIO_ROOTS."""
    if not file_path:
        raise HTTPException(400, "File not found: (empty path)")
    p = Path(file_path)
    try:
        rp = p.resolve(strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(400, f"File not found: {file_path}")
    if not rp.is_file() or not any(rp.is_relative_to(root.resolve()) for root in ALLOWED_AUDIO_ROOTS):
        raise HTTPException(400, f"File not allowed: {file_path}")
    return rp
AUDIO_ANALYSIS_URL  = os.getenv("AUDIO_ANALYSIS_URL",       "http://audio-analysis:8100")
BEETS_URL           = os.getenv("MUSIC_BEETS_URL",          "http://music-beets:8337")
# Separate from BEETS_URL on purpose: BEETS_URL is the internal docker
# network address this container uses for server-to-server calls; browsers
# can't resolve it. BEETS_PUBLIC_URL is what actually goes into play_url
# for the <audio> tag — the real Traefik-fronted domain. Same Authelia
# session cookie (domain=home.example.org) covers both studio.* and beets.*, so
# a user already logged into the Music Studio UI hits this transparently.
BEETS_PUBLIC_URL     = os.getenv("MUSIC_BEETS_PUBLIC_URL",   "https://beets.home.example.org")
KNOWN_USERS         = ["owner", "partner"]
DEMUCS_MODEL        = os.getenv("DEMUCS_MODEL",             "htdemucs")
BP_ONSET_THRESHOLD  = float(os.getenv("BASIC_PITCH_ONSET_THRESHOLD", "0.5"))
BP_FRAME_THRESHOLD  = float(os.getenv("BASIC_PITCH_FRAME_THRESHOLD", "0.3"))
BP_MIN_NOTE_LEN     = float(os.getenv("BASIC_PITCH_MIN_NOTE_LEN",    "127.5"))

for d in (SESSION_FILES_DIR, FIELD_DIR, MIDI_OUTPUT_DIR, STEMS_OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                    format="[%(asctime)s] %(levelname)s %(name)s — %(message)s")
log = logging.getLogger("music-catalog")

# ============================================================================
# App
# ============================================================================

app = FastAPI(
    title="Music Studio Catalog API",
    description="Elementary Particles: music session, song, track and clip management",
    version="1.0.0",
)

_pool: Optional[asyncpg.Pool] = None


@app.on_event("startup")
async def startup():
    global _pool
    _pool = await asyncpg.create_pool(DB_DSN, min_size=2, max_size=10)
    log.info("Database pool ready")


@app.on_event("shutdown")
async def shutdown():
    if _pool:
        await _pool.close()


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised")
    return _pool


# ============================================================================
# Pydantic models
# ============================================================================

class SessionCreate(BaseModel):
    title:          str
    project_file:   Optional[str] = None
    session_dir:    Optional[str] = None
    daw:            str = "ableton"
    status:         str = "draft"
    mood:           List[str] = []
    genre:          List[str] = []
    tempo_bpm:      Optional[float] = None
    key_signature:  Optional[str] = None
    time_signature: str = "4/4"
    notes:          Optional[str] = None
    location:       Optional[str] = None
    source_device:  Optional[str] = None
    recorded_at:    Optional[datetime] = None

class SessionUpdate(BaseModel):
    title:          Optional[str] = None
    status:         Optional[str] = None
    mood:           Optional[List[str]] = None
    genre:          Optional[List[str]] = None
    tempo_bpm:      Optional[float] = None
    key_signature:  Optional[str] = None
    notes:          Optional[str] = None
    project_file:   Optional[str] = None
    session_dir:    Optional[str] = None
    location:       Optional[str] = None
    source_device:  Optional[str] = None


class SongCreate(BaseModel):
    title:              str
    composer:           List[str] = ["Self"]
    genre:              List[str] = []
    mood:               List[str] = []
    key_signature:      Optional[str] = None
    tempo_bpm:          Optional[float] = None
    time_signature:     str = "4/4"
    status:             str = "draft"
    is_original:        bool = True
    cover_source_title:  Optional[str] = None
    cover_source_artist: Optional[str] = None
    cover_source_url:    Optional[str] = None
    arrangement_notes:  Optional[str] = None
    lyrics:             Optional[str] = None
    notes:              Optional[str] = None
    tags:               List[str] = []

class SongUpdate(BaseModel):
    title:              Optional[str] = None
    status:             Optional[str] = None
    mood:               Optional[List[str]] = None
    genre:              Optional[List[str]] = None
    key_signature:      Optional[str] = None
    tempo_bpm:          Optional[float] = None
    notes:              Optional[str] = None
    lyrics:             Optional[str] = None
    tags:               Optional[List[str]] = None
    arrangement_notes:  Optional[str] = None


class TrackCreate(BaseModel):
    name:          str
    type:          str = "audio"          # audio | midi | instrument | return | master | group
    instrument_id: Optional[str] = None
    color:         str = "#AAAAAA"
    position:      int = 0
    volume_db:     float = 0.0
    pan:           float = 0.0
    muted:         bool = False
    soloed:        bool = False
    notes:         Optional[str] = None


class ClipCreate(BaseModel):
    name:          Optional[str] = None
    type:          str = "audio"
    file_path:     Optional[str] = None
    start_beat:    float = 0.0
    duration_beats: Optional[float] = None
    duration_sec:  Optional[float] = None
    source_device: Optional[str] = None
    notes:         Optional[str] = None
    recorded_at:   Optional[datetime] = None


class ReferenceCreate(BaseModel):
    title:         str
    artist:        Optional[str] = None
    album:         Optional[str] = None
    year:          Optional[int] = None
    genre:         List[str] = []
    mood:          List[str] = []
    key_signature: Optional[str] = None
    tempo_bpm:     Optional[float] = None
    url:           Optional[str] = None
    tab_url:       Optional[str] = None
    notes:         Optional[str] = None
    status:        str = "wishlist"
    difficulty:    Optional[str] = None
    tags:          List[str] = []

class ReferenceUpdate(BaseModel):
    status:        Optional[str] = None
    notes:         Optional[str] = None
    difficulty:    Optional[str] = None
    key_signature: Optional[str] = None
    tempo_bpm:     Optional[float] = None
    tab_url:       Optional[str] = None
    song_id:       Optional[str] = None


class FieldRecordingCreate(BaseModel):
    file_path:     str
    duration_sec:  Optional[float] = None
    recorded_at:   Optional[datetime] = None
    location:      Optional[str] = None
    device:        Optional[str] = None
    description:   Optional[str] = None
    tags:          List[str] = []


class PluginCreate(BaseModel):
    name:         str
    manufacturer: Optional[str] = None
    type:         str = "vst3"
    category:     str = "effect"
    version:      Optional[str] = None
    path:         Optional[str] = None
    is_foss:      bool = False
    license:      Optional[str] = None
    notes:        Optional[str] = None


class AnnotationCreate(BaseModel):
    entity_type: str    # session | song | track | clip | reference | field_recording | library | beets_track
    entity_id:   str    # Postgres UUID (as string) or a Beets integer item id (as string)
    note:        str
    category:    str = "general"   # improvement | mix | arrangement | lyrics | performance | general


class EngagementUpdate(BaseModel):
    status:           Optional[str] = None   # new | reviewing | needs_work | approved
    rating:           Optional[int] = None   # 1-5
    notes:            Optional[str] = None
    position_seconds: Optional[int] = None


# ============================================================================
# Helpers
# ============================================================================

def _row_to_dict(row) -> Dict:
    if row is None:
        return {}
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, (datetime,)):
            d[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            d[k] = str(v)
    return d


def _rows_to_list(rows) -> List[Dict]:
    return [_row_to_dict(r) for r in rows]


async def _get_or_404(table: str, id: str) -> Dict:
    row = await pool().fetchrow(
        f"SELECT * FROM music.{table} WHERE id = $1", uuid.UUID(id)
    )
    if not row:
        raise HTTPException(404, f"{table} {id} not found")
    return _row_to_dict(row)


# ============================================================================
# Health
# ============================================================================

@app.get("/health")
async def health():
    try:
        await pool().fetchval("SELECT 1")
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        return JSONResponse({"status": "error", "db": str(e)}, status_code=503)


# ============================================================================
# Dashboard
# ============================================================================

@app.get("/api/v1/dashboard")
async def dashboard():
    row = await pool().fetchrow("SELECT * FROM music.dashboard")
    return _row_to_dict(row)


# ============================================================================
# Sessions
# ============================================================================

@app.get("/api/v1/sessions")
async def list_sessions(
    status: Optional[str] = None,
    mood:   Optional[str] = None,
    limit:  int = Query(50, le=500),
    offset: int = 0,
):
    q = "SELECT * FROM music.sessions"
    conds, args = [], []
    if status:
        args.append(status)
        conds.append(f"status = ${len(args)}")
    if mood:
        args.append([mood])
        conds.append(f"mood && ${len(args)}")
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += f" ORDER BY created_at DESC LIMIT {limit} OFFSET {offset}"
    rows = await pool().fetch(q, *args)
    return _rows_to_list(rows)


@app.post("/api/v1/sessions", status_code=201)
async def create_session(body: SessionCreate):
    row = await pool().fetchrow(
        """INSERT INTO music.sessions
           (title, project_file, session_dir, daw, status, mood, genre,
            tempo_bpm, key_signature, time_signature, notes, location,
            source_device, recorded_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
           RETURNING *""",
        body.title, body.project_file, body.session_dir, body.daw,
        body.status, body.mood, body.genre,
        body.tempo_bpm, body.key_signature, body.time_signature,
        body.notes, body.location, body.source_device, body.recorded_at,
    )
    return _row_to_dict(row)


@app.get("/api/v1/sessions/{session_id}")
async def get_session(session_id: str):
    return await _get_or_404("sessions", session_id)


@app.put("/api/v1/sessions/{session_id}")
async def update_session(session_id: str, body: SessionUpdate):
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    set_clause = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
    row = await pool().fetchrow(
        f"UPDATE music.sessions SET {set_clause} WHERE id = $1 RETURNING *",
        uuid.UUID(session_id), *updates.values(),
    )
    if not row:
        raise HTTPException(404, f"Session {session_id} not found")
    return _row_to_dict(row)


# ============================================================================
# Tracks (within a session)
# ============================================================================

@app.get("/api/v1/sessions/{session_id}/tracks")
async def list_tracks(session_id: str):
    rows = await pool().fetch(
        "SELECT t.*, i.name AS instrument_name FROM music.tracks t "
        "LEFT JOIN music.instruments i ON t.instrument_id = i.id "
        "WHERE t.session_id = $1 ORDER BY t.position",
        uuid.UUID(session_id),
    )
    return _rows_to_list(rows)


@app.post("/api/v1/sessions/{session_id}/tracks", status_code=201)
async def create_track(session_id: str, body: TrackCreate):
    row = await pool().fetchrow(
        """INSERT INTO music.tracks
           (session_id, name, type, instrument_id, color, position,
            volume_db, pan, muted, soloed, notes)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
           RETURNING *""",
        uuid.UUID(session_id), body.name, body.type,
        uuid.UUID(body.instrument_id) if body.instrument_id else None,
        body.color, body.position, body.volume_db, body.pan,
        body.muted, body.soloed, body.notes,
    )
    return _row_to_dict(row)


@app.get("/api/v1/tracks/{track_id}/clips")
async def list_clips(track_id: str):
    rows = await pool().fetch(
        "SELECT * FROM music.clips WHERE track_id = $1 ORDER BY start_beat",
        uuid.UUID(track_id),
    )
    return _rows_to_list(rows)


@app.post("/api/v1/tracks/{track_id}/clips", status_code=201)
async def create_clip(track_id: str, body: ClipCreate):
    row = await pool().fetchrow(
        """INSERT INTO music.clips
           (track_id, name, type, file_path, start_beat, duration_beats,
            duration_sec, source_device, notes, recorded_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
           RETURNING *""",
        uuid.UUID(track_id), body.name, body.type, body.file_path,
        body.start_beat, body.duration_beats, body.duration_sec,
        body.source_device, body.notes, body.recorded_at,
    )
    return _row_to_dict(row)


# ============================================================================
# Songs
# ============================================================================

@app.get("/api/v1/songs")
async def list_songs(
    status:      Optional[str] = None,
    is_original: Optional[bool] = None,
    limit:       int = Query(50, le=500),
    offset:      int = 0,
):
    q = "SELECT * FROM music.songs"
    conds, args = [], []
    if status:
        args.append(status); conds.append(f"status = ${len(args)}")
    if is_original is not None:
        args.append(is_original); conds.append(f"is_original = ${len(args)}")
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += f" ORDER BY created_at DESC LIMIT {limit} OFFSET {offset}"
    return _rows_to_list(await pool().fetch(q, *args))


@app.post("/api/v1/songs", status_code=201)
async def create_song(body: SongCreate):
    row = await pool().fetchrow(
        """INSERT INTO music.songs
           (title, composer, genre, mood, key_signature, tempo_bpm,
            time_signature, status, is_original, cover_source_title,
            cover_source_artist, cover_source_url, arrangement_notes,
            lyrics, notes, tags)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
           RETURNING *""",
        body.title, body.composer, body.genre, body.mood,
        body.key_signature, body.tempo_bpm, body.time_signature,
        body.status, body.is_original, body.cover_source_title,
        body.cover_source_artist, body.cover_source_url,
        body.arrangement_notes, body.lyrics, body.notes, body.tags,
    )
    return _row_to_dict(row)


@app.get("/api/v1/songs/{song_id}")
async def get_song(song_id: str):
    return await _get_or_404("songs", song_id)


@app.put("/api/v1/songs/{song_id}")
async def update_song(song_id: str, body: SongUpdate):
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    set_clause = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
    row = await pool().fetchrow(
        f"UPDATE music.songs SET {set_clause} WHERE id = $1 RETURNING *",
        uuid.UUID(song_id), *updates.values(),
    )
    if not row:
        raise HTTPException(404, f"Song {song_id} not found")
    return _row_to_dict(row)


# ============================================================================
# References (covers wishlist)
# ============================================================================

@app.get("/api/v1/references")
async def list_references(
    status: Optional[str] = None,
    limit:  int = Query(50, le=500),
    offset: int = 0,
):
    q = "SELECT * FROM music.references"
    if status:
        q += " WHERE status = $1 ORDER BY created_at DESC"
        rows = await pool().fetch(q + f" LIMIT {limit} OFFSET {offset}", status)
    else:
        rows = await pool().fetch(q + f" ORDER BY created_at DESC LIMIT {limit} OFFSET {offset}")
    return _rows_to_list(rows)


@app.post("/api/v1/references", status_code=201)
async def create_reference(body: ReferenceCreate):
    row = await pool().fetchrow(
        """INSERT INTO music.references
           (title, artist, album, year, genre, mood, key_signature,
            tempo_bpm, url, tab_url, notes, status, difficulty, tags)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
           RETURNING *""",
        body.title, body.artist, body.album, body.year,
        body.genre, body.mood, body.key_signature, body.tempo_bpm,
        body.url, body.tab_url, body.notes,
        body.status, body.difficulty, body.tags,
    )
    return _row_to_dict(row)


@app.patch("/api/v1/references/{ref_id}")
async def update_reference(ref_id: str, body: ReferenceUpdate):
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    # song_id needs UUID conversion
    if "song_id" in updates:
        updates["song_id"] = uuid.UUID(updates["song_id"])
    set_clause = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
    row = await pool().fetchrow(
        f"UPDATE music.references SET {set_clause} WHERE id = $1 RETURNING *",
        uuid.UUID(ref_id), *updates.values(),
    )
    if not row:
        raise HTTPException(404, f"Reference {ref_id} not found")
    return _row_to_dict(row)


# ============================================================================
# Field Recordings
# ============================================================================

@app.get("/api/v1/field-recordings")
async def list_field_recordings(
    status: Optional[str] = "inbox",
    limit:  int = Query(50, le=500),
):
    rows = await pool().fetch(
        "SELECT * FROM music.field_recordings "
        "WHERE ($1::TEXT IS NULL OR status = $1) "
        "ORDER BY recorded_at DESC NULLS LAST LIMIT $2",
        status, limit,
    )
    return _rows_to_list(rows)


@app.post("/api/v1/field-recordings", status_code=201)
async def create_field_recording(body: FieldRecordingCreate):
    row = await pool().fetchrow(
        """INSERT INTO music.field_recordings
           (file_path, duration_sec, recorded_at, location, device, description, tags)
           VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *""",
        body.file_path, body.duration_sec, body.recorded_at,
        body.location, body.device, body.description, body.tags,
    )
    return _row_to_dict(row)


@app.post("/api/v1/field-recordings/upload")
async def upload_field_recording(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    device: str = "unknown",
    description: str = "",
    tags: str = "",
):
    """Accept a file upload and create a field recording entry."""
    dest = FIELD_DIR / _safe_upload_name(file.filename)
    if dest.exists():
        raise HTTPException(409, f"a recording named {dest.name} already exists")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    row = await pool().fetchrow(
        """INSERT INTO music.field_recordings
           (file_path, device, description, tags, recorded_at)
           VALUES ($1,$2,$3,$4,NOW()) RETURNING *""",
        str(dest), device, description, tag_list,
    )
    result = _row_to_dict(row)
    background_tasks.add_task(_analyse_field_recording, str(result["id"]), str(dest))
    return result


async def _analyse_field_recording(rec_id: str, file_path: str):
    """Background task: call audio-analysis service to get BPM/key/mood."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{AUDIO_ANALYSIS_URL}/api/analyse",
                json={"file_path": file_path},
            )
            if resp.status_code == 200:
                analysis = resp.json()
                await pool().execute(
                    "UPDATE music.field_recordings SET analysis_id = $1 WHERE id = $2",
                    uuid.UUID(str(analysis.get("id", ""))),
                    uuid.UUID(rec_id),
                )
    except Exception as e:
        log.warning(f"Audio analysis for field recording {rec_id} failed: {e}")


# ============================================================================
# Audio → MIDI  (Basic Pitch)
# ============================================================================

@app.post("/api/v1/field-recordings/{rec_id}/transcribe")
async def transcribe_to_midi(rec_id: str, background_tasks: BackgroundTasks):
    """Transcribe a field recording to MIDI using Spotify Basic Pitch."""
    row = await _get_or_404("field_recordings", rec_id)
    file_path = str(_audio_path_or_400(row.get("file_path")))

    output_dir = MIDI_OUTPUT_DIR / rec_id
    output_dir.mkdir(parents=True, exist_ok=True)

    background_tasks.add_task(_run_basic_pitch, rec_id, file_path, str(output_dir))
    return {"status": "queued", "output_dir": str(output_dir), "recording_id": rec_id}


async def _run_basic_pitch(rec_id: str, file_path: str, output_dir: str):
    """Run basic-pitch in a subprocess and update the database with the MIDI path."""
    cmd = [
        "basic-pitch", output_dir, file_path,
        "--onset-threshold",     str(BP_ONSET_THRESHOLD),
        "--frame-threshold",     str(BP_FRAME_THRESHOLD),
        "--minimum-note-length", str(BP_MIN_NOTE_LEN),
        "--save-midi",
    ]
    log.info(f"Running Basic Pitch: {' '.join(cmd)}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode != 0:
            log.error(f"Basic Pitch failed for {rec_id}: {stderr.decode()}")
            return

        # Find the generated MIDI file
        midi_files = list(Path(output_dir).glob("*.mid")) + list(Path(output_dir).glob("*.midi"))
        midi_path  = str(midi_files[0]) if midi_files else None

        await pool().execute(
            "UPDATE music.field_recordings SET midi_path = $1, status = 'reviewed' WHERE id = $2",
            midi_path, uuid.UUID(rec_id),
        )
        log.info(f"Basic Pitch complete for {rec_id}: {midi_path}")
    except asyncio.TimeoutError:
        log.error(f"Basic Pitch timed out for {rec_id}")
    except Exception as e:
        log.error(f"Basic Pitch error for {rec_id}: {e}")


# ============================================================================
# Stem Separation  (Demucs)
# ============================================================================

@app.post("/api/v1/field-recordings/{rec_id}/separate")
async def separate_stems(
    rec_id: str,
    background_tasks: BackgroundTasks,
    model: str = DEMUCS_MODEL,
):
    """Separate audio into drums / bass / vocals / other stems using Demucs."""
    row = await _get_or_404("field_recordings", rec_id)
    file_path = str(_audio_path_or_400(row.get("file_path")))

    out_dir = STEMS_OUTPUT_DIR / rec_id
    out_dir.mkdir(parents=True, exist_ok=True)

    background_tasks.add_task(_run_demucs, rec_id, file_path, str(out_dir), model)
    return {"status": "queued", "stems_dir": str(out_dir), "model": model, "recording_id": rec_id}


async def _run_demucs(rec_id: str, file_path: str, out_dir: str, model: str):
    cmd = ["python3", "-m", "demucs", "-n", model, "-o", out_dir, file_path]
    log.info(f"Running Demucs: {' '.join(cmd)}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=1800)  # 30 min
        if proc.returncode != 0:
            log.error(f"Demucs failed for {rec_id}: {stderr.decode()}")
            return
        await pool().execute(
            "UPDATE music.field_recordings SET stems_dir = $1 WHERE id = $2",
            out_dir, uuid.UUID(rec_id),
        )
        log.info(f"Demucs complete for {rec_id}: {out_dir}")
    except asyncio.TimeoutError:
        log.error(f"Demucs timed out for {rec_id}")
    except Exception as e:
        log.error(f"Demucs error for {rec_id}: {e}")


# ============================================================================
# Plugins & Instruments
# ============================================================================

@app.get("/api/v1/plugins")
async def list_plugins(category: Optional[str] = None, is_foss: Optional[bool] = None):
    q = "SELECT * FROM music.plugins"
    conds, args = [], []
    if category:
        args.append(category); conds.append(f"category = ${len(args)}")
    if is_foss is not None:
        args.append(is_foss); conds.append(f"is_foss = ${len(args)}")
    if conds:
        q += " WHERE " + " AND ".join(conds)
    return _rows_to_list(await pool().fetch(q + " ORDER BY name", *args))


@app.post("/api/v1/plugins", status_code=201)
async def create_plugin(body: PluginCreate):
    row = await pool().fetchrow(
        """INSERT INTO music.plugins
           (name, manufacturer, type, category, version, path, is_foss, license, notes)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *""",
        body.name, body.manufacturer, body.type, body.category,
        body.version, body.path, body.is_foss, body.license, body.notes,
    )
    return _row_to_dict(row)


@app.get("/api/v1/instruments")
async def list_instruments():
    return _rows_to_list(await pool().fetch("SELECT * FROM music.instruments ORDER BY name"))


# ============================================================================
# Library  (Beets-managed finished tracks)
# ============================================================================

@app.get("/api/v1/library")
async def list_library(
    genre: Optional[str] = None,
    mood:  Optional[str] = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
):
    q = "SELECT * FROM music.library"
    conds, args = [], []
    if genre:
        args.append(f"%{genre}%"); conds.append(f"genre ILIKE ${len(args)}")
    if mood:
        args.append(f"%{mood}%"); conds.append(f"mood ILIKE ${len(args)}")
    if conds:
        q += " WHERE " + " AND ".join(conds)
    return _rows_to_list(await pool().fetch(q + f" ORDER BY imported_at DESC LIMIT {limit} OFFSET {offset}", *args))


# ============================================================================
# Annotations — feedback/notes history (running log, never overwritten —
# see music.annotations in music_schema.sql for why this is separate from
# the single `notes` column already on sessions/songs/tracks/etc)
# ============================================================================

@app.post("/api/v1/annotations", status_code=201)
async def create_annotation(body: AnnotationCreate):
    audio_id = None
    if body.entity_type == "beets_track" and body.entity_id.isdigit():
        audio_id = await _audio_id_for_item(int(body.entity_id))
    row = await pool().fetchrow(
        """INSERT INTO music.annotations (entity_type, entity_id, note, category, audio_id)
           VALUES ($1,$2,$3,$4,$5) RETURNING *""",
        body.entity_type, body.entity_id, body.note, body.category, audio_id,
    )
    return _row_to_dict(row)


@app.get("/api/v1/annotations")
async def list_annotations(
    entity_type: Optional[str] = None,
    entity_id:   Optional[str] = None,
    limit:       int = Query(50, le=200),
):
    """Notes for one specific entity, newest first."""
    conds, args = [], []
    if entity_type:
        args.append(entity_type); conds.append(f"entity_type = ${len(args)}")
    if entity_id:
        args.append(entity_id); conds.append(f"entity_id = ${len(args)}")
    q = "SELECT * FROM music.annotations"
    if conds:
        q += " WHERE " + " AND ".join(conds)
    return _rows_to_list(await pool().fetch(q + f" ORDER BY created_at DESC LIMIT {limit}", *args))


@app.get("/api/v1/annotations/recent")
async def recent_annotations(limit: int = Query(20, le=100)):
    """Global feed across every session/song/etc — 'what needs work right now'.
    Joined against search_all so the response carries a human-readable title
    for Postgres entities; beets_track entities (search_all doesn't cover
    Beets' separate SQLite library) get their title resolved below instead."""
    rows = await pool().fetch(
        """SELECT a.*, s.title AS entity_title
           FROM music.annotations a
           LEFT JOIN music.search_all s
             ON s.entity_type = a.entity_type AND s.id = a.entity_id
           ORDER BY a.created_at DESC LIMIT $1""",
        limit,
    )
    entries = _rows_to_list(rows)
    beets_ids = [int(e["entity_id"]) for e in entries if e["entity_type"] == "beets_track"]
    if beets_ids:
        titles = await _beets_title_map(beets_ids)
        for e in entries:
            if e["entity_type"] == "beets_track":
                e["entity_title"] = titles.get(int(e["entity_id"]), f"Track #{e['entity_id']}")
    return entries


# ============================================================================
# Track engagement — per-user status/rating/notes on a playable Beets track.
# Mirrors podcast.home.example.org's episode_engagement exactly (see
# playback_api_server.py) — same upsert-by-(item,user) shape, same
# "/ui/history = every engagement record, most recent first" idea. This is
# NOT the same thing as music.annotations above — see the schema comment on
# music.track_engagement in music_schema.sql for why both exist.
#
# Also lives here: proxying/merging Beets' own item API (music-beets:8337)
# with this engagement data, since the browse/history/detail UI (built into
# music-assistant, studio.home.example.org) needs both combined and Beets' web UI
# has no engagement concept of its own to merge against.
# ============================================================================

async def _beets_get(path: str, **params) -> Any:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BEETS_URL}{path}", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()


async def _engagement_map(item_ids: List[int], user_id: str) -> Dict[int, Dict]:
    """Bulk-fetch engagement rows for a set of item_ids, keyed by item_id.
    Missing rows are NOT synthesized here (unlike get_engagement below) —
    callers merge in their own 'new'/None defaults per item."""
    if not item_ids:
        return {}
    rows = await pool().fetch(
        "SELECT * FROM music.track_engagement WHERE user_id = $1 AND item_id = ANY($2::int[])",
        user_id, item_ids,
    )
    return {r["item_id"]: _row_to_dict(r) for r in rows}


def _default_engagement(item_id: int, user_id: str) -> Dict:
    return {"item_id": item_id, "user_id": user_id, "status": "new",
            "rating": None, "notes": None, "position_seconds": 0, "updated_at": None}


# NOTE ON ORDER: FastAPI/Starlette matches routes in registration order, so
# the literal-path routes below (/history, /notes/search) MUST come before
# the parameterized /{item_id} routes — otherwise "history"/"notes" get
# captured as item_id and fail int parsing with a 422. Found live
# 2026-08-22 testing the history page for the first time (GET
# /api/v1/engagement/history 422'd: "unable to parse 'history' as an
# integer" — it had matched /{item_id} instead of the intended route).

@app.get("/api/v1/engagement/history")
async def engagement_history(
    status: Optional[str] = Query(None, pattern="^(new|reviewing|needs_work|approved)$"),
    limit:  int = Query(200, le=500),
):
    """Every engagement entry across every user, most recently updated
    first — the data source for /ui/history. Not scoped to one user by
    design, same reasoning as podcast's version: a shared household view of
    what's been reviewed, rated, or flagged, whoever left it."""
    where = "WHERE status = $1" if status else ""
    args  = [status] if status else []
    rows = await pool().fetch(
        f"SELECT * FROM music.track_engagement {where} ORDER BY updated_at DESC LIMIT {limit}",
        *args,
    )
    entries = _rows_to_list(rows)
    if entries:
        titles = await _beets_title_map([e["item_id"] for e in entries])
        for e in entries:
            e["title"] = titles.get(e["item_id"], f"Track #{e['item_id']} (missing from Beets)")
    return {"count": len(entries), "entries": entries}


@app.get("/api/v1/engagement/notes/search")
async def engagement_notes_search(q: str = Query(..., min_length=1), limit: int = Query(30, le=100)):
    """Search the text of engagement notes themselves — distinct from
    /api/v1/tracks/search, which searches Beets' own tags, not what you
    wrote about a track."""
    rows = await pool().fetch(
        "SELECT * FROM music.track_engagement WHERE notes ILIKE $1 ORDER BY updated_at DESC LIMIT $2",
        f"%{q}%", limit,
    )
    entries = _rows_to_list(rows)
    if entries:
        titles = await _beets_title_map([e["item_id"] for e in entries])
        for e in entries:
            e["title"] = titles.get(e["item_id"], f"Track #{e['item_id']} (missing from Beets)")
    return {"query": q, "count": len(entries), "results": entries}


@app.get("/api/v1/engagement/{item_id}")
async def get_engagement(item_id: int, user_id: str = Query("owner")):
    row = await pool().fetchrow(
        "SELECT * FROM music.track_engagement WHERE item_id = $1 AND user_id = $2",
        item_id, user_id,
    )
    return _row_to_dict(row) if row else _default_engagement(item_id, user_id)


@app.put("/api/v1/engagement/{item_id}")
async def set_engagement(item_id: int, body: EngagementUpdate, user_id: str = Query("owner")):
    await pool().execute(
        """INSERT INTO music.track_engagement (item_id, user_id, status, rating, notes, position_seconds, audio_id, updated_at)
           VALUES ($1, $2, COALESCE($3, 'new'), $4, $5, COALESCE($6, 0), $7, NOW())
           ON CONFLICT (item_id, user_id) DO UPDATE SET
             status           = COALESCE(EXCLUDED.status, music.track_engagement.status),
             rating           = COALESCE(EXCLUDED.rating, music.track_engagement.rating),
             notes            = COALESCE(EXCLUDED.notes, music.track_engagement.notes),
             position_seconds = COALESCE(EXCLUDED.position_seconds, music.track_engagement.position_seconds),
             audio_id         = COALESCE(music.track_engagement.audio_id, EXCLUDED.audio_id),
             updated_at       = NOW()""",
        item_id, user_id, body.status, body.rating, body.notes, body.position_seconds,
        await _audio_id_for_item(item_id),
    )
    return await get_engagement(item_id, user_id)


async def _beets_title_map(item_ids: List[int]) -> Dict[int, str]:
    """One bulk fetch of Beets' whole item list -> {id: 'artist - title'}.
    Cheap at our scale (dozens-hundreds of tracks); avoids N+1 calls to
    Beets when enriching a history/search result set."""
    try:
        data = await _beets_get("/item/")
    except Exception:
        log.warning("Beets unreachable while building title map")
        return {}
    out = {}
    for item in data.get("items", []):
        artist = item.get("artist") or ""
        title  = item.get("title") or f"Track #{item['id']}"
        out[item["id"]] = f"{artist} - {title}".strip(" -") if artist else title
    return out


def _merge_beets_item(item: Dict, engagement: Optional[Dict], user_id: str) -> Dict:
    eng = engagement or _default_engagement(item["id"], user_id)
    return {
        "item_id":  item["id"],
        "artist":   item.get("artist") or "",
        "title":    item.get("title") or f"Track #{item['id']}",
        "album":    item.get("album") or "",
        "format":   item.get("format"),
        "length":   item.get("length"),
        "genre":    item.get("genre") or "",
        "year":     item.get("year") or item.get("original_year"),
        "play_url": f"{BEETS_PUBLIC_URL}/item/{item['id']}/file",
        "engagement": eng,
    }


@app.get("/api/v1/tracks")
async def list_tracks(user_id: str = Query("owner"), limit: int = Query(100, le=500)):
    """All playable Beets tracks, merged with this user's engagement —
    the data source for the main /ui browse page when there's no search
    query yet."""
    data = await _beets_get("/item/")
    items = data.get("items", [])[:limit]
    eng_map = await _engagement_map([i["id"] for i in items], user_id)
    return [_merge_beets_item(i, eng_map.get(i["id"]), user_id) for i in items]


@app.get("/api/v1/tracks/search")
async def search_tracks(q: str, user_id: str = Query("owner"), limit: int = Query(50, le=200)):
    """Search Beets' own tags (artist/title/album/genre) via its query
    endpoint, merged with engagement. See _beets_get docstring context —
    Beets' own query language, not a SQL LIKE, so results depend on what's
    actually tagged (many personal recordings have thin tags)."""
    try:
        data = await _beets_get(f"/item/query/{q}")
    except httpx.HTTPStatusError:
        data = {}
    items = data.get("results", data.get("items", []))[:limit]
    eng_map = await _engagement_map([i["id"] for i in items], user_id)
    return [_merge_beets_item(i, eng_map.get(i["id"]), user_id) for i in items]


@app.get("/api/v1/tracks/{item_id}")
async def get_track(item_id: int, user_id: str = Query("owner")):
    try:
        item = await _beets_get(f"/item/{item_id}")
    except httpx.HTTPStatusError:
        raise HTTPException(404, f"Track {item_id} not found in Beets")
    eng = await get_engagement(item_id, user_id)
    return _merge_beets_item(item, eng, user_id)


# ============================================================================
# Audio identity — stable content-hash ids for recordings (music.audio_files /
# music.audio_locations, populated by audio_identity_sync.py). Beets item ids
# shift on a re-scan; audio_id doesn't. Everything the musician platform
# attaches to a recording (analysis, lyric alignment, song links) keys on it.
# ============================================================================

async def _audio_id_for_item(item_id: int) -> Optional[uuid.UUID]:
    """Current audio_id for a Beets item, or None if the sync hasn't seen it
    yet (new import since the last nightly run) — callers store None and the
    next sync backfills it."""
    return await pool().fetchval(
        "SELECT audio_id FROM music.audio_locations WHERE beets_item_id = $1 AND missing_since IS NULL",
        item_id,
    )


async def _audio_file_with_locations(where: str, arg) -> Dict:
    row = await pool().fetchrow(f"SELECT * FROM music.audio_files WHERE {where}", arg)
    if not row:
        raise HTTPException(404, "audio file not found")
    out = _row_to_dict(row)
    out["locations"] = _rows_to_list(await pool().fetch(
        """SELECT path, beets_item_id, hash_source, size_bytes, first_seen, last_seen, missing_since
           FROM music.audio_locations WHERE audio_id = $1 ORDER BY missing_since NULLS FIRST, path""",
        row["id"],
    ))
    return out


# Literal-path routes before /{audio_id} — same registration-order trap as
# the engagement routes above.
@app.get("/api/v1/audio-files/by-beets/{item_id}")
async def audio_file_by_beets(item_id: int):
    audio_id = await _audio_id_for_item(item_id)
    if not audio_id:
        raise HTTPException(404, f"Beets item {item_id} has no audio identity yet (not synced, or file missing)")
    return await _audio_file_with_locations("id = $1", audio_id)


@app.get("/api/v1/audio-files/by-hash/{content_hash}")
async def audio_file_by_hash(content_hash: str):
    return await _audio_file_with_locations("content_hash = $1", content_hash.lower())


@app.get("/api/v1/audio-files/{audio_id}")
async def get_audio_file(audio_id: uuid.UUID):
    return await _audio_file_with_locations("id = $1", audio_id)


# ============================================================================
# Musician Platform phase 1 — songs as the hub (IaC/docs/MUSICIAN_PLATFORM.md)
#
# * Song sheets live in git (Forgejo owner/songbook, songs/<slug>/song.cho).
#   Postgres only indexes them (music.song_documents). Reads always go to
#   Forgejo, so an edit made from a git client or a ChordPro app shows up on
#   the next page load; saves use Forgejo's blob sha as an optimistic lock —
#   if the file changed in git since the page was opened, the save is refused
#   (409) instead of silently overwriting it.
# * Song membership comes from music.song_suggestions (song_suggest.py, the
#   local gpu-01 LLM) and is only applied when a human accepts it.
# ============================================================================

import base64
import re as _re
import tempfile

FORGEJO_URL     = os.getenv("FORGEJO_URL", "http://10.0.30.31:3000")
FORGEJO_TOKEN   = os.getenv("FORGEJO_TOKEN", "")
SONGBOOK_OWNER  = os.getenv("SONGBOOK_OWNER", "owner")
SONGBOOK_REPO   = os.getenv("SONGBOOK_REPO", "songbook")
SONGBOOK_BRANCH = os.getenv("SONGBOOK_BRANCH", "main")
SONGBOOK_PUBLIC = os.getenv("SONGBOOK_PUBLIC_URL", "https://git.home.example.org")
STUDIO_AUTHOR   = {"name": "Music Studio", "email": "admin@home.example.org"}

_DOC_KINDS = {".cho": "chordpro", ".chopro": "chordpro", ".chordpro": "chordpro", ".alphatex": "alphatex",
              ".gp": "guitarpro", ".gp3": "guitarpro", ".gp4": "guitarpro", ".gp5": "guitarpro", ".gpx": "guitarpro",
              ".musicxml": "musicxml", ".xml": "musicxml", ".mid": "midi", ".midi": "midi", ".pdf": "pdf"}


class SongbookConflict(Exception):
    pass


async def _forgejo(method: str, path: str, **kw) -> httpx.Response:
    if not FORGEJO_TOKEN:
        raise HTTPException(503, "Songbook not configured (FORGEJO_TOKEN unset)")
    async with httpx.AsyncClient() as client:
        return await client.request(
            method, f"{FORGEJO_URL}/api/v1/repos/{SONGBOOK_OWNER}/{SONGBOOK_REPO}{path}",
            headers={"Authorization": f"token {FORGEJO_TOKEN}"}, timeout=20, **kw)


async def songbook_read(repo_path: str) -> Optional[Dict]:
    r = await _forgejo("GET", f"/contents/{repo_path}", params={"ref": SONGBOOK_BRANCH})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = r.json()
    return {"path": repo_path, "sha": d["sha"], "content": base64.b64decode(d["content"]).decode("utf-8")}


async def songbook_write(repo_path: str, content: str, message: str, sha: Optional[str]) -> Dict:
    """Create (sha=None) or update (sha = blob sha the editor started from)."""
    body = {"content": base64.b64encode(content.encode("utf-8")).decode(), "message": message,
            "branch": SONGBOOK_BRANCH, "author": STUDIO_AUTHOR, "committer": STUDIO_AUTHOR}
    if sha:
        body["sha"] = sha
    r = await _forgejo("PUT" if sha else "POST", f"/contents/{repo_path}", json=body)
    if r.status_code in (409, 422) or (r.status_code == 400 and "sha" in r.text.lower()):
        raise SongbookConflict(r.text[:300])
    r.raise_for_status()
    d = r.json()
    return {"sha": d["content"]["sha"], "commit": d["commit"]["sha"]}


def _slugify(text: str) -> str:
    s = _re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60].strip("-") or "untitled"


async def _unique_slug(base: str) -> str:
    slug, n = base, 2
    while await pool().fetchval("SELECT 1 FROM music.songs WHERE slug = $1", slug):
        slug, n = f"{base}-{n}", n + 1
    return slug


def _directive(text: str, name: str) -> Optional[str]:
    m = _re.search(r"^\{\s*" + name + r"\s*:\s*(.*?)\s*\}\s*$", text, _re.M | _re.I)
    return m.group(1) if m else None


def _skeleton_chordpro(song: Dict) -> str:
    lines = [f"{{title: {song['title']}}}"]
    if song.get("project"):
        lines.append(f"{{artist: {song['project']}}}")
    lines += [f"{{meta: song_id {song['id']}}}", "", "{start_of_verse: Verse 1}",
              "[C]Lyrics and chords go here — see README.md for the conventions.", "{end_of_verse}", ""]
    return "\n".join(lines)


async def _index_document(song_id, repo_path: str, blob_sha: str, commit_sha: Optional[str]):
    ext = os.path.splitext(repo_path)[1].lower()
    await pool().execute(
        """INSERT INTO music.song_documents (song_id, repo_path, kind, blob_sha, commit_sha, updated_at)
           VALUES ($1,$2,$3,$4,$5,NOW())
           ON CONFLICT (song_id, repo_path) DO UPDATE SET blob_sha = EXCLUDED.blob_sha,
               commit_sha = COALESCE(EXCLUDED.commit_sha, music.song_documents.commit_sha), updated_at = NOW()""",
        song_id, repo_path, _DOC_KINDS.get(ext, "other"), blob_sha, commit_sha)


async def _sync_song_header(song_id, text: str):
    """{title}/{key}/{tempo}/{artist} in the sheet are authoritative once set."""
    tempo = _directive(text, "tempo")
    try:
        tempo_v = float(tempo) if tempo else None
    except ValueError:
        tempo_v = None
    await pool().execute(
        """UPDATE music.songs SET title = COALESCE($2, title), key_signature = COALESCE($3, key_signature),
               tempo_bpm = COALESCE($4, tempo_bpm), project = COALESCE($5, project) WHERE id = $1""",
        song_id, _directive(text, "title"), _directive(text, "key"), tempo_v, _directive(text, "artist"))


# ---- Suggestions (review queue) --------------------------------------------

class SuggestionAccept(BaseModel):
    title:        Optional[str] = None
    project:      Optional[str] = None
    kind:         Optional[str] = None
    files:        Optional[List[Dict[str, Any]]] = None   # [{audio_id, role}] — subset/re-roled; default: all
    into_song_id: Optional[str] = None                    # merge into an existing song instead of creating one


async def _attach_file_details(files: List[Dict]) -> List[Dict]:
    ids = [uuid.UUID(f["audio_id"]) for f in files]
    rows = await pool().fetch(
        """SELECT f.id, f.duration_sec, f.format,
                  (SELECT l.beets_item_id FROM music.audio_locations l
                   WHERE l.audio_id = f.id AND l.missing_since IS NULL AND l.beets_item_id IS NOT NULL
                   ORDER BY l.path LIMIT 1) AS beets_item_id
           FROM music.audio_files f WHERE f.id = ANY($1::uuid[])""", ids)
    by_id = {str(r["id"]): r for r in rows}
    out = []
    for f in files:
        r = by_id.get(f["audio_id"])
        d = dict(f)
        d["name"] = f["path"].rsplit("/", 1)[-1]
        d["duration_sec"] = float(r["duration_sec"]) if r and r["duration_sec"] is not None else None
        d["play_url"] = f"{BEETS_PUBLIC_URL}/item/{r['beets_item_id']}/file" if r and r["beets_item_id"] else None
        out.append(d)
    return out


@app.get("/api/v1/suggestions/summary")
async def suggestions_summary():
    rows = await pool().fetch("SELECT status, count(*) AS n FROM music.song_suggestions GROUP BY status")
    folders = await pool().fetch(
        """SELECT folder, count(*) FILTER (WHERE status = 'pending') AS pending, count(*) AS total
           FROM music.song_suggestions WHERE status <> 'unassigned'
           GROUP BY folder HAVING count(*) FILTER (WHERE status = 'pending') > 0 ORDER BY folder""")
    return {"by_status": {r["status"]: r["n"] for r in rows}, "folders": _rows_to_list(folders)}


@app.get("/api/v1/suggestions")
async def list_suggestions(
    status: str = Query("pending", pattern="^(pending|accepted|rejected|merged|unassigned)$"),
    folder: Optional[str] = None, q: Optional[str] = None,
    limit: int = Query(25, le=200), offset: int = 0,
):
    conds, args = ["status = $1"], [status]
    if folder:
        args.append(folder); conds.append(f"folder = ${len(args)}")
    if q:
        args.append(f"%{q}%"); conds.append(f"(title ILIKE ${len(args)} OR folder ILIKE ${len(args)} OR project ILIKE ${len(args)})")
    where = " AND ".join(conds)
    total = await pool().fetchval(f"SELECT count(*) FROM music.song_suggestions WHERE {where}", *args)
    rows = await pool().fetch(
        f"""SELECT * FROM music.song_suggestions WHERE {where}
            ORDER BY folder, confidence DESC NULLS LAST, title LIMIT {limit} OFFSET {offset}""", *args)
    out = []
    for r in rows:
        d = _row_to_dict(r)
        d["files"] = await _attach_file_details(json.loads(r["files"]) if isinstance(r["files"], str) else r["files"])
        d["similar_to"] = [str(x) for x in r["similar_to"]]
        if r["similar_to"]:
            d["similar"] = _rows_to_list(await pool().fetch(
                "SELECT id, title, folder, status, song_id FROM music.song_suggestions WHERE id = ANY($1::uuid[])",
                r["similar_to"]))
        out.append(d)
    return {"total": total, "items": out}


@app.post("/api/v1/suggestions/{sugg_id}/accept")
async def accept_suggestion(sugg_id: uuid.UUID, body: SuggestionAccept):
    s = await pool().fetchrow("SELECT * FROM music.song_suggestions WHERE id = $1", sugg_id)
    if not s:
        raise HTTPException(404, "suggestion not found")
    if s["status"] not in ("pending", "unassigned"):
        raise HTTPException(409, f"suggestion already {s['status']}")
    files = json.loads(s["files"]) if isinstance(s["files"], str) else s["files"]
    if body.files is not None:
        allowed = {f["audio_id"] for f in files}
        files = [f for f in body.files if f.get("audio_id") in allowed]
    if not files:
        raise HTTPException(400, "no files selected")
    title = (body.title or s["title"]).strip()
    if not title or title == "(unassigned)":
        raise HTTPException(400, "a title is required")

    async with pool().acquire() as con, con.transaction():
        if body.into_song_id:
            song = await con.fetchrow("SELECT * FROM music.songs WHERE id = $1", uuid.UUID(body.into_song_id))
            if not song:
                raise HTTPException(404, "target song not found")
            new_status = "merged"
        else:
            project = (body.project if body.project is not None else s["project"]).strip()
            kind = body.kind or (s["kind"] if s["kind"] != "unassigned" else "unknown")
            slug = await _unique_slug(_slugify(title))
            song = await con.fetchrow(
                """INSERT INTO music.songs (title, project, kind, slug, songbook_path, is_original, status)
                   VALUES ($1,$2,$3,$4,$5,$6,'draft') RETURNING *""",
                title, project or None, kind, slug, f"songs/{slug}", kind == "original")
            new_status = "accepted"
        for f in files:
            await con.execute(
                """INSERT INTO music.song_recordings (song_id, audio_id, role, source, suggestion_id)
                   VALUES ($1,$2,$3,'suggested',$4) ON CONFLICT (song_id, audio_id) DO NOTHING""",
                song["id"], uuid.UUID(f["audio_id"]), f.get("role") or "take", sugg_id)
        if s["status"] == "unassigned":
            # Only the picked files leave the unassigned pool; the rest stay reviewable.
            picked = {f["audio_id"] for f in files}
            rest = [f for f in (json.loads(s["files"]) if isinstance(s["files"], str) else s["files"])
                    if f["audio_id"] not in picked]
            await con.execute("UPDATE music.song_suggestions SET files = $2::jsonb WHERE id = $1", sugg_id, json.dumps(rest))
        else:
            await con.execute(
                "UPDATE music.song_suggestions SET status = $2, song_id = $3, decided_at = NOW() WHERE id = $1",
                sugg_id, new_status, song["id"])

    # Skeleton song sheet for a brand-new song (outside the DB transaction:
    # a Forgejo hiccup must not undo the human's decision — the song page
    # offers "create sheet" if it's missing).
    if new_status == "accepted":
        try:
            path = f"songs/{song['slug']}/song.cho"
            res = await songbook_write(path, _skeleton_chordpro(dict(song)), f"New song: {title}", None)
            await _index_document(song["id"], path, res["sha"], res["commit"])
        except Exception as e:
            log.warning(f"songbook skeleton for {song['slug']} failed: {e}")
    _rebuild_soon(song["id"])      # phase 4: song harmony from its recordings
    return {"song_id": str(song["id"]), "slug": song["slug"], "status": new_status, "files": len(files)}


@app.post("/api/v1/suggestions/{sugg_id}/reject")
async def reject_suggestion(sugg_id: uuid.UUID):
    r = await pool().execute(
        "UPDATE music.song_suggestions SET status = 'rejected', decided_at = NOW() WHERE id = $1 AND status = 'pending'",
        sugg_id)
    if r.endswith(" 0"):
        raise HTTPException(409, "not a pending suggestion")
    return {"status": "rejected"}


# ---- Songs hub -----------------------------------------------------------------

@app.get("/api/v1/songbook/songs")
async def songbook_songs(q: Optional[str] = None, limit: int = Query(100, le=500), offset: int = 0):
    args, where = [], "WHERE s.slug IS NOT NULL"
    if q:
        args.append(f"%{q}%"); where += f" AND (s.title ILIKE $1 OR s.project ILIKE $1)"
    rows = await pool().fetch(
        f"""SELECT s.id, s.title, s.project, s.kind, s.slug, s.key_signature, s.tempo_bpm, s.status, s.created_at,
                   (SELECT count(*) FROM music.song_recordings r WHERE r.song_id = s.id) AS recordings,
                   EXISTS (SELECT 1 FROM music.song_documents d WHERE d.song_id = s.id AND d.kind = 'chordpro') AS has_sheet
            FROM music.songs s {where} ORDER BY lower(s.title) LIMIT {limit} OFFSET {offset}""", *args)
    return _rows_to_list(rows)


@app.get("/api/v1/songs/{song_id}/full")
async def song_full(song_id: uuid.UUID):
    song = await pool().fetchrow("SELECT * FROM music.songs WHERE id = $1", song_id)
    if not song:
        raise HTTPException(404, "song not found")
    recs = await pool().fetch(
        """SELECT r.audio_id, r.role, r.source, f.duration_sec, f.format, f.content_hash,
                  l.path, l.beets_item_id, l.labcat_path, a.key AS detected_key, a.tempo_bpm AS detected_tempo
           FROM music.song_recordings r
           JOIN music.audio_files f ON f.id = r.audio_id
           LEFT JOIN music.audio_analysis a ON a.audio_id = r.audio_id
           LEFT JOIN LATERAL (SELECT * FROM music.audio_locations l WHERE l.audio_id = r.audio_id
                              AND l.missing_since IS NULL ORDER BY l.path LIMIT 1) l ON true
           WHERE r.song_id = $1
           ORDER BY array_position(ARRAY['final_mix','master','mix','demo','take','sketch','stem','reference'], r.role), l.path""",
        song_id)
    recordings = []
    for r in recs:
        d = _row_to_dict(r)
        d["name"] = (r["path"] or "").rsplit("/", 1)[-1]
        d["play_url"] = f"{BEETS_PUBLIC_URL}/item/{r['beets_item_id']}/file" if r["beets_item_id"] else None
        d["duration_sec"] = float(r["duration_sec"]) if r["duration_sec"] is not None else None
        d["detected_tempo"] = float(r["detected_tempo"]) if r["detected_tempo"] is not None else None
        recordings.append(d)
    docs = _rows_to_list(await pool().fetch(
        "SELECT repo_path, kind, blob_sha, commit_sha, updated_at FROM music.song_documents WHERE song_id = $1 ORDER BY repo_path",
        song_id))
    out = _row_to_dict(song)
    out.update({"recordings": recordings, "documents": docs,
                "songbook_url": f"{SONGBOOK_PUBLIC}/{SONGBOOK_OWNER}/{SONGBOOK_REPO}/src/branch/{SONGBOOK_BRANCH}/{song['songbook_path']}"
                                if song["songbook_path"] else None})
    return out


class ChordProSave(BaseModel):
    content: str
    sha:     Optional[str] = None     # blob sha the editor loaded; None only when creating the sheet
    message: Optional[str] = None


async def _song_sheet_path(song_id: uuid.UUID) -> Tuple[Dict, str]:
    song = await pool().fetchrow("SELECT * FROM music.songs WHERE id = $1", song_id)
    if not song or not song["songbook_path"]:
        raise HTTPException(404, "song not found or not in the songbook")
    return dict(song), f"{song['songbook_path']}/song.cho"


@app.get("/api/v1/songs/{song_id}/chordpro")
async def get_chordpro(song_id: uuid.UUID):
    song, path = await _song_sheet_path(song_id)
    doc = await songbook_read(path)
    if doc is None:
        return {"path": path, "sha": None, "content": _skeleton_chordpro(song), "exists": False}
    return {**doc, "exists": True}


@app.put("/api/v1/songs/{song_id}/chordpro")
async def save_chordpro(song_id: uuid.UUID, body: ChordProSave):
    song, path = await _song_sheet_path(song_id)
    if f"song_id {song['id']}" not in body.content:
        # Keep the link git -> Postgres even if the meta line got deleted in the editor.
        body.content = body.content.rstrip("\n") + f"\n{{meta: song_id {song['id']}}}\n"
    try:
        res = await songbook_write(path, body.content, body.message or f"Edit {song['title']} (studio.home.example.org)", body.sha)
    except SongbookConflict:
        raise HTTPException(409, "The sheet changed in git since you opened it — reload, then re-apply your edit.")
    await _index_document(song["id"], path, res["sha"], res["commit"])
    await _sync_song_header(song["id"], body.content)
    _rebuild_soon(song["id"])      # phase 4: the edited sheet is now the song's harmony source
    return {"path": path, "sha": res["sha"], "commit": res["commit"]}


@app.get("/api/v1/songs/{song_id}/pdf")
async def chordpro_pdf(song_id: uuid.UUID, transpose: int = Query(0, ge=-11, le=11)):
    song, path = await _song_sheet_path(song_id)
    doc = await songbook_read(path)
    if doc is None:
        raise HTTPException(404, "no song sheet yet")
    if not shutil.which("chordpro"):
        raise HTTPException(503, "chordpro CLI not installed in this container")
    with tempfile.TemporaryDirectory() as tmp:
        src, out = os.path.join(tmp, "song.cho"), os.path.join(tmp, "song.pdf")
        open(src, "w", encoding="utf-8").write(doc["content"])
        cmd = ["chordpro", f"--transpose={transpose}", "--output", out, src]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0 or not os.path.exists(out):
            raise HTTPException(500, f"chordpro failed: {err.decode()[:300]}")
        pdf = open(out, "rb").read()
    from fastapi.responses import Response
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{song["slug"]}.pdf"'})


@app.post("/api/v1/songbook/reindex")
async def songbook_reindex():
    """Rebuild music.song_documents from the repo. Also picks up songs created
    straight in git (songs/<slug>/ with no Postgres row yet): they get a song
    row from the sheet's {title}. Run nightly and from the UI."""
    r = await _forgejo("GET", f"/git/trees/{SONGBOOK_BRANCH}", params={"recursive": "true", "per_page": 10000})
    r.raise_for_status()
    blobs = [t for t in r.json().get("tree", []) if t["type"] == "blob" and t["path"].startswith("songs/")]
    by_dir: Dict[str, List[Dict]] = {}
    for b in blobs:
        parts = b["path"].split("/")
        if len(parts) >= 3:
            by_dir.setdefault("/".join(parts[:2]), []).append(b)
    created = indexed = 0
    for d, files in by_dir.items():
        song_id = await pool().fetchval("SELECT id FROM music.songs WHERE songbook_path = $1", d)
        if not song_id:
            sheet = next((f for f in files if f["path"].endswith("/song.cho")), None)
            text = (await songbook_read(sheet["path"]))["content"] if sheet else ""
            m = _re.search(r"song_id\s+([0-9a-f-]{36})", text)
            if m:
                song_id = await pool().fetchval(
                    "UPDATE music.songs SET songbook_path = $2 WHERE id = $1::uuid RETURNING id", m.group(1), d)
            if not song_id:
                title = _directive(text, "title") or d.split("/", 1)[1].replace("-", " ").title()
                slug = await _unique_slug(_slugify(d.split("/", 1)[1]))
                song_id = await pool().fetchval(
                    "INSERT INTO music.songs (title, slug, songbook_path, status) VALUES ($1,$2,$3,'draft') RETURNING id",
                    title, slug, d)
                created += 1
            if text:
                await _sync_song_header(song_id, text)
        live = [f["path"] for f in files]
        await pool().execute("DELETE FROM music.song_documents WHERE song_id = $1 AND NOT (repo_path = ANY($2::text[]))",
                             song_id, live)
        for f in files:
            await _index_document(song_id, f["path"], f["sha"], None)
            indexed += 1
    return {"song_dirs": len(by_dir), "documents": indexed, "songs_created_from_git": created}


# ============================================================================
# Musician Platform phase 2 — recording analysis (music-analysis worker) and
# the Sonic Visualiser round trip. Export = CSV annotation layers SV imports
# (File > Import Annotation Layer; times in seconds); import = a corrected
# layer exported back from SV, stored as a 'human' run that wins over the
# machine result for that kind (music.analysis_events_effective).
# ============================================================================

import csv as _csv
import io as _io
from fastapi import Request as _Request
from fastapi.responses import PlainTextResponse

_SV_KINDS = {"chords": "chord", "sections": "section", "keys": "key"}


@app.get("/api/v1/analysis/status")
async def analysis_status():
    rows = await pool().fetch(
        "SELECT status, count(*) n FROM music.analysis_runs WHERE source = 'auto' GROUP BY status")
    recent = await pool().fetch(
        """SELECT r.audio_id, r.status, r.error, r.finished_at,
                  (SELECT l.path FROM music.audio_locations l WHERE l.audio_id = r.audio_id ORDER BY l.path LIMIT 1) path
           FROM music.analysis_runs r WHERE r.source = 'auto' AND r.status IN ('done','failed')
           ORDER BY r.finished_at DESC NULLS LAST LIMIT 10""")
    rate = await pool().fetchval(
        """SELECT count(*) FROM music.analysis_runs WHERE source = 'auto' AND status = 'done'
           AND finished_at > NOW() - interval '1 hour'""")
    return {"by_status": {r["status"]: r["n"] for r in rows}, "done_last_hour": rate, "recent": _rows_to_list(recent)}


@app.get("/api/v1/audio-files/{audio_id}/analysis")
async def audio_analysis(audio_id: uuid.UUID):
    summary = await pool().fetchrow("SELECT * FROM music.audio_analysis WHERE audio_id = $1", audio_id)
    run = await pool().fetchrow(
        """SELECT status, error, finished_at, timings FROM music.analysis_runs
           WHERE audio_id = $1 AND source = 'auto' ORDER BY created_at DESC LIMIT 1""", audio_id)
    f = await pool().fetchrow("SELECT duration_sec FROM music.audio_files WHERE id = $1", audio_id)
    if not f:
        raise HTTPException(404, "audio file not found")
    events = await pool().fetch(
        """SELECT kind, t_start, t_end, label, source FROM music.analysis_events_effective
           WHERE audio_id = $1 ORDER BY kind, t_start""", audio_id)
    by_kind: Dict[str, List] = {"chord": [], "section": [], "key": []}
    sources: Dict[str, str] = {}
    for e in events:
        by_kind[e["kind"]].append([float(e["t_start"]), float(e["t_end"]) if e["t_end"] is not None else None, e["label"]])
        sources[e["kind"]] = e["source"]
    out = {"audio_id": str(audio_id), "duration_sec": float(f["duration_sec"] or 0),
           "status": run["status"] if run else "not queued", "error": run["error"] if run else None,
           "events": by_kind, "sources": sources}
    if summary:
        s = _row_to_dict(summary)
        for k in ("key_share", "tempo_bpm", "tuning_hz"):
            s[k] = float(s[k]) if s[k] is not None else None
        out["summary"] = s
        hm = await pool().fetchrow("SELECT meter, meter_confidence FROM music.audio_harmony WHERE audio_id = $1", audio_id)
        s["meter"] = hm["meter"] if hm else None
        s["meter_confidence"] = float(hm["meter_confidence"]) if hm and hm["meter_confidence"] is not None else None
    return out


@app.get("/api/v1/audio-files/{audio_id}/analysis/{kind}.csv", response_class=PlainTextResponse)
async def analysis_csv(audio_id: uuid.UUID, kind: str):
    """Sonic Visualiser import: chords/keys = time,label (time instants);
    sections = time,duration,label (regions); beats = time,label (beat no.)."""
    buf = _io.StringIO(); w = _csv.writer(buf)
    if kind == "beats":
        s = await pool().fetchrow("SELECT beats, beat_numbers FROM music.audio_analysis WHERE audio_id = $1", audio_id)
        for t, n in zip((s["beats"] or []) if s else [], (s["beat_numbers"] or []) if s else []):
            w.writerow([f"{t:.3f}", n])
    elif kind in _SV_KINDS:
        rows = await pool().fetch(
            "SELECT t_start, t_end, label FROM music.analysis_events_effective WHERE audio_id = $1 AND kind = $2 ORDER BY t_start",
            audio_id, _SV_KINDS[kind])
        for r in rows:
            if kind == "sections":
                w.writerow([f"{r['t_start']:.3f}", f"{(r['t_end'] or r['t_start']) - r['t_start']:.3f}", r["label"]])
            else:
                w.writerow([f"{r['t_start']:.3f}", r["label"]])
    else:
        raise HTTPException(404, "kind must be chords, sections, keys or beats")
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{audio_id}-{kind}.csv"'})


def _num(x: str) -> Optional[float]:
    try:
        return float(x)
    except ValueError:
        return None


@app.post("/api/v1/audio-files/{audio_id}/analysis/{kind}")
async def analysis_import(audio_id: uuid.UUID, kind: str, request: _Request):
    """Import a layer corrected in Sonic Visualiser (File > Export Annotation
    Layer, CSV, times in seconds). Lenient about SV's column layouts:
    time,label · time,value,label · time,duration,label · time,value,duration,label."""
    if kind not in _SV_KINDS:
        raise HTTPException(400, "kind must be chords, sections or keys")
    f = await pool().fetchrow("SELECT duration_sec FROM music.audio_files WHERE id = $1", audio_id)
    if not f:
        raise HTTPException(404, "audio file not found")
    duration = float(f["duration_sec"] or 0)
    text = (await request.body()).decode("utf-8", errors="replace")
    parsed = []
    for cols in _csv.reader(_io.StringIO(text)):
        cols = [c.strip() for c in cols if c.strip() != ""]
        if not cols or _num(cols[0]) is None:
            continue                                   # header / blank lines
        nums = [_num(c) for c in cols[1:] if _num(c) is not None]
        texts = [c for c in cols[1:] if _num(c) is None]
        label = texts[-1] if texts else (f"{nums[-1]:g}" if nums else "?")
        dur = (nums[1] if len(nums) >= 2 else nums[0] if nums else None) if kind == "sections" else None
        parsed.append([float(cols[0]), dur, label])
    if not parsed:
        raise HTTPException(400, "no rows with a numeric time in the first column")
    if max(p[0] for p in parsed) > max(duration, 1) * 1.05:
        raise HTTPException(400, "times run past the end of the recording — export from SV with times in seconds, not frames")
    parsed.sort(key=lambda p: p[0])
    events = []
    for i, (t, dur, label) in enumerate(parsed):
        end = t + dur if dur is not None else (parsed[i + 1][0] if i + 1 < len(parsed) else duration)
        events.append((round(t, 3), round(end, 3), label))
    async with pool().acquire() as con, con.transaction():
        run_id = await con.fetchval(
            """INSERT INTO music.analysis_runs (audio_id, source, version, status, started_at, finished_at, worker)
               VALUES ($1,'human',$2,'done',NOW(),NOW(),'sonic-visualiser') RETURNING id""", audio_id, f"sv:{kind}")
        await con.executemany(
            "INSERT INTO music.analysis_events (run_id, audio_id, kind, t_start, t_end, label) VALUES ($1,$2,$3,$4,$5,$6)",
            [(run_id, audio_id, _SV_KINDS[kind], a, b, l) for a, b, l in events])
    return {"run_id": str(run_id), "kind": kind, "events": len(events)}


# ============================================================================
# Musician Platform phase 3 — draft lead sheets (music.lead_sheet_drafts),
# worked by the music-analysis container: Demucs vocals + Whisper on gpu-02,
# aligned with the recording's chords/sections. Drafts are NOT written to the
# songbook — the song page loads one into the editor and the human's save
# is the commit.
# ============================================================================

class DraftRequest(BaseModel):
    audio_id: Optional[str] = None     # default: the song's best recording (final_mix > master > mix > demo > take)


@app.post("/api/v1/songs/{song_id}/drafts", status_code=201)
async def request_draft(song_id: uuid.UUID, body: DraftRequest):
    if body.audio_id:
        audio_id = await pool().fetchval(
            "SELECT audio_id FROM music.song_recordings WHERE song_id = $1 AND audio_id = $2", song_id, uuid.UUID(body.audio_id))
    else:
        audio_id = await pool().fetchval(
            """SELECT audio_id FROM music.song_recordings WHERE song_id = $1
               ORDER BY array_position(ARRAY['final_mix','master','mix','demo','take','sketch','reference','stem'], role)
               LIMIT 1""", song_id)
    if not audio_id:
        raise HTTPException(400, "pick a recording that belongs to this song")
    busy = await pool().fetchval(
        "SELECT id FROM music.lead_sheet_drafts WHERE song_id = $1 AND audio_id = $2 AND status IN ('queued','running')",
        song_id, audio_id)
    if busy:
        raise HTTPException(409, "a draft for this recording is already being made")
    row = await pool().fetchrow(
        "INSERT INTO music.lead_sheet_drafts (song_id, audio_id) VALUES ($1,$2) RETURNING id, status, created_at",
        song_id, audio_id)
    return _row_to_dict(row)


@app.get("/api/v1/songs/{song_id}/drafts")
async def list_drafts(song_id: uuid.UUID):
    rows = await pool().fetch(
        """SELECT d.id, d.audio_id, d.status, d.stage, d.voiced_share, d.language, d.chordpro, d.error, d.timings,
                  d.created_at, d.finished_at,
                  (SELECT l.path FROM music.audio_locations l WHERE l.audio_id = d.audio_id ORDER BY l.path LIMIT 1) AS path,
                  (SELECT count(*) FROM music.lead_sheet_drafts q WHERE q.status = 'queued' AND q.created_at < d.created_at) AS ahead
           FROM music.lead_sheet_drafts d WHERE d.song_id = $1 ORDER BY d.created_at DESC LIMIT 10""", song_id)
    out = []
    for r in rows:
        d = _row_to_dict(r)
        d["name"] = (r["path"] or "").rsplit("/", 1)[-1]
        d["voiced_share"] = float(r["voiced_share"]) if r["voiced_share"] is not None else None
        out.append(d)
    return out


# ============================================================================
# Musician Platform phase 4 — harmony + meter catalogue around SONGS
# (harmony.py does the music; this is the I/O). A song's harmony comes from
# its song.cho when that has chords (human-corrected = authoritative), else
# from its best recording. The progression catalogue counts songs first.
# ============================================================================

import harmony as H

_ROLE_ORDER = ['final_mix', 'master', 'mix', 'demo', 'take', 'sketch', 'reference', 'stem']
_harmony_lock = asyncio.Lock()


async def _recording_harmony_backfill(only_audio: Optional[List[uuid.UUID]] = None) -> int:
    """(Re)compute audio_harmony where missing, outdated, or older than its analysis."""
    args = [H.HARMONY_VERSION]
    extra = ""
    if only_audio:
        args.append(only_audio); extra = " AND a.audio_id = ANY($2::uuid[])"
    todo = await pool().fetch(
        f"""SELECT a.audio_id, a.beats, a.key FROM music.audio_analysis a
            LEFT JOIN music.audio_harmony h ON h.audio_id = a.audio_id
            WHERE (h.audio_id IS NULL OR h.version <> $1 OR h.computed_at < a.analyzed_at){extra}""", *args)
    if not todo:
        return 0
    ids = [r["audio_id"] for r in todo]
    ev = await pool().fetch(
        """SELECT audio_id, kind, t_start, t_end, label FROM music.analysis_events_effective
           WHERE audio_id = ANY($1::uuid[]) AND kind IN ('chord','key') ORDER BY audio_id, kind, t_start""", ids)
    by: Dict[Any, Dict[str, list]] = {}
    for e in ev:
        by.setdefault(e["audio_id"], {"chord": [], "key": []})[e["kind"]].append(
            (float(e["t_start"]), float(e["t_end"]) if e["t_end"] is not None else None, e["label"]))
    rows = []
    for r in todo:
        d = by.get(r["audio_id"], {"chord": [], "key": []})
        h = await asyncio.to_thread(H.analyse_recording, d["chord"], d["key"], list(r["beats"] or []), r["key"])
        rows.append((r["audio_id"], h["meter"], h["meter_confidence"], h["downbeat_phase"], h["lift3"], h["lift4"],
                     json.dumps(h["chords"]), json.dumps(h["loops"]), H.HARMONY_VERSION))
    await pool().executemany(
        """INSERT INTO music.audio_harmony (audio_id, meter, meter_confidence, downbeat_phase, lift3, lift4, chords, loops, version, computed_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9,NOW())
           ON CONFLICT (audio_id) DO UPDATE SET meter=EXCLUDED.meter, meter_confidence=EXCLUDED.meter_confidence,
               downbeat_phase=EXCLUDED.downbeat_phase, lift3=EXCLUDED.lift3, lift4=EXCLUDED.lift4, chords=EXCLUDED.chords,
               loops=EXCLUDED.loops, version=EXCLUDED.version, computed_at=NOW()""", rows)
    return len(rows)


async def _song_harmony(song_id) -> Optional[str]:
    """Compute one song's harmony; returns its source ('sheet'|'recording') or None."""
    song = await pool().fetchrow("SELECT id, songbook_path FROM music.songs WHERE id = $1", song_id)
    if not song:
        return None
    best = await pool().fetchrow(
        """SELECT r.audio_id, a.key AS akey, h.meter, h.chords, h.loops
           FROM music.song_recordings r JOIN music.audio_harmony h ON h.audio_id = r.audio_id
           LEFT JOIN music.audio_analysis a ON a.audio_id = r.audio_id
           WHERE r.song_id = $1 ORDER BY array_position($2::text[], r.role), r.added_at LIMIT 1""",
        song_id, _ROLE_ORDER)
    rec_key = H.parse_key(best["akey"]) if best and best["akey"] else None
    sheet = None
    if song["songbook_path"] and FORGEJO_TOKEN:
        try:
            doc = await songbook_read(f"{song['songbook_path']}/song.cho")
            sheet = H.parse_chordpro(doc["content"]) if doc else None
        except Exception as e:
            log.warning(f"harmony: sheet read failed for {song_id}: {e}")
    if sheet and sheet["sections"]:
        key, sections = H.song_sections_from_sheet(sheet, rec_key)
        if key:
            loops = sorted({H.pattern_str(l) for s in sections for l in s["loops"]})
            meter = sheet["time"] or (best["meter"] if best else None)
            await _upsert_song_harmony(song_id, "sheet", best["audio_id"] if best else None, H.key_short(key), meter,
                                       "sheet" if sheet["time"] else ("detected" if meter else None), sections, loops)
            return "sheet"
    if not best:
        await pool().execute("DELETE FROM music.song_harmony WHERE song_id = $1", song_id)
        return None
    chords = json.loads(best["chords"]) if isinstance(best["chords"], str) else best["chords"]
    rloops = json.loads(best["loops"]) if isinstance(best["loops"], str) else best["loops"]
    secs = await pool().fetch(
        "SELECT t_start, t_end, label FROM music.analysis_events_effective WHERE audio_id = $1 AND kind = 'section' ORDER BY t_start",
        best["audio_id"])
    bounds = [(float(s["t_start"]), float(s["t_end"]) if s["t_end"] is not None else 1e9, s["label"]) for s in secs] or [(0.0, 1e9, "")]
    sections = []
    for st, en, label in bounds:
        inside = [c for c in chords if st <= c[0] < en]
        sections.append({"label": label, "t_start": st, "chords": [c[2] for c in inside], "roman": [c[4] for c in inside],
                         "loops": [l[2] for l in rloops if st <= l[0] < en]})
    loops = sorted({H.pattern_str(l[2]) for l in rloops})
    await _upsert_song_harmony(song_id, "recording", best["audio_id"], H.key_short(rec_key) if rec_key else None,
                               best["meter"], "detected" if best["meter"] else None, sections, loops)
    return "recording"


async def _upsert_song_harmony(song_id, source, audio_id, key, meter, meter_source, sections, loops):
    await pool().execute(
        """INSERT INTO music.song_harmony (song_id, source, audio_id, key, meter, meter_source, sections, loops, computed_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,NOW())
           ON CONFLICT (song_id) DO UPDATE SET source=EXCLUDED.source, audio_id=EXCLUDED.audio_id, key=EXCLUDED.key,
               meter=EXCLUDED.meter, meter_source=EXCLUDED.meter_source, sections=EXCLUDED.sections, loops=EXCLUDED.loops,
               computed_at=NOW()""",
        song_id, source, audio_id, key, meter, meter_source, json.dumps(sections), loops)


async def _rebuild_catalogue():
    """progressions + progression_hits from scratch (small: ~10^3 patterns, ~10^4 hits)."""
    recs = await pool().fetch(
        """SELECT h.audio_id, h.meter, h.loops,
                  (SELECT r.song_id FROM music.song_recordings r WHERE r.audio_id = h.audio_id LIMIT 1) AS song_id
           FROM music.audio_harmony h""")
    songs = await pool().fetch("SELECT song_id, key, meter, sections, loops, source FROM music.song_harmony")
    stats: Dict[str, Dict[str, Any]] = {}
    hits = []
    def st(p, unit):
        return stats.setdefault(p, {"mode": H.pattern_mode(unit), "length": len(unit), "songs": set(), "recordings": set(),
                                    "occ": 0, "meters": {}})
    for s in songs:
        secs = json.loads(s["sections"]) if isinstance(s["sections"], str) else s["sections"]
        for sec in secs:
            for unit in sec.get("loops", []):
                p = H.pattern_str(unit); x = st(p, unit)
                x["songs"].add(s["song_id"])
                if s["meter"]:
                    x["meters"][s["meter"]] = x["meters"].get(s["meter"], 0) + 1
                hits.append((p, None, s["song_id"], sec.get("t_start"), None, None, s["key"],
                             " ".join(sec.get("chords", [])[:16]), sec.get("label") or ""))
    for r in recs:
        for l in (json.loads(r["loops"]) if isinstance(r["loops"], str) else r["loops"]):
            p = H.pattern_str(l[2]); x = st(p, l[2])
            x["recordings"].add(r["audio_id"]); x["occ"] += 1
            if r["song_id"]:
                x["songs"].add(r["song_id"])
            if r["meter"] and not r["song_id"]:
                x["meters"][r["meter"]] = x["meters"].get(r["meter"], 0) + 1
            hits.append((p, r["audio_id"], r["song_id"], l[0], l[1], l[3], l[4], " ".join(l[5]), None))
    async with pool().acquire() as con, con.transaction():
        await con.execute("DELETE FROM music.progression_hits")
        await con.execute("DELETE FROM music.progressions")
        await con.executemany(
            "INSERT INTO music.progressions (pattern, mode, length, songs, recordings, occurrences, meters, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,NOW())",
            [(p, x["mode"], x["length"], len(x["songs"]), len(x["recordings"]), x["occ"], json.dumps(x["meters"]))
             for p, x in stats.items()])
        await con.copy_records_to_table(
            "progression_hits", schema_name="music",
            columns=["pattern", "audio_id", "song_id", "t_start", "t_end", "repeats", "key", "chords", "section"],
            records=hits)
    return len(stats), len(hits)


@app.post("/api/v1/harmony/rebuild")
async def harmony_rebuild(song_id: Optional[uuid.UUID] = None):
    """Nightly (cron), after a sheet save / accepted suggestion (one song), or from the UI."""
    async with _harmony_lock:
        t0 = time.time()
        recs = await _recording_harmony_backfill()
        ids = [song_id] if song_id else [r["id"] for r in await pool().fetch("SELECT id FROM music.songs WHERE slug IS NOT NULL")]
        sources: Dict[str, int] = {}
        for sid in ids:
            src = await _song_harmony(sid)
            sources[src or "none"] = sources.get(src or "none", 0) + 1
        patterns, hits = await _rebuild_catalogue()
    return {"recordings_computed": recs, "songs": sources, "patterns": patterns, "hits": hits, "seconds": round(time.time() - t0, 1)}


def _rebuild_soon(song_id=None):
    async def run():
        try:
            await harmony_rebuild(song_id)
        except Exception as e:
            log.warning(f"harmony rebuild failed: {e}")
    asyncio.get_running_loop().create_task(run())


@app.get("/api/v1/progressions")
async def list_progressions(mode: Optional[str] = Query(None, pattern="^(major|minor)$"), length: Optional[int] = None,
                            meter: Optional[str] = None, contains: Optional[str] = None,
                            limit: int = Query(100, le=500), offset: int = 0):
    conds, args = ["length >= 3 OR occurrences >= 4"], []
    if mode:
        args.append(mode); conds.append(f"mode = ${len(args)}")
    if length:
        args.append(length); conds.append(f"length = ${len(args)}")
    if meter:
        args.append(meter); conds.append(f"meters ? ${len(args)}")
    if contains:
        args.append(f"%{contains.strip()}%"); conds.append(f"pattern ILIKE ${len(args)}")
    where = " AND ".join(f"({c})" for c in conds)
    total = await pool().fetchval(f"SELECT count(*) FROM music.progressions WHERE {where}", *args)
    rows = await pool().fetch(
        f"""SELECT * FROM music.progressions WHERE {where}
            ORDER BY songs DESC, recordings DESC, occurrences DESC LIMIT {limit} OFFSET {offset}""", *args)
    out = []
    for r in rows:
        d = _row_to_dict(r)
        unit = r["pattern"].split("–")
        d["example"] = " ".join(H.realise(x, (0, "major") if r["mode"] == "major" else (9, "minor")) for x in unit)
        d["meters"] = json.loads(r["meters"]) if isinstance(r["meters"], str) else r["meters"]
        out.append(d)
    return {"total": total, "items": out,
            "songs_with_harmony": await pool().fetchval("SELECT count(*) FROM music.song_harmony")}


@app.get("/api/v1/progression")
async def progression_detail(pattern: str, key: Optional[str] = None):
    p = await pool().fetchrow("SELECT * FROM music.progressions WHERE pattern = $1", pattern)
    if not p:
        raise HTTPException(404, "progression not in the catalogue")
    unit = pattern.split("–")
    k = H.key_from_sheet(key) if key else ((0, "major") if p["mode"] == "major" else (9, "minor"))
    hits = await pool().fetch(
        """SELECT h.*, s.title AS song_title, s.slug,
                  (SELECT l.beets_item_id FROM music.audio_locations l WHERE l.audio_id = h.audio_id
                     AND l.missing_since IS NULL AND l.beets_item_id IS NOT NULL ORDER BY l.path LIMIT 1) AS beets_item_id,
                  (SELECT l.path FROM music.audio_locations l WHERE l.audio_id = h.audio_id ORDER BY l.path LIMIT 1) AS path
           FROM music.progression_hits h LEFT JOIN music.songs s ON s.id = h.song_id
           WHERE h.pattern = $1 ORDER BY (h.song_id IS NULL), s.title, h.repeats DESC NULLS LAST LIMIT 300""", pattern)
    songs, takes = {}, []
    for h in hits:
        d = _row_to_dict(h)
        for f in ("t_start", "t_end"):
            d[f] = float(d[f]) if d[f] is not None else None
        d["name"] = (h["path"] or "").rsplit("/", 1)[-1]
        if h["song_id"]:
            songs.setdefault(str(h["song_id"]), {"song_id": str(h["song_id"]), "title": h["song_title"], "hits": []})["hits"].append(d)
        else:
            takes.append(d)
    out = _row_to_dict(p)
    out.update({"in_key": H.key_short(k), "chords_in_key": [H.realise(x, k) for x in unit],
                "meters": json.loads(p["meters"]) if isinstance(p["meters"], str) else p["meters"],
                "songs": list(songs.values()), "takes": takes[:200]})
    return out


@app.get("/api/v1/harmony/search")
async def harmony_search(q: str, limit: int = Query(100, le=300)):
    """'ii V I' (numerals) or 'Am F C G' (chord names — matched in ANY key)."""
    kind, query = H.parse_query(q)
    if not kind or len(query) < 2:
        raise HTTPException(400, "use Roman numerals (ii V I) or chord names (Am F C G), at least two")
    rows = await pool().fetch(
        """SELECT h.audio_id, h.chords, h.meter,
                  (SELECT r.song_id FROM music.song_recordings r WHERE r.audio_id = h.audio_id LIMIT 1) AS song_id,
                  (SELECT l.beets_item_id FROM music.audio_locations l WHERE l.audio_id = h.audio_id
                     AND l.missing_since IS NULL AND l.beets_item_id IS NOT NULL ORDER BY l.path LIMIT 1) AS beets_item_id,
                  (SELECT l.path FROM music.audio_locations l WHERE l.audio_id = h.audio_id ORDER BY l.path LIMIT 1) AS path
           FROM music.audio_harmony h""")
    def scan():
        res = []
        for r in rows:
            ch = json.loads(r["chords"]) if isinstance(r["chords"], str) else r["chords"]
            for t0, t1, played, key in H.find_in_sequence(ch, kind, query):
                res.append({"audio_id": str(r["audio_id"]), "song_id": str(r["song_id"]) if r["song_id"] else None,
                            "beets_item_id": r["beets_item_id"], "name": (r["path"] or "").rsplit("/", 1)[-1],
                            "path": r["path"], "t_start": t0, "t_end": t1, "chords": played, "key": key, "meter": r["meter"]})
        return res
    res = await asyncio.to_thread(scan)
    titles = {str(r["id"]): r["title"] for r in await pool().fetch(
        "SELECT id, title FROM music.songs WHERE id = ANY($1::uuid[])", list({uuid.UUID(x["song_id"]) for x in res if x["song_id"]}))}
    for x in res:
        x["song_title"] = titles.get(x["song_id"])
    res.sort(key=lambda x: (x["song_id"] is None, x["song_title"] or "", x["name"]))
    return {"query": q, "kind": kind, "recordings": len({x["audio_id"] for x in res}), "total": len(res), "hits": res[:limit]}


@app.get("/api/v1/songs/{song_id}/harmony")
async def song_harmony(song_id: uuid.UUID):
    r = await pool().fetchrow("SELECT * FROM music.song_harmony WHERE song_id = $1", song_id)
    if not r:
        return {"song_id": str(song_id), "source": None}
    d = _row_to_dict(r)
    d["sections"] = json.loads(r["sections"]) if isinstance(r["sections"], str) else r["sections"]
    return d


class NewSongFromProgression(BaseModel):
    pattern: str
    title:   str
    key:     str                      # e.g. 'Am', 'G', 'F#m'
    time:    str = "4/4"
    tempo:   Optional[int] = None


@app.post("/api/v1/progressions/new-song", status_code=201)
async def new_song_from_progression(body: NewSongFromProgression):
    unit = body.pattern.split("–")
    k = H.key_from_sheet(body.key)
    if not k or not body.title.strip():
        raise HTTPException(400, "title and a key like 'Am' or 'G' are required")
    if body.time not in ("4/4", "3/4", "6/8", "2/4", "5/4", "7/8", "12/8"):
        raise HTTPException(400, "unsupported time signature")
    chords = [H.realise(x, k) for x in unit]
    slug = await _unique_slug(_slugify(body.title))
    song = await pool().fetchrow(
        """INSERT INTO music.songs (title, kind, slug, songbook_path, is_original, status, key_signature, tempo_bpm, time_signature)
           VALUES ($1,'original',$2,$3,true,'draft',$4,$5,$6) RETURNING *""",
        body.title.strip(), slug, f"songs/{slug}", H.key_short(k), body.tempo, body.time)
    bar = " ".join(f"[{c}]" for c in chords)
    text = "\n".join(
        [f"{{title: {body.title.strip()}}}", f"{{key: {H.key_short(k)}}}", f"{{time: {body.time}}}"]
        + ([f"{{tempo: {body.tempo}}}"] if body.tempo else [])
        + [f"{{meta: song_id {song['id']}}}", f"{{comment: Started from the progression {body.pattern} ({' '.join(chords)})}}", "",
           "{start_of_verse: Verse 1}", f"{bar}", f"{bar}", "{end_of_verse}", "",
           "{start_of_chorus}", f"{bar}", "{end_of_chorus}", ""])
    res = await songbook_write(f"songs/{slug}/song.cho", text, f"New song from progression: {body.title.strip()}", None)
    await _index_document(song["id"], f"songs/{slug}/song.cho", res["sha"], res["commit"])
    _rebuild_soon(song["id"])
    return {"song_id": str(song["id"]), "slug": slug, "chords": chords}


# ============================================================================
# Unified search — Immich-style: one query across everything Music Studio
# knows about, whatever format it lives in (Postgres sessions/songs/
# references/library rows, or Beets-cataloged audio files).
# ============================================================================

@app.get("/api/v1/search")
async def search(
    q:      str,
    entity: Optional[str] = None,   # session | song | reference | library
    limit:  int = Query(20, le=100),
):
    """Full-text search across sessions, songs, references and library."""
    args  = [f"%{q}%"]
    where = "(title ILIKE $1 OR artist ILIKE $1 OR mood_str ILIKE $1 OR genre_str ILIKE $1)"
    if entity:
        args.append(entity)
        where += f" AND entity_type = ${len(args)}"
    rows = await pool().fetch(
        f"SELECT * FROM music.search_all WHERE {where} ORDER BY created_at DESC LIMIT {limit}",
        *args,
    )
    return _rows_to_list(rows)


@app.get("/api/v1/search/all")
async def search_all_entities(q: str, user_id: str = Query("owner"), limit: int = Query(30, le=100)):
    """Combines /api/v1/search (Postgres: sessions/songs/references/library)
    with /api/v1/tracks/search (Beets-cataloged audio) into one result set,
    each item tagged with its own entity_type so the UI can render/link
    them differently. This is the endpoint the browse UI actually calls."""
    pg_results = await search(q=q, limit=limit)
    for r in pg_results:
        r["source"] = "catalog"
    try:
        track_results = await search_tracks(q=q, user_id=user_id, limit=limit)
    except Exception:
        track_results = []
    for r in track_results:
        r["entity_type"] = "beets_track"
        r["source"] = "beets"
        r["id"] = r["item_id"]
    combined = pg_results + track_results
    return {"query": q, "count": len(combined), "results": combined}


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("music_catalog_api:app", host="0.0.0.0", port=8200, reload=True)
