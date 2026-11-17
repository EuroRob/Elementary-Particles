"""studio_partner.py — DOMO in the studio (2026-10-24).

owner asked for DOMO as a partner while he makes music: help with concepts,
chords and arrangement, a creative journal kept while working, and a record of
the whole process without notes on paper -- all local, tied to Music Studio and
to where the files are on DATAGRID. Household decisions (2026-10-24,
AskUserQuestion): sets are saved on DATAGRID (My Recordings); Live 12 Suite on
the Intel MacBook; DOMO reads and suggests, owner makes every change himself; the
journal is the studio chronicle in Music Studio's database (not his private
diary).

  chronicle  Every .als under My Recordings is watched (full walk every 15 min for new sets,
             every known set's file date every 20 s). Each save becomes a
             snapshot (als_reader.summarise) and a line of what changed
             ("Added midi track 'Bass' (Operator); Tempo 120 → 96 BPM"). On
             first sight of a project, Live's own Backup/ copies are read too,
             so its history starts before DOMO did. Projects are music.sessions
             rows; their audio is linked to music.audio_locations (the catalogue
             of every recording and where it is).
  notes      "studio note: ...", "idea: ...", "todo: ..." -> filed under the set
             being worked on (saved in the last 3 hours, or the open session's).
  session    "studio session" opens a conversation on gpu01-primary (gpu-01):
             the open set's facts (tracks, devices, loops, locators, recent
             changes, notes), chords computed by music_theory.py for its key (or
             a key he names), his own most-played progressions, the instrument
             palette. DOMO suggests; it cannot change the set. Kept in the
             chronicle with a summary when it ends.
  practice   Ear training and keyboard drills (practice.py) for the page at
             /ui/practice: next exercise (missed items come back more), grading,
             music.practice_attempts, progress; /practice/summary for wind-down.

No egress: DATAGRID over NFS, pg-01, gpu-01.
"""
from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

import als_reader as A
import music_theory as T
import practice as PR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("studio-partner")
TZ = ZoneInfo("Europe/Berlin")

DB_DSN = os.environ.get("DATABASE_URL", "")
API_PORT = int(os.environ.get("API_PORT", "8202"))
ROOT = os.environ.get("STUDIO_ROOT", "/import/nasty")
# How the container path reads on the NAS, for "where are the files".
DATAGRID_PATH = os.environ.get("STUDIO_DATAGRID_PATH", "/DATAGRID/Multimedia/Music/My Recordings")
SKIP_DIRS = {"Backup", "Factory Packs", "User Library", "Lessons", "Clips", "Presets", "Defaults",
             "Ableton Project Info", "Samples", ".stversions", "@eaDir", ".AppleDouble"}
WALK_EVERY_MIN = int(os.environ.get("WALK_EVERY_MIN", "15"))
HOT_EVERY_S = int(os.environ.get("HOT_EVERY_S", "20"))
ACTIVE_HOURS = 3
PEOPLE: list[str] = json.loads(os.environ.get("STUDIO_PEOPLE_JSON", '["owner"]'))
NAMES = {"owner": "owner", "partner": "Partner"}
LLM_URL = os.environ.get("OLLAMA_URL", "http://10.10.30.50:11434").rstrip("/")
LLM_MODEL = os.environ.get("STUDIO_LLM_MODEL", "gpu01-primary")
LLM_NUM_CTX = int(os.environ.get("STUDIO_LLM_NUM_CTX", "16384"))     # = the resident copy; another value reloads it
TALK_IDLE_MIN = int(os.environ.get("TALK_IDLE_MIN", "30"))
# Live 12 Suite's own instruments (so suggestions name what's actually there).
LIVE_INSTRUMENTS = ["Drift", "Meld", "Wavetable", "Operator", "Analog", "Electric", "Tension", "Collision",
                    "Simpler", "Sampler", "Drum Rack", "Drum Sampler", "Granulator III"]
# Only an arrangement question gets the bar plan (FOUND: "chorus" alone put it on every turn).
_ARRANGE = re.compile(r"\b(arrang\w*|structure|song form|how long|minutes?|minuten|länge|length of the song)\b", re.I)
_NOTE = re.compile(r"^\W*(?:(?:studio |music |song )?(note|idea|todo|to do|idee|notiz)|note (?:for the )?studio|merk dir)"
                   r"\s*[:,]\s*(.+)$", re.I | re.S)
_TALK_END = re.compile(r"^\W*(?:end (?:the )?studio(?: session)?|stop (?:the )?studio(?: session)?|studio off|"
                       r"that'?s it for (?:the studio|today|now)|done in the studio|studio aus|ende studio)\W*$", re.I)

_pool: Optional[asyncpg.Pool] = None
_status: dict[str, Any] = {}
_lock = asyncio.Lock()
_seen: dict[str, int] = {}          # set path -> mtime_ns already ingested (saves a DB query every 20 s)


# ── storage ──────────────────────────────────────────────────────────────────

async def pool() -> asyncpg.Pool:
    """Tables: music.studio_* in music_schema.sql (applied by deploy_gpu.yml as owner;
    the app user has no CREATE on schema music)."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=4)
    return _pool


def nas_path(p: str) -> str:
    return DATAGRID_PATH + p[len(ROOT):] if p.startswith(ROOT) else p


# ── the chronicle: watching saves ────────────────────────────────────────────

def find_sets(root: str) -> list[str]:
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
        out += [os.path.join(d, f) for f in files if f.endswith(".als") and not f.startswith("._")]
    return sorted(out)


def backups_of(als: str) -> list[str]:
    """Live's own copies: <project>/Backup/<name> [YYYY-MM-DD HHMMSS].als."""
    b = Path(als).parent / "Backup"
    stem = Path(als).stem
    try:
        return sorted(glob.glob(os.path.join(glob.escape(str(b)), glob.escape(stem) + " [[]*].als")),
                      key=lambda p: os.stat(p).st_mtime_ns)
    except OSError:
        return []


async def project_for(als: str, summary: dict) -> str:
    """The music.sessions row for a set (created on first sight). the owner's own edits
    (title, status, notes) are never overwritten; tempo/key follow the set."""
    p = await pool()
    sid = await p.fetchval("SELECT id FROM music.sessions WHERE project_file=$1", als)
    tempo = summary.get("tempo")
    if sid is None:
        recent = datetime.now(timezone.utc) - datetime.fromtimestamp(os.stat(als).st_mtime, timezone.utc) < timedelta(days=60)
        sid = await p.fetchval(
            "INSERT INTO music.sessions (title, project_file, session_dir, daw, status, tempo_bpm, key_signature, "
            "time_signature, recorded_at, location) VALUES ($1,$2,$3,'ableton',$4,$5,$6,$7,$8,'home-studio') RETURNING id",
            Path(als).stem, als, str(Path(als).parent), "in_progress" if recent else "archived",
            tempo, summary.get("scale"), summary.get("time_signature") or "4/4",
            datetime.fromtimestamp(os.stat(als).st_mtime, timezone.utc))
    else:
        await p.execute("UPDATE music.sessions SET tempo_bpm=COALESCE($2, tempo_bpm), "
                        "key_signature=COALESCE($3, key_signature), updated_at=now() WHERE id=$1",
                        sid, tempo, summary.get("scale"))
    return sid


async def ingest(als: str, source: str, owner: Optional[str] = None) -> Optional[str]:
    """One file -> one snapshot + one 'save' event. `owner` = the current set's path
    when reading its backups. Returns the snapshot id, or None if already known."""
    st = await asyncio.to_thread(os.stat, als)
    if _seen.get(als) == st.st_mtime_ns:
        return None
    p = await pool()
    key = owner or als
    if await p.fetchval("SELECT 1 FROM music.studio_snapshots WHERE als_path=$1 AND mtime_ns=$2", als, st.st_mtime_ns):
        return None
    raw = await asyncio.to_thread(Path(als).read_bytes)
    try:
        summary = A.read_set(raw)
    except Exception as e:  # noqa: BLE001 -- a half-written save: try again next round
        log.warning("could not read %s: %s", als, e)
        return None
    sid = await project_for(key, summary)
    saved = datetime.fromtimestamp(st.st_mtime, timezone.utc)
    prev = await p.fetchrow("SELECT summary FROM music.studio_snapshots WHERE session_id=$1 AND saved_at < $2 "
                            "ORDER BY saved_at DESC LIMIT 1", sid, saved)
    lines = A.diff(json.loads(prev["summary"]) if prev else {}, summary)
    snap = await p.fetchval(
        "INSERT INTO music.studio_snapshots (session_id, als_path, mtime_ns, size_bytes, saved_at, source, summary) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING RETURNING id",
        sid, als, st.st_mtime_ns, st.st_size, saved, source, json.dumps(summary))
    _seen[als] = st.st_mtime_ns
    if snap is None:
        return None
    await p.execute("INSERT INTO music.studio_events (session_id, at, kind, text, detail, snapshot_id) "
                    "VALUES ($1,$2,'save',$3,$4,$5)", sid, saved,
                    ("" if not prev else "Saved (Live backup): " if source == "backup" else "Saved: ") + "; ".join(lines),
                    json.dumps(lines), snap)
    if source == "save":
        await link_files(sid, key, summary)
    return snap


AUDIO_EXT = (".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a", ".ogg", ".alc", ".mid", ".midi")


def ref_inside(ref: dict) -> bool:
    """A set's own audio is inside its project when Live stores it project-relative.
    "../.." or an empty relative path with an absolute one = a file somewhere else."""
    rel = ref.get("relative") or ""
    return bool(rel) and not rel.startswith(("../", "/")) and "/../" not in rel


def outside_refs(summary: dict) -> list[str]:
    """Own (non-library) files the set uses that live outside its project folder."""
    return [s.get("absolute") or s.get("relative") or s.get("name") for s in summary.get("samples", [])
            if not s.get("pack") and not ref_inside(s)]


def project_files_on_disk(als: str) -> list[str]:
    """Audio/MIDI inside a '<name> Project' folder, catalogued or not (new takes aren't yet)."""
    folder = Path(als).parent
    if not folder.name.endswith(" Project"):
        return []
    out = []
    for d, dirs, files in os.walk(folder):
        dirs[:] = [x for x in dirs if x not in ("Backup", ".stversions") and not x.startswith(".")]
        out += [os.path.join(d, f) for f in files if f.lower().endswith(AUDIO_EXT) and not f.startswith("._")]
    return sorted(out)


def own_sample_paths(als: str, summary: dict) -> list[str]:
    """Container paths of the set's own (non-library) audio references."""
    base, out = Path(als).parent, []
    for s in summary.get("samples", []):
        if s.get("pack"):
            continue
        if s.get("absolute") and "/My Recordings/" in s["absolute"]:
            out.append(ROOT + "/" + s["absolute"].split("/My Recordings/", 1)[1])
        elif s.get("relative"):
            out.append(str((base / s["relative"]).resolve()) if not s["relative"].startswith("/") else s["relative"])
    return out


def project_audio_prefixes(als: str) -> list[str]:
    """Where a set keeps its own recordings: a '<name> Project' folder as a whole;
    otherwise '<name> Sounds/' (Live <= 7) and Samples/Recorded next to it."""
    folder, stem = Path(als).parent, Path(als).stem
    if folder.name.endswith(" Project"):
        return [str(folder) + "/"]
    return [f"{folder}/{stem} Sounds/", f"{folder}/Samples/Recorded/"]


async def link_files(sid: str, als: str, summary: dict) -> None:
    p = await pool()
    rows = []
    for prefix in project_audio_prefixes(als):
        rows += [(r["path"], "project-audio", r["audio_id"], True) for r in await p.fetch(
            "SELECT path, audio_id FROM music.audio_locations WHERE missing_since IS NULL AND starts_with(path, $1)",
            prefix)]
    known = {r[0] for r in rows}
    for path in await asyncio.to_thread(project_files_on_disk, als):       # new takes, not catalogued yet
        if path not in known:
            aid = await p.fetchval("SELECT audio_id FROM music.audio_locations WHERE path=$1", path)
            rows.append((path, "project-audio", aid, True))
            known.add(path)
    for path in own_sample_paths(als, summary):
        if path in known:
            continue
        aid = await p.fetchval("SELECT audio_id FROM music.audio_locations WHERE path=$1", path)
        rows.append((path, "sample", aid, await asyncio.to_thread(os.path.exists, path)))
        known.add(path)
    for ref in outside_refs(summary):
        if ref not in known:
            rows.append((ref, "outside", None, False))
    async with p.acquire() as c, c.transaction():
        await c.execute("DELETE FROM music.studio_files WHERE session_id=$1", sid)
        await c.executemany("INSERT INTO music.studio_files (session_id, path, role, audio_id, present) "
                            "VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING", [(sid, *r) for r in rows])


async def scan(full: bool) -> dict:
    """full: walk the tree (finds new sets); else re-check every known set's file date."""
    p = await pool()
    if full:
        sets = await asyncio.to_thread(find_sets, ROOT)
    else:
        sets = [r["project_file"] for r in await p.fetch(
            "SELECT project_file FROM music.sessions WHERE daw='ableton' AND starts_with(project_file, $1)", ROOT)]
    new = 0
    for als in sets:
        try:
            first = not await p.fetchval("SELECT 1 FROM music.sessions WHERE project_file=$1", als)
            if first:
                for b in await asyncio.to_thread(backups_of, als):
                    new += bool(await ingest(b, "backup", owner=als))
            new += bool(await ingest(als, "save"))
        except FileNotFoundError:
            continue
        except Exception:  # noqa: BLE001 -- one set must not stop the others
            log.exception("ingest of %s failed", als)
    _status.update({"full" if full else "hot": datetime.now(TZ).isoformat(), "sets": len(sets) if full else
                    _status.get("sets"), "last_new": new})
    return {"sets": len(sets), "new_snapshots": new}


async def _watch() -> None:
    await asyncio.sleep(15)
    last_full = datetime.min.replace(tzinfo=TZ)
    while True:
        try:
            now = datetime.now(TZ)
            full = now - last_full > timedelta(minutes=WALK_EVERY_MIN)
            await scan(full)
            if full:
                last_full = now
        except Exception:  # noqa: BLE001
            log.exception("studio scan failed")
        await asyncio.sleep(HOT_EVERY_S)


# ── notes ────────────────────────────────────────────────────────────────────

async def active_project(speaker: str) -> Optional[dict]:
    p = await pool()
    sid = await p.fetchval("SELECT session_id FROM music.studio_talks WHERE speaker=$1 AND status='open' "
                           "ORDER BY id DESC LIMIT 1", speaker)
    if sid:
        row = await p.fetchrow("SELECT * FROM music.sessions WHERE id=$1", sid)
        return dict(row) if row else None
    row = await p.fetchrow(
        "SELECT s.* FROM music.sessions s JOIN music.studio_snapshots n ON n.session_id=s.id "
        "WHERE n.source='save' AND n.saved_at > now() - make_interval(hours => $1) ORDER BY n.saved_at DESC LIMIT 1",
        ACTIVE_HOURS)
    return dict(row) if row else None


async def find_project(text: str) -> Optional[dict]:
    p = await pool()
    row = await p.fetchrow(
        "SELECT * FROM music.sessions WHERE daw='ableton' AND (title ILIKE '%' || $1 || '%' OR similarity(title, $1) > 0.3) "
        "ORDER BY similarity(title, $1) DESC, updated_at DESC LIMIT 1", text.strip())
    return dict(row) if row else None


async def latest(sid) -> Optional[dict]:
    row = await (await pool()).fetchrow("SELECT summary, saved_at FROM music.studio_snapshots WHERE session_id=$1 "
                                        "AND source='save' ORDER BY saved_at DESC LIMIT 1", sid)
    return {**json.loads(row["summary"]), "saved_at": row["saved_at"]} if row else None


async def add_note(speaker: str, text: str, kind: str = "note") -> str:
    proj = await active_project(speaker)
    snap = None
    if proj:
        snap = await (await pool()).fetchval("SELECT id FROM music.studio_snapshots WHERE session_id=$1 "
                                             "ORDER BY saved_at DESC LIMIT 1", proj["id"])
    await (await pool()).execute("INSERT INTO music.studio_events (session_id, kind, text, snapshot_id, speaker) "
                                 "VALUES ($1,$2,$3,$4,$5)", proj["id"] if proj else None, kind, text.strip(), snap, speaker)
    label = {"idea": "Idea", "todo": "To-do", "note": "Note"}[kind]
    return f"{label} saved for “{proj['title']}”." if proj else \
        f"{label} saved in the studio chronicle (no set saved in the last {ACTIVE_HOURS} hours, so it isn't filed under one)."


def note_kind(word: str) -> str:
    w = (word or "note").lower()
    return "idea" if w in ("idea", "idee") else "todo" if w in ("todo", "to do") else "note"


# ── session facts + talk ─────────────────────────────────────────────────────

def set_lines(s: dict) -> list[str]:
    if s.get("format") != "xml":
        return [s.get("live", "")]
    out = [A.describe(s)]
    for t in s["tracks"]:
        loops = [c for c in t["session_clips"] if c["looping"]]
        bits = [f"{t['kind']} track '{t['name']}'"]
        if t["devices"]:
            bits.append("devices: " + ", ".join(t["devices"][:6]))
        if t["session_clips"]:
            bits.append(f"{len(t['session_clips'])} session clips" + (f" ({len(loops)} looping: " + ", ".join(
                f"'{c['name'] or 'untitled'}' {(c['loop_end'] - c['loop_start']) / 4:g} bars" for c in loops[:4]) + ")" if loops else ""))
        if t["arrangement_clips"]:
            bits.append(f"{len(t['arrangement_clips'])} arrangement clips")
        out.append("; ".join(bits))
    if s.get("locators"):
        n, d = (int(x) for x in s["time_signature"].split("/"))
        out.append("Locators: " + ", ".join(f"{l['name']} (bar {l['beat'] / (n * 4 / d) + 1:g})" for l in s["locators"]))
    if s.get("scenes"):
        out.append("Scenes: " + ", ".join(s["scenes"][:12]))
    if s.get("annotation"):
        out.append(f"Set note: {s['annotation'][:300]}")
    return out


async def own_progressions() -> list[dict]:
    rows = await (await pool()).fetch(
        "(SELECT pattern, mode, recordings FROM music.progressions WHERE mode='major' AND length >= 3 ORDER BY recordings DESC LIMIT 5) "
        "UNION ALL (SELECT pattern, mode, recordings FROM music.progressions WHERE mode='minor' AND length >= 3 ORDER BY recordings DESC LIMIT 5)")
    return [dict(r) for r in rows]


async def plugin_names() -> list[str]:
    rows = await (await pool()).fetch("SELECT DISTINCT name FROM music.plugins WHERE category='instrument' AND type <> 'builtin'")
    return [r["name"] for r in rows]


async def facts(speaker: str, text: str = "") -> tuple[str, Optional[dict], Optional[tuple], Optional[dict]]:
    proj = await active_project(speaker)
    lines, key = [], None
    if proj:
        s = await latest(proj["id"])
        lines.append(f"Open set: “{proj['title']}”, folder {nas_path(proj['session_dir'] or '')}.")
        if s:
            lines.append(f"Last saved {s['saved_at'].astimezone(TZ):%a %d %b %H:%M}.")
            lines += set_lines(s)
        p = await pool()
        ev = await p.fetch("SELECT at, kind, text FROM music.studio_events WHERE session_id=$1 ORDER BY at DESC LIMIT 8", proj["id"])
        if ev:
            lines.append("Recent in this set: " + " | ".join(f"{e['at'].astimezone(TZ):%d %b %H:%M} {e['kind']}: {e['text'][:160]}"
                                                            for e in reversed(ev)))
        todos = await p.fetch("SELECT text FROM music.studio_events WHERE session_id=$1 AND kind='todo' AND NOT done "
                              "ORDER BY at DESC LIMIT 5", proj["id"])
        if todos:
            lines.append("Open to-dos: " + "; ".join(t["text"] for t in todos))
        key_text = proj.get("key_signature") or ""
        if s and s.get("scale"):
            key_text = s["scale"]
        if pk := T.parse_key_text(key_text):
            key = T.make_key(*pk)
        tempo = float(s["tempo"]) if s and s.get("tempo") else None
    else:
        lines.append(f"No set was saved in the last {ACTIVE_HOURS} hours, so DOMO doesn't know which one is open. "
                     "Ask him which set, or to save once.")
        tempo = None
    if pk := T.parse_key_text(text):                      # a key he names now wins
        key = T.make_key(*pk)
    if key:
        lines.append(T.theory_block(key, await own_progressions()))
    else:
        lines.append("Key: not known (no scale set in Live). Ask him for the key before naming chords.")
    if tempo is None and (m := re.search(r"\b(\d{2,3}(?:\.\d)?)\s*bpm\b", text, re.I)):
        tempo = float(m.group(1))
    plan = None
    if tempo and _ARRANGE.search(text):
        plan = T.arrangement_plan(tempo, T.parse_minutes(text) or 3.5,
                                  time_signature=(s or {}).get("time_signature") if proj and s else "4/4")
        lines.append(T.plan_line(plan) + " This plan is already shown to him at the top of your reply: "
                     "comment on it, don't give other bar numbers.")
    lines.append("Instruments in his Live 12 Suite: " + ", ".join(LIVE_INSTRUMENTS) +
                 (". Plugins: " + ", ".join(await plugin_names()) if await plugin_names() else "") + ".")
    return "\n".join(f"- {x}" for x in lines), proj, key, plan


TALK_PROMPT = """You are DOMO, {name}'s studio partner while he makes music in Ableton Live 12. It is {now:%A %H:%M}.
Speak like a friendly, experienced producer and music teacher: concrete, encouraging, honest.
2 to 6 plain sentences, it may be read aloud -- no Markdown, no lists, no bold. One question at a time.
Reply in the language he uses.

What you know (the only facts -- never invent tracks, clips, files or past sessions):
{facts}

Rules:
- You cannot change the Live set. Never say you added, set, moved or created anything in Live; tell him
  what to do (which instrument or device, which track, how many bars, which notes) and he does it.
- Name chords only from the chord lists above, and write each numeral with its chord exactly as listed
  ("bVII G", "iv Dm"); if he wants another key, ask him to name it.
- Arrangement: when a bar plan is in the facts, use its bar numbers and lengths exactly (you may rename or
  reorder sections); never invent bar counts or say a plan "fits" the set unless the numbers show it.
- If he says something worth keeping, suggest he says "idea: ..." or "todo: ..." so it goes in the chronicle."""

SUMMARY_PROMPT = """Write a short studio log entry (at most 120 words, plain text) about this session between {name}
and DOMO: what he worked on, the musical ideas and decisions, and what's next. Only what was said; nothing invented;
DOMO's suggestions are suggestions, not things done."""


async def llm(messages: list[dict], temperature: float, num_predict: int) -> str:
    body = {"model": LLM_MODEL, "stream": False, "messages": messages,
            "options": {"num_ctx": LLM_NUM_CTX, "temperature": temperature, "num_predict": num_predict}}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{LLM_URL}/api/chat", json=body)
        r.raise_for_status()
    return r.json()["message"]["content"].strip()


async def open_talk(speaker: str) -> Optional[dict]:
    row = await (await pool()).fetchrow("SELECT * FROM music.studio_talks WHERE speaker=$1 AND status='open' "
                                        "ORDER BY id DESC LIMIT 1", speaker)
    return dict(row) if row else None


async def talk_start(speaker: str) -> str:
    async with _lock:
        if t := await open_talk(speaker):
            return "We're already in the studio. What are you working on?"
        proj = await active_project(speaker)
        if proj:
            s = await latest(proj["id"])
            p = await pool()
            last = await p.fetchval("SELECT text FROM music.studio_events WHERE session_id=$1 AND kind='save' "
                                    "ORDER BY at DESC LIMIT 1", proj["id"])
            todos = await p.fetchval("SELECT count(*) FROM music.studio_events WHERE session_id=$1 AND kind='todo' "
                                     "AND NOT done", proj["id"])
            change = (last or "").removeprefix("Saved: ")
            opening = (f"Studio session on “{proj['title']}”: {A.describe(s) if s else 'no save read yet'}. "
                       + (f"Last change: {change} " if last and not change.startswith("First seen") else "")
                       + (f"{todos} open to-do{'s' if todos != 1 else ''}. " if todos else "")
                       + "What are you working on?")
        else:
            opening = (f"Studio session started. I don't see a set saved in the last {ACTIVE_HOURS} hours: "
                       "save once in Live and I'll know which one we're in. What are you working on?")
        await (await pool()).execute(
            "INSERT INTO music.studio_talks (speaker, session_id, turns) VALUES ($1,$2,$3)", speaker,
            proj["id"] if proj else None, json.dumps([{"role": "assistant", "content": opening}]))
    return opening


async def talk_turn(speaker: str, text: str) -> dict:
    t = await open_talk(speaker)
    if not t:
        return {"active": False}
    if _TALK_END.match(text):
        return {"active": True, "answer": await talk_end(speaker, "said")}
    prefix = ""
    if m := _NOTE.match(text):
        prefix = await add_note(speaker, m.group(2), note_kind(m.group(1))) + " "
    turns = json.loads(t["turns"]) + [{"role": "user", "content": text}]
    f, proj, key, plan = await facts(speaker, text)
    if proj and not t["session_id"]:                      # he saved after starting: now we know the set
        await (await pool()).execute("UPDATE music.studio_talks SET session_id=$2 WHERE id=$1", t["id"], proj["id"])
    system = TALK_PROMPT.format(name=NAMES.get(speaker, speaker.title()), now=datetime.now(TZ), facts=f)
    messages = [{"role": "system", "content": system}] + turns[-24:]
    try:
        answer = await llm(messages, 0.6, 380)
        if key:
            # Chord talk is checked by code; one rewrite, then any remaining slip is said out loud.
            allowed = set(re.findall(r"[A-G][#b]?(?:m|dim|aug)?", f + " " + text))
            if problems := T.check_chords(answer, key, allowed):
                log.info("studio chord check: %s", problems)
                # As a system note, not as owner: FOUND 2026-10-24 a "user" correction made DOMO apologise to him.
                answer = await llm(messages + [{"role": "assistant", "content": answer},
                                               {"role": "system", "content": "Chord checker (internal, never mention it): " +
                                                "; ".join(problems) + ". Rewrite your last reply with the right chords. "
                                                "Reply with the rewritten answer only, as if it were your first."}], 0.3, 380)
                if left := T.check_chords(answer, key, allowed):
                    answer += " (Chord check: " + "; ".join(left) + ".)"
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("studio turn failed: %s", e)
        answer = "Sorry, I lost the thread for a moment. Say that again?"
    if plan:            # FOUND 2026-10-24: the model made up its own bar counts -- the plan is code's
        answer = T.plan_line(plan) + " " + answer.replace(T.plan_line(plan), "").strip()
    answer = prefix + answer
    turns.append({"role": "assistant", "content": answer})
    await (await pool()).execute("UPDATE music.studio_talks SET turns=$2, last_at=now() WHERE id=$1", t["id"], json.dumps(turns))
    return {"active": True, "answer": answer}


async def talk_end(speaker: str, why: str) -> str:
    async with _lock:
        t = await open_talk(speaker)
        if not t:
            return "There's no studio session open."
        turns = json.loads(t["turns"])
        summary = None
        if any(x["role"] == "user" for x in turns):
            name = NAMES.get(speaker, speaker.title())
            try:
                summary = await llm([{"role": "system", "content": SUMMARY_PROMPT.format(name=name)},
                                     {"role": "user", "content": "\n".join(
                                         f"{name if x['role'] == 'user' else 'DOMO'}: {x['content']}" for x in turns)}], 0.2, 300)
            except (httpx.HTTPError, KeyError, ValueError) as e:
                log.warning("studio summary failed: %s", e)
        p = await pool()
        await p.execute("UPDATE music.studio_talks SET status='closed', ended_at=now(), summary=$2 WHERE id=$1", t["id"], summary)
        if summary:
            await p.execute("INSERT INTO music.studio_events (session_id, kind, text, speaker, detail) "
                            "VALUES ($1,'talk',$2,$3,$4)", t["session_id"], summary, speaker, json.dumps({"talk_id": str(t["id"])}))
    return ("Studio session saved to the chronicle." if summary else "Studio session closed.") + \
        (" Good work today." if why == "said" else "")


async def _idle() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            for r in await (await pool()).fetch("SELECT DISTINCT speaker FROM music.studio_talks WHERE status='open' "
                                                "AND last_at < now() - make_interval(mins => $1)", TALK_IDLE_MIN):
                await talk_end(r["speaker"], "idle")
        except Exception:  # noqa: BLE001
            log.exception("idle close failed")


# ── read-backs ───────────────────────────────────────────────────────────────

async def chronicle_today(speaker: str) -> str:
    since = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = await (await pool()).fetch(
        "SELECT s.title, e.kind, e.text FROM music.studio_events e LEFT JOIN music.sessions s ON s.id=e.session_id "
        "WHERE e.at >= $1 AND e.kind <> 'talk' ORDER BY e.at", since)
    if not rows:
        return "Nothing in the studio chronicle today yet."
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["title"] or "not filed", []).append(r)
    parts = []
    for title, evs in by.items():
        saves = [e for e in evs if e["kind"] == "save"]
        notes = [e for e in evs if e["kind"] != "save"]
        bit = f"“{title}”: "
        if saves:
            bit += f"saved {len(saves)} time{'s' if len(saves) > 1 else ''}; latest: {saves[-1]['text'].removeprefix('Saved: ')[:220]}"
        if notes:
            bit += (". " if saves else "") + "; ".join(f"{e['kind']}: {e['text'][:120]}" for e in notes[-4:])
        parts.append(bit)
    return "Today in the studio: " + " ".join(p.rstrip(".") + "." for p in parts)


async def where_files(text: str) -> str:
    proj = await find_project(text) if text.strip() else None
    if not proj:
        return f"I couldn't find a Live set called “{text.strip()}”."
    p = await pool()
    files = await p.fetch("SELECT role, present FROM music.studio_files WHERE session_id=$1", proj["id"])
    own = sum(1 for f in files if f["role"] == "project-audio")
    missing = sum(1 for f in files if not f["present"])
    return (f"“{proj['title']}” is at {nas_path(proj['project_file'])} on DATAGRID. "
            f"{own} recording{' of its own is' if own == 1 else 's of its own are'} in the catalogue" +
            (f"; {missing} referenced file{'s are' if missing != 1 else ' is'} missing." if missing else "."))


# ── API ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="studio-partner")


@app.on_event("startup")
async def _startup() -> None:
    await pool()
    if os.environ.get("STUDIO_WATCH", "on") == "on":
        asyncio.get_event_loop().create_task(_watch())
    asyncio.get_event_loop().create_task(_idle())


@app.get("/health")
async def health():
    await (await pool()).fetchval("SELECT 1")
    return {"status": "ok", **_status}


class SpeakerText(BaseModel):
    speaker: str
    text: str = ""


def _person(speaker: str) -> None:
    if speaker not in PEOPLE:
        raise HTTPException(404, f"no studio partner for {speaker}")


@app.post("/notes")
async def ep_note(req: SpeakerText, kind: str = "note"):
    _person(req.speaker)
    return {"answer": await add_note(req.speaker, req.text, note_kind(kind))}


@app.get("/talk/active")
async def ep_active(speaker: str):
    return {"active": speaker in PEOPLE and await open_talk(speaker) is not None}


@app.post("/talk/start")
async def ep_start(req: SpeakerText):
    _person(req.speaker)
    return {"answer": await talk_start(req.speaker)}


@app.post("/talk/turn")
async def ep_turn(req: SpeakerText):
    _person(req.speaker)
    return await talk_turn(req.speaker, req.text)


@app.post("/talk/end")
async def ep_end(req: SpeakerText):
    _person(req.speaker)
    return {"answer": await talk_end(req.speaker, "said")}


@app.get("/chronicle/today")
async def ep_today(speaker: str):
    _person(speaker)
    return {"answer": await chronicle_today(speaker)}


@app.get("/files")
async def ep_files(q: str):
    return {"answer": await where_files(q)}


@app.get("/theory")
async def ep_theory(key: str, tempo: Optional[float] = None):
    """Chords for a key (and bar maths for a tempo) -- for the UI and testing."""
    pk = T.parse_key_text(key if re.search(r"major|minor|moll|dur|m\b", key, re.I) else f"key of {key}")
    if not pk:
        raise HTTPException(400, "name a key, e.g. 'D minor'")
    k = T.make_key(*pk)
    return {"key": T.key_name(k), "diatonic": T.diatonic(k), "borrowed": T.borrowed(k),
            "secondary_dominants": T.secondary_dominants(k), "related": T.related_keys(k),
            "own": [{**p, "chords": T.realise_pattern(p["pattern"], k)} for p in await own_progressions() if p["mode"] == k[1]],
            "arrangement": T.arrangement_plan(tempo) if tempo else None}


@app.get("/projects")
async def ep_projects(q: str = "", limit: int = 100):
    rows = await (await pool()).fetch(
        "SELECT s.id, s.title, s.project_file, s.status, s.tempo_bpm, s.key_signature, "
        "max(n.saved_at) AS last_saved, count(n.id) AS saves, "
        "(SELECT count(*) FROM music.studio_events e WHERE e.session_id=s.id AND e.kind IN ('note','idea','todo')) AS notes "
        "FROM music.sessions s LEFT JOIN music.studio_snapshots n ON n.session_id=s.id WHERE s.daw='ableton' "
        "AND ($1 = '' OR s.title ILIKE '%' || $1 || '%' OR s.project_file ILIKE '%' || $1 || '%') "
        "GROUP BY s.id ORDER BY max(n.saved_at) DESC NULLS LAST LIMIT $2", q, limit)
    return [{**dict(r), "id": str(r["id"]), "nas_path": nas_path(r["project_file"] or ""),
             "folder": nas_path(str(Path(r["project_file"] or "").parent))} for r in rows]


@app.get("/projects/{sid}")
async def ep_project(sid: str):
    p = await pool()
    s = await p.fetchrow("SELECT * FROM music.sessions WHERE id=$1::uuid", sid)
    if not s:
        raise HTTPException(404, "no such project")
    snap = await latest(s["id"])
    events = await p.fetch("SELECT id, at, kind, text, detail, done FROM music.studio_events WHERE session_id=$1 "
                           "ORDER BY at DESC LIMIT 300", s["id"])
    files = await p.fetch("SELECT f.path, f.role, f.present, a.id AS audio_id, l.beets_item_id FROM music.studio_files f "
                          "LEFT JOIN music.audio_files a ON a.id=f.audio_id "
                          "LEFT JOIN music.audio_locations l ON l.path=f.path WHERE f.session_id=$1 ORDER BY f.path", s["id"])
    return {"project": {**dict(s), "id": str(s["id"]), "nas_path": nas_path(s["project_file"] or ""),
                        "folder": nas_path(s["session_dir"] or "")},
            "latest": {**snap, "saved_at": snap["saved_at"].isoformat(), "lines": set_lines(snap)} if snap else None,
            "events": [{**dict(e), "at": e["at"].isoformat(), "detail": json.loads(e["detail"]) if e["detail"] else None}
                       for e in events],
            "files": [{**dict(f), "audio_id": str(f["audio_id"]) if f["audio_id"] else None,
                       "nas_path": nas_path(f["path"])} for f in files]}


# ── for the Mac's `studio` script (plain text: no JSON tooling needed on the Mac) ──
# Paths are relative to My Recordings (= STUDIO_ROOT here, /Volumes/Multimedia/Music/My Recordings on the Mac).

def rel(p: str) -> str:
    return p[len(ROOT):].lstrip("/") if p.startswith(ROOT) else p


@app.get("/studio/list", response_class=PlainTextResponse)
async def st_list(q: str = "", limit: int = 30):
    rows = await ep_projects(q, limit)
    return "".join(f"{r['title']}\t{rel(str(Path(r['project_file'] or '').parent))}\t"
                   f"{(r['last_saved'].astimezone(TZ).strftime('%Y-%m-%d %H:%M') if r['last_saved'] else '')}\n"
                   for r in rows)


@app.get("/studio/where", response_class=PlainTextResponse)
async def st_where(q: str):
    proj = await find_project(q)
    if not proj or not proj.get("project_file"):
        raise HTTPException(404, f"no Live set called {q!r}")
    als = proj["project_file"]
    folder = Path(als).parent
    own = folder.name.endswith(" Project")
    extra = [] if own else [str(folder / f"{Path(als).stem} Sounds"), str(folder / "Samples")]
    lines = [f"TITLE={proj['title']}", f"SET={rel(als)}", f"FOLDER={rel(str(folder))}",
             f"PROJECT_FOLDER={'yes' if own else 'no'}"] + \
            [f"EXTRA={rel(x)}" for x in extra if os.path.exists(x)]
    return "\n".join(lines) + "\n"


@app.get("/studio/check", response_class=PlainTextResponse)
async def st_check(als: str, mtime: float = 0):
    """Is DATAGRID's copy of this set (path relative to My Recordings) at least as new as the
    Mac's (mtime, epoch s), are its own files there, and does it use files outside its project?"""
    path = os.path.normpath(os.path.join(ROOT, als))
    if not path.startswith(ROOT + "/"):
        raise HTTPException(400, "path outside My Recordings")
    if not os.path.exists(path):
        return "STATE=missing\n"
    st = os.stat(path)
    lines = []
    if mtime and st.st_mtime + 2 < mtime:
        lines.append("STATE=stale")
    try:
        summary = A.read_set(await asyncio.to_thread(Path(path).read_bytes))
    except Exception:  # noqa: BLE001 -- half-synced file
        return "STATE=stale\n" if lines else "STATE=unreadable\n"
    if summary.get("format") != "xml":
        return (lines[0] if lines else "STATE=old-format") + "\n"
    outside = outside_refs(summary)
    base = Path(path).parent
    missing = [str(base / x["relative"]) for x in summary.get("samples", [])
               if not x.get("pack") and ref_inside(x) and not (base / x["relative"]).exists()]
    lines = lines or [f"STATE={'outside' if outside else 'incomplete' if missing else 'ok'}"]
    lines += [f"OUTSIDE {x}" for x in outside] + [f"MISSING {rel(x)}" for x in missing]
    return "\n".join(lines) + "\n"


# ── practice (2026-10-31) ────────────────────────────────────────────────────
# Exercises are made and graded in practice.py; this stores the answers and reads
# back what to weight. Tables: music.practice_attempts (music_schema.sql).
PRACTICE_DAY_MIN = 10            # answers that make a day count for the streak


async def practice_history(speaker: str, deck: str) -> tuple[dict[str, list[bool]], str]:
    """Last 5 results per item (oldest first; "Show me" counts as a miss) and the item asked last."""
    db = await pool()
    rows = await db.fetch(
        "SELECT item, array_agg(ok ORDER BY at) AS results FROM ("
        " SELECT item, at, correct AND NOT hint_used AS ok,"
        " row_number() OVER (PARTITION BY item ORDER BY at DESC) AS rn"
        " FROM music.practice_attempts WHERE speaker=$1 AND deck=$2) x WHERE rn <= 5 GROUP BY item", speaker, deck)
    last = await db.fetchval("SELECT item FROM music.practice_attempts WHERE speaker=$1 AND deck=$2 "
                             "ORDER BY at DESC LIMIT 1", speaker, deck)
    return {r["item"]: list(r["results"]) for r in rows}, last or ""


async def practice_summary(speaker: str, since: datetime, until: datetime) -> dict:
    rows = await (await pool()).fetch(
        "SELECT at, correct FROM music.practice_attempts WHERE speaker=$1 AND at >= $2 AND at < $3", speaker, since, until)
    return {"attempts": len(rows), "correct": sum(r["correct"] for r in rows),
            "minutes": PR.minutes([r["at"] for r in rows])}


class Attempt(BaseModel):
    speaker: str
    exercise: dict
    answer: Any = None
    ms: Optional[int] = None
    hint_used: bool = False


@app.get("/practice/decks")
async def ep_practice_decks():
    return PR.DECKS


@app.get("/practice/next")
async def ep_practice_next(speaker: str, deck: str, level: int = 1):
    _person(speaker)
    try:
        PR.check_level(deck, level)
    except PR.PracticeError as e:
        raise HTTPException(400, str(e))
    history, last = await practice_history(speaker, deck)
    own = await own_progressions() if "progressions" in deck else []
    return PR.next_exercise(deck, level, history, last_item=last, own=own)


@app.post("/practice/attempt")
async def ep_practice_attempt(req: Attempt):
    _person(req.speaker)
    ex = req.exercise
    try:
        PR.check_level(ex.get("deck", ""), int(ex.get("level", 0)))
        result = PR.grade(ex, req.answer)
    except (PR.PracticeError, KeyError, ValueError, TypeError, StopIteration, AttributeError) as e:
        raise HTTPException(400, f"cannot grade this exercise: {e}")
    await (await pool()).execute(
        "INSERT INTO music.practice_attempts (speaker, deck, level, item, correct, hint_used, ms, answer, exercise, result) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb)",
        req.speaker, ex["deck"], int(ex["level"]), str(ex.get("item", ""))[:200], result["correct"], req.hint_used,
        req.ms, json.dumps(req.answer), json.dumps(ex), json.dumps(result))
    return result


@app.get("/practice/stats")
async def ep_practice_stats(speaker: str):
    _person(speaker)
    db = await pool()
    now = datetime.now(TZ)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    days = await db.fetch(
        "SELECT (at AT TIME ZONE 'Europe/Berlin')::date AS d FROM music.practice_attempts "
        "WHERE speaker=$1 AND at > now() - interval '400 days' GROUP BY 1 HAVING count(*) >= $2", speaker, PRACTICE_DAY_MIN)
    decks = await db.fetch(
        "SELECT deck, count(*) AS tries, avg(CASE WHEN correct AND NOT hint_used THEN 1.0 ELSE 0 END) AS accuracy "
        "FROM music.practice_attempts WHERE speaker=$1 AND at > now() - interval '7 days' GROUP BY deck", speaker)
    weakest = await db.fetch(
        "SELECT deck, item, count(*) AS tries, count(*) FILTER (WHERE NOT correct OR hint_used) AS missed FROM ("
        " SELECT deck, item, correct, hint_used, row_number() OVER (PARTITION BY deck, item ORDER BY at DESC) AS rn"
        " FROM music.practice_attempts WHERE speaker=$1 AND at > now() - interval '30 days') x"
        " WHERE rn <= 5 GROUP BY deck, item HAVING count(*) >= 3 AND count(*) FILTER (WHERE NOT correct OR hint_used) > 0"
        " ORDER BY count(*) FILTER (WHERE NOT correct OR hint_used)::float / count(*) DESC, count(*) DESC LIMIT 8", speaker)
    return {"today": await practice_summary(speaker, day0, now + timedelta(seconds=1)),
            "streak": PR.day_streak({r["d"] for r in days}, now.date()), "day_min": PRACTICE_DAY_MIN,
            "decks": {r["deck"]: {"tries": r["tries"], "accuracy": round(float(r["accuracy"]), 2)} for r in decks},
            "weakest": [dict(r) for r in weakest]}


@app.get("/practice/summary")
async def ep_practice_summary(speaker: str, since: datetime, until: datetime):
    """For domo-winddown: answers between two times (its night runs 04:00 to 04:00)."""
    _person(speaker)
    return await practice_summary(speaker, since, until)


@app.post("/scan")
async def ep_scan(full: bool = True):
    return await scan(full)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)
