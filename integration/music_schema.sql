-- ============================================================================
-- music-studio: PostgreSQL Schema
-- Schema: music
--
-- Data model inspired by Ableton Live's session view:
--   Sessions   — a recording/composition project (like an .als file)
--   Songs      — the composition entity (may span many sessions)
--   Tracks     — audio, MIDI, instrument, return, or master tracks
--   Clips      — individual audio/MIDI regions on a track
--   Instruments — physical/virtual instruments catalog
--   Plugins    — VST/AU/CLAP/builtin plugin catalog
--   References — songs to cover, study, or arrange (wishlist → done)
--   Field Recordings — raw captures from iPhone, portable mics, etc.
--
-- Naming: snake_case throughout
-- UUIDs: gen_random_uuid() via pgcrypto / pg 13+
-- Audit: updated_at maintained via trigger on all main tables
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS music;

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- for fast fuzzy-text search

-- ============================================================================
-- ENUM-style check constraints (avoids enum migration headaches)
-- ============================================================================

-- session / song / reference statuses in one place via domain
-- draft → in_progress → needs_more → finished → archived
-- covers:  wishlist → in_progress → covered → arranged → abandoned

-- ============================================================================
-- Sessions
-- "Project file" equivalent — everything that happened in one work session.
-- May be a DAW project, a field recording, a jam session, etc.
-- ============================================================================
CREATE TABLE music.sessions (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title          TEXT        NOT NULL,
    project_file   TEXT,                       -- absolute path to .als / .cwp / .ptx
    session_dir    TEXT,                       -- folder containing all session assets
    daw            TEXT        DEFAULT 'ableton',  -- ableton | logic | cubase | reaper | ardour | none
    status         TEXT        NOT NULL DEFAULT 'draft'
                               CHECK (status IN ('draft','in_progress','needs_more','finished','archived')),
    mood           TEXT[]      DEFAULT '{}',   -- ['energetic','melancholic','ambient']
    genre          TEXT[]      DEFAULT '{}',
    tempo_bpm      NUMERIC(6,2),
    key_signature  TEXT,                       -- 'C major', 'A minor', etc.
    time_signature TEXT        DEFAULT '4/4',
    notes          TEXT,
    recorded_at    TIMESTAMPTZ,
    location       TEXT,                       -- 'home-studio' | 'live' | 'field' | 'iphone'
    source_device  TEXT,                       -- 'focusrite-solo' | 'iphone-15' | 'zoom-h6'
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_sessions_status    ON music.sessions(status);
CREATE INDEX idx_sessions_mood      ON music.sessions USING GIN(mood);
CREATE INDEX idx_sessions_genre     ON music.sessions USING GIN(genre);
CREATE INDEX idx_sessions_title_trgm ON music.sessions USING GIN(title gin_trgm_ops);

COMMENT ON TABLE  music.sessions IS 'Recording / composition session — analogous to an Ableton .als project file';
COMMENT ON COLUMN music.sessions.mood IS 'Array of mood tags e.g. {energetic, melancholic}';

-- ============================================================================
-- Songs
-- The composition entity. Multiple sessions can contribute to one song.
-- ============================================================================
CREATE TABLE music.songs (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title              TEXT        NOT NULL,
    composer           TEXT[]      DEFAULT '{}',     -- ['Self', 'Co-writer Name']
    genre              TEXT[]      DEFAULT '{}',
    mood               TEXT[]      DEFAULT '{}',
    key_signature      TEXT,
    tempo_bpm          NUMERIC(6,2),
    time_signature     TEXT        DEFAULT '4/4',
    duration_sec       NUMERIC(10,2),
    status             TEXT        NOT NULL DEFAULT 'draft'
                                   CHECK (status IN ('draft','in_progress','needs_more','finished','archived')),
    is_original        BOOLEAN     DEFAULT TRUE,
    cover_source_title  TEXT,                         -- title of the source song (covers only)
    cover_source_artist TEXT,                         -- original artist
    cover_source_url   TEXT,                          -- YouTube/Spotify/Bandcamp URL
    arrangement_notes  TEXT,                          -- how this version differs
    lyrics             TEXT,
    notes              TEXT,
    tags               TEXT[]      DEFAULT '{}',
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    updated_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_songs_status       ON music.songs(status);
CREATE INDEX idx_songs_is_original  ON music.songs(is_original);
CREATE INDEX idx_songs_title_trgm   ON music.songs USING GIN(title gin_trgm_ops);
CREATE INDEX idx_songs_tags         ON music.songs USING GIN(tags);

-- ============================================================================
-- Session ↔ Song junction (many-to-many)
-- ============================================================================
CREATE TABLE music.session_songs (
    session_id  UUID NOT NULL REFERENCES music.sessions(id) ON DELETE CASCADE,
    song_id     UUID NOT NULL REFERENCES music.songs(id)    ON DELETE CASCADE,
    role        TEXT DEFAULT 'primary',   -- 'primary' | 'sketch' | 'reference'
    PRIMARY KEY (session_id, song_id)
);

-- ============================================================================
-- Instruments
-- Physical or virtual instruments you play or program.
-- ============================================================================
CREATE TABLE music.instruments (
    id           UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT    NOT NULL UNIQUE,
    type         TEXT    NOT NULL,          -- 'guitar' | 'piano' | 'bass' | 'drums' | 'vocals' | 'synth' | 'field-recording'
    brand        TEXT,
    model        TEXT,
    tuning       TEXT    DEFAULT 'standard', -- 'standard' | 'drop-d' | 'open-g' | 'DADGAD'
    midi_channel INTEGER CHECK (midi_channel BETWEEN 1 AND 16),
    notes        TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO music.instruments (name, type) VALUES
    ('Acoustic Guitar',  'guitar'),
    ('Electric Guitar',  'guitar'),
    ('Bass Guitar',      'bass'),
    ('Piano',            'piano'),
    ('Vocals',           'vocals'),
    ('Drums',            'drums'),
    ('Synth',            'synth'),
    ('iPhone Mic',       'field-recording')
ON CONFLICT DO NOTHING;

-- ============================================================================
-- Plugins
-- VST2/VST3/AU/CLAP/builtin plugin catalog.
-- ============================================================================
CREATE TABLE music.plugins (
    id           UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT    NOT NULL,
    manufacturer TEXT,
    type         TEXT    NOT NULL CHECK (type IN ('vst2','vst3','au','clap','builtin','rack')),
    category     TEXT    NOT NULL CHECK (category IN ('instrument','effect','analyzer','midi','other')),
    -- NOT NULL DEFAULT '' (not just TEXT) — Postgres UNIQUE constraints
    -- treat NULL as distinct from NULL, so with a nullable version column
    -- `ON CONFLICT DO NOTHING` below never actually fires: every re-run of
    -- this seed inserts 10 more duplicate rows. Found live 2026-08-21 after
    -- 4 schema-apply re-runs had produced 40 rows for what should've been
    -- 10 unique plugins.
    version      TEXT    NOT NULL DEFAULT '',
    path         TEXT,
    is_foss      BOOLEAN DEFAULT FALSE,
    license      TEXT,
    notes        TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (name, manufacturer, version)
);

-- Seed common free/builtin plugins
INSERT INTO music.plugins (name, manufacturer, type, category, is_foss, license) VALUES
    ('Compressor',     'Ableton',   'builtin', 'effect',     false, 'commercial'),
    ('EQ Eight',       'Ableton',   'builtin', 'effect',     false, 'commercial'),
    ('Reverb',         'Ableton',   'builtin', 'effect',     false, 'commercial'),
    ('Analog',         'Ableton',   'builtin', 'instrument', false, 'commercial'),
    ('Helm',           'Matt Tytel','vst3',    'instrument', true,  'GPL-3.0'),
    ('VITAL',          'Matt Tytel','vst3',    'instrument', false, 'freeware'),
    ('Surge XT',       'Surge Synth Team','vst3','instrument',true, 'GPL-3.0'),
    ('BYOD',           'Chowdhury DSP','vst3','effect',      true,  'GPL-3.0'),
    ('Dragonfly Reverb','Michael Willis','vst3','effect',    true,  'GPL-3.0'),
    ('TDR Nova',       'Tokyo Dawn','vst3',    'effect',     false, 'freeware')
ON CONFLICT DO NOTHING;

-- ============================================================================
-- Tracks
-- Individual tracks within a session — mirrors Ableton's track view.
-- ============================================================================
CREATE TABLE music.tracks (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    UUID        NOT NULL REFERENCES music.sessions(id) ON DELETE CASCADE,
    name          TEXT        NOT NULL,
    type          TEXT        NOT NULL CHECK (type IN ('audio','midi','instrument','return','master','group')),
    instrument_id UUID        REFERENCES music.instruments(id),
    color         TEXT        DEFAULT '#AAAAAA',   -- hex colour (Ableton-style)
    position      INTEGER     DEFAULT 0,           -- track order left→right
    volume_db     NUMERIC(5,2) DEFAULT 0.0,
    pan           NUMERIC(4,2) DEFAULT 0.0 CHECK (pan BETWEEN -1.0 AND 1.0),
    muted         BOOLEAN     DEFAULT FALSE,
    soloed        BOOLEAN     DEFAULT FALSE,
    notes         TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_tracks_session ON music.tracks(session_id);
CREATE INDEX idx_tracks_type    ON music.tracks(type);

-- ============================================================================
-- Track → Plugin chain (ordered)
-- ============================================================================
CREATE TABLE music.track_plugins (
    id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id    UUID    NOT NULL REFERENCES music.tracks(id)  ON DELETE CASCADE,
    plugin_id   UUID    NOT NULL REFERENCES music.plugins(id),
    position    INTEGER NOT NULL DEFAULT 0,          -- order in signal chain
    preset_name TEXT,
    enabled     BOOLEAN DEFAULT TRUE,
    params_json JSONB   DEFAULT '{}',                -- serialised plugin state
    UNIQUE (track_id, position)
);

-- ============================================================================
-- Clips
-- Audio / MIDI regions on a track (the "cells" in Ableton's Session View).
-- ============================================================================
CREATE TABLE music.clips (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id       UUID        NOT NULL REFERENCES music.tracks(id) ON DELETE CASCADE,
    name           TEXT,
    type           TEXT        NOT NULL CHECK (type IN ('audio','midi')),
    -- file locations
    file_path      TEXT,                        -- original recording or MIDI file
    midi_path      TEXT,                        -- Basic Pitch output (if transcribed)
    waveform_path  TEXT,                        -- pre-rendered waveform PNG/SVG
    stems_dir      TEXT,                        -- Demucs output directory
    -- position in arrangement
    start_beat     NUMERIC(10,3) DEFAULT 0,
    duration_beats NUMERIC(10,3),
    duration_sec   NUMERIC(10,3),
    -- audio properties
    sample_rate    INTEGER,
    channels       INTEGER     DEFAULT 2,
    bit_depth      INTEGER     DEFAULT 24,
    -- analysis results (from audio-analysis service)
    analysis_id    UUID,                        -- FK into audio_analysis schema
    bpm_detected   NUMERIC(6,2),
    key_detected   TEXT,
    loudness_lufs  NUMERIC(6,2),
    -- provenance
    recorded_at    TIMESTAMPTZ,
    source_device  TEXT,                        -- 'focusrite-solo' | 'iphone-15'
    source_url     TEXT,                        -- if captured from external source
    notes          TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_clips_track       ON music.clips(track_id);
CREATE INDEX idx_clips_type        ON music.clips(type);
CREATE INDEX idx_clips_bpm         ON music.clips(bpm_detected);

-- ============================================================================
-- Field Recordings
-- Raw captures — iPhone, Zoom H6, portable recorder — not yet in a session.
-- Think of this as the "inbox" / "harvesting" area.
-- ============================================================================
CREATE TABLE music.field_recordings (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    file_path    TEXT        NOT NULL,
    duration_sec NUMERIC(10,2),
    recorded_at  TIMESTAMPTZ,
    location     TEXT,                          -- 'outside', 'rehearsal', 'concert', GPS coords
    device       TEXT,                          -- 'iphone-15' | 'zoom-h6' | 'tascam-dr40x'
    description  TEXT,
    tags         TEXT[]      DEFAULT '{}',
    status       TEXT        NOT NULL DEFAULT 'inbox'
                             CHECK (status IN ('inbox','reviewed','used','archived','rejected')),
    -- resolved links
    analysis_id  UUID,                          -- audio-analysis result
    session_id   UUID        REFERENCES music.sessions(id) ON DELETE SET NULL,
    clip_id      UUID        REFERENCES music.clips(id)    ON DELETE SET NULL,
    midi_path    TEXT,                          -- if transcribed
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_field_recordings_status ON music.field_recordings(status);
CREATE INDEX idx_field_recordings_tags   ON music.field_recordings USING GIN(tags);

-- ============================================================================
-- References
-- Songs to cover, study, or arrange — the "wishlist to done" funnel.
-- ============================================================================
CREATE TABLE music.references (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT        NOT NULL,
    artist          TEXT,
    album           TEXT,
    year            INTEGER,
    genre           TEXT[]      DEFAULT '{}',
    mood            TEXT[]      DEFAULT '{}',
    key_signature   TEXT,
    tempo_bpm       NUMERIC(6,2),
    duration_sec    NUMERIC(10,2),
    time_signature  TEXT,
    url             TEXT,                       -- YouTube, Spotify, Bandcamp, SoundCloud
    tab_url         TEXT,                       -- Guitar tabs / chord charts
    notes           TEXT,
    status          TEXT        NOT NULL DEFAULT 'wishlist'
                                CHECK (status IN ('wishlist','studying','in_progress','covered','arranged','abandoned')),
    difficulty      TEXT        CHECK (difficulty IN ('beginner','easy','medium','hard','expert')),
    tags            TEXT[]      DEFAULT '{}',
    song_id         UUID        REFERENCES music.songs(id) ON DELETE SET NULL,  -- link to cover version
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_references_status      ON music.references(status);
CREATE INDEX idx_references_title_trgm  ON music.references USING GIN(title gin_trgm_ops);
CREATE INDEX idx_references_artist_trgm ON music.references USING GIN(artist gin_trgm_ops);

-- ============================================================================
-- Library (Beets-managed tracks — finished / exported)
-- Populated by Beets after import; read by music_catalog_api.py
-- ============================================================================
CREATE TABLE music.library (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    beets_id        INTEGER     UNIQUE,             -- beets item ID for sync
    title           TEXT        NOT NULL,
    artist          TEXT,
    album           TEXT,
    album_artist    TEXT,
    year            INTEGER,
    genre           TEXT,
    subgenre        TEXT,
    mood            TEXT,
    key_signature   TEXT,
    tempo_bpm       NUMERIC(6,2),
    duration_sec    NUMERIC(10,2),
    file_path       TEXT        NOT NULL,
    file_format     TEXT,                           -- flac, mp3, wav, ogg
    sample_rate     INTEGER,
    bit_depth       INTEGER,
    channels        INTEGER,
    loudness_lufs   NUMERIC(6,2),
    mb_track_id     TEXT,                           -- MusicBrainz track GUID
    mb_album_id     TEXT,                           -- MusicBrainz album GUID
    fingerprint     TEXT,                           -- Chromaprint acoustic fingerprint
    song_id         UUID        REFERENCES music.songs(id) ON DELETE SET NULL,
    imported_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_library_beets_id   ON music.library(beets_id);
CREATE INDEX idx_library_title_trgm ON music.library USING GIN(title gin_trgm_ops);
CREATE INDEX idx_library_artist_trgm ON music.library USING GIN(artist gin_trgm_ops);
CREATE INDEX idx_library_genre      ON music.library(genre);
CREATE INDEX idx_library_mood       ON music.library(mood);

-- ============================================================================
-- Full-text search view — unified search across sessions, songs, references, library
-- ============================================================================
CREATE OR REPLACE VIEW music.search_all AS
    SELECT 'session'   AS entity_type, id::TEXT, title, status, NULL::TEXT AS artist,
           created_at, array_to_string(mood,'|') AS mood_str, array_to_string(genre,'|') AS genre_str
    FROM music.sessions
    UNION ALL
    SELECT 'song',     id::TEXT, title, status, array_to_string(composer,'|'),
           created_at, array_to_string(mood,'|'), array_to_string(genre,'|')
    FROM music.songs
    UNION ALL
    SELECT 'reference', id::TEXT, title, status, artist,
           created_at, array_to_string(mood,'|'), array_to_string(genre,'|')
    FROM music.references
    UNION ALL
    SELECT 'library',  id::TEXT, title, 'finished', artist,
           imported_at, mood, genre
    FROM music.library;

-- ============================================================================
-- Annotations — running feedback/notes history, added 2026-08-21.
-- Deliberately append-only and polymorphic (entity_type + entity_id, no FK)
-- rather than the single overwritable `notes` column already on
-- sessions/songs/tracks/etc: the point is a timestamped trail of what was
-- flagged and when ("needs a better mix on the vocals" today, "fixed" next
-- week), not a single current-state blurb that loses history on every edit.
-- ============================================================================
CREATE TABLE IF NOT EXISTS music.annotations (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type  TEXT        NOT NULL,
    -- entity_id is TEXT, not UUID: added 'beets_track' below (Beets' own
    -- integer /item/<id> ids, e.g. "42") alongside the original UUID-based
    -- Postgres entities (stored as their string form) — one column can't
    -- be typed UUID and also hold a bare integer string.
    entity_id    TEXT        NOT NULL,
    note         TEXT        NOT NULL,
    category     TEXT        NOT NULL DEFAULT 'general'
                             CHECK (category IN ('improvement','mix','arrangement','lyrics','performance','general')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Widen an existing deployment's entity_id UUID -> TEXT (safe/no-op once
-- already TEXT) and refresh the entity_type CHECK to include 'beets_track'
-- — needed so notes can attach to actual playable Beets tracks, not just
-- Postgres session/song/etc rows. Both idempotent, safe to re-run.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'music' AND table_name = 'annotations'
          AND column_name = 'entity_id' AND data_type = 'uuid'
    ) THEN
        ALTER TABLE music.annotations ALTER COLUMN entity_id TYPE TEXT USING entity_id::TEXT;
    END IF;
END $$;

ALTER TABLE music.annotations DROP CONSTRAINT IF EXISTS annotations_entity_type_check;
ALTER TABLE music.annotations ADD CONSTRAINT annotations_entity_type_check
    CHECK (entity_type IN ('session','song','track','clip','reference','field_recording','library','beets_track'));

CREATE INDEX IF NOT EXISTS idx_annotations_entity ON music.annotations (entity_type, entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_annotations_recent ON music.annotations (created_at DESC);

-- ============================================================================
-- Track engagement — per-user current status/rating/notes on a playable
-- Beets track, added 2026-08-22. Mirrors podcast.home.example.org's
-- episode_engagement table exactly (see playback_api_server.py) — same
-- upsert-by-(item,user) shape, same "/ui/history = every engagement record
-- across the household, most recent first" idea. Deliberately separate
-- from music.annotations above: this is a single current-state snapshot
-- per user (like a star rating + review), not an append-only trail: the
-- point here is "what do I currently think of this take", not "here's
-- everything that's been said about it over time".
-- item_id is Beets' own integer /item/<id> id (music-beets:8337), not a
-- Postgres row — there is no FK, Beets' SQLite library is a separate store.
-- ============================================================================
CREATE TABLE IF NOT EXISTS music.track_engagement (
    item_id           INTEGER     NOT NULL,
    user_id           TEXT        NOT NULL DEFAULT 'owner',
    status            TEXT        NOT NULL DEFAULT 'new'
                                  CHECK (status IN ('new','reviewing','needs_work','approved')),
    rating            INTEGER     CHECK (rating BETWEEN 1 AND 5),
    notes             TEXT,
    position_seconds  INTEGER     DEFAULT 0,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (item_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_track_engagement_updated ON music.track_engagement (updated_at DESC);

-- ============================================================================
-- updated_at trigger (applied to all tables with that column)
-- ============================================================================
CREATE OR REPLACE FUNCTION music.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DO $$
DECLARE tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['sessions','songs','tracks','clips','field_recordings','references','library']
    LOOP
        EXECUTE format(
            'CREATE TRIGGER trg_%s_updated_at BEFORE UPDATE ON music.%s
             FOR EACH ROW EXECUTE FUNCTION music.set_updated_at()',
            tbl, tbl
        );
    END LOOP;
END;
$$;

-- ============================================================================
-- Stats / dashboard view
-- ============================================================================
CREATE OR REPLACE VIEW music.dashboard AS
SELECT
    (SELECT COUNT(*) FROM music.sessions)                          AS total_sessions,
    (SELECT COUNT(*) FROM music.sessions WHERE status='finished')  AS finished_sessions,
    (SELECT COUNT(*) FROM music.sessions WHERE status='draft')     AS draft_sessions,
    (SELECT COUNT(*) FROM music.songs)                             AS total_songs,
    (SELECT COUNT(*) FROM music.songs WHERE status='finished')     AS finished_songs,
    (SELECT COUNT(*) FROM music.songs WHERE is_original=false)     AS covers,
    (SELECT COUNT(*) FROM music.references)                        AS references_total,
    (SELECT COUNT(*) FROM music.references WHERE status='wishlist') AS wishlist,
    (SELECT COUNT(*) FROM music.references WHERE status='in_progress') AS covers_in_progress,
    (SELECT COUNT(*) FROM music.field_recordings WHERE status='inbox') AS field_inbox,
    (SELECT COUNT(*) FROM music.library)                           AS library_tracks,
    (SELECT COUNT(*) FROM music.tracks)                            AS total_tracks,
    (SELECT COUNT(*) FROM music.clips)                             AS total_clips,
    (SELECT COUNT(*) FROM music.plugins)                           AS plugins_cataloged;

COMMENT ON VIEW music.dashboard IS 'Music studio at-a-glance statistics';

-- ============================================================================
-- Audio identity — added 2026-10-08 (Musician Platform phase 0).
-- Beets' integer item ids are NOT stable: a re-scan renumbered a track from
-- 1 to 1326 (2026-09-04), and file paths changed when NASTY was retired for
-- nas-02/DATAGRID (2026-09-28). Everything the musician platform hangs off a
-- recording (chord/beat/section analysis, lyric alignment, song links) keys
-- on audio_files.id instead — identity by CONTENT HASH, so it survives both.
--
-- Hash = xxh3_128 over the whole file, hex — deliberately the same algorithm
-- and encoding labcat (consolidate.py) uses, so the first sync seeds from
-- labcat's existing DATAGRID full-hash index instead of re-reading ~127GB
-- over NFS. See audio_identity_sync.py.
--
-- Two tables, not one: the same content can sit at more than one path (the
-- archive has real copies of takes in several folders), and each path is
-- its own Beets item. audio_files = the content; audio_locations = where it
-- lives and which Beets item currently points at it.
-- Everything below is IF NOT EXISTS — safe to re-run every deploy.
-- ============================================================================
CREATE TABLE IF NOT EXISTS music.audio_files (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    hash_algo     TEXT        NOT NULL DEFAULT 'xxh3_128',
    content_hash  TEXT        NOT NULL,
    size_bytes    BIGINT      NOT NULL,
    duration_sec  NUMERIC(12,3),
    format        TEXT,
    sample_rate   INTEGER,
    channels      INTEGER,
    bit_depth     INTEGER,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hash_algo, content_hash)
);

CREATE TABLE IF NOT EXISTS music.audio_locations (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    audio_id       UUID        NOT NULL REFERENCES music.audio_files(id) ON DELETE CASCADE,
    path           TEXT        NOT NULL UNIQUE,   -- as seen inside the containers (/import/nasty/...)
    size_bytes     BIGINT      NOT NULL,
    mtime_ns       BIGINT      NOT NULL,          -- unchanged size+mtime = no re-hash
    hash_source    TEXT        NOT NULL CHECK (hash_source IN ('labcat','computed')),
    beets_item_id  INTEGER,                       -- CURRENT Beets id; refreshed every sync, never a key
    first_seen     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    missing_since  TIMESTAMPTZ                    -- set when the file is gone from disk/Beets; row kept for history
);

CREATE INDEX IF NOT EXISTS idx_audio_locations_audio ON music.audio_locations (audio_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_audio_locations_beets_item
    ON music.audio_locations (beets_item_id) WHERE beets_item_id IS NOT NULL;

-- Durable link from existing per-track data to content. item_id / entity_id
-- stay as-is (the UI still reads by Beets id); audio_id is filled in by the
-- sync and on every new write, so a Beets renumbering no longer orphans a
-- rating or a note — it can always be re-pointed from audio_id.
ALTER TABLE music.track_engagement ADD COLUMN IF NOT EXISTS audio_id UUID REFERENCES music.audio_files(id);
ALTER TABLE music.annotations      ADD COLUMN IF NOT EXISTS audio_id UUID REFERENCES music.audio_files(id);
CREATE INDEX IF NOT EXISTS idx_track_engagement_audio ON music.track_engagement (audio_id);
CREATE INDEX IF NOT EXISTS idx_annotations_audio      ON music.annotations (audio_id);

-- ============================================================================
-- Musician Platform phase 1 (2026-10-08): songs as the hub.
-- See IaC/docs/MUSICIAN_PLATFORM.md. Decisions: ChordPro lives in git
-- (Forgejo owner/songbook) — Postgres only INDEXES it (song_documents); song
-- membership is suggested by gpu-01's local LLM (song_suggest.py) and
-- confirmed by a human; files under 6 s are excluded from both.
-- Everything here is IF NOT EXISTS — safe to re-run every deploy.
-- ============================================================================

-- labcat reference per location (D4): content_hash already IS labcat's
-- full_hash; this is labcat's own path for the same file (file_index.path,
-- under labcat drive "zfs:DATAGRID/Multimedia"), filled by
-- audio_identity_sync.py.
ALTER TABLE music.audio_locations ADD COLUMN IF NOT EXISTS labcat_path TEXT;

ALTER TABLE music.songs ADD COLUMN IF NOT EXISTS slug          TEXT;
ALTER TABLE music.songs ADD COLUMN IF NOT EXISTS project       TEXT;
ALTER TABLE music.songs ADD COLUMN IF NOT EXISTS kind          TEXT;   -- original | cover | commercial | unknown
ALTER TABLE music.songs ADD COLUMN IF NOT EXISTS songbook_path TEXT;   -- folder in owner/songbook, e.g. songs/new-york-lights
CREATE UNIQUE INDEX IF NOT EXISTS uq_songs_slug ON music.songs (slug) WHERE slug IS NOT NULL;

-- LLM proposals awaiting a human decision. One row per proposed song, plus
-- one 'unassigned' row per folder holding the files the model left out (so
-- the review UI can still move them into a song).
CREATE TABLE IF NOT EXISTS music.song_suggestions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    folder      TEXT        NOT NULL,                 -- relative to the recordings root
    title       TEXT        NOT NULL,
    title_norm  TEXT        NOT NULL DEFAULT '',      -- for look-alike matching
    project     TEXT        NOT NULL DEFAULT '',
    kind        TEXT        NOT NULL CHECK (kind IN ('original','cover','commercial','unknown','unassigned')),
    confidence  NUMERIC(3,2),
    reason      TEXT,
    files       JSONB       NOT NULL,                 -- [{audio_id, path, role}]
    similar_to  UUID[]      NOT NULL DEFAULT '{}',    -- look-alike titles in other folders (hint only)
    status      TEXT        NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','accepted','rejected','merged','unassigned')),
    song_id     UUID        REFERENCES music.songs(id) ON DELETE SET NULL,
    model       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_song_suggestions_status ON music.song_suggestions (status, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_song_suggestions_folder ON music.song_suggestions (folder);

-- Which recordings belong to which song (keyed on content, not Beets ids).
CREATE TABLE IF NOT EXISTS music.song_recordings (
    song_id        UUID        NOT NULL REFERENCES music.songs(id) ON DELETE CASCADE,
    audio_id       UUID        NOT NULL REFERENCES music.audio_files(id),
    role           TEXT        NOT NULL DEFAULT 'take'
                               CHECK (role IN ('final_mix','mix','master','demo','take','stem','sketch','reference')),
    source         TEXT        NOT NULL DEFAULT 'manual' CHECK (source IN ('suggested','manual')),
    suggestion_id  UUID        REFERENCES music.song_suggestions(id) ON DELETE SET NULL,
    added_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (song_id, audio_id)
);
CREATE INDEX IF NOT EXISTS idx_song_recordings_audio ON music.song_recordings (audio_id);

-- Index of the song's files in the songbook repo. Git is the source of
-- truth; this is rebuilt from it (Forgejo push webhook + nightly).
CREATE TABLE IF NOT EXISTS music.song_documents (
    song_id     UUID        NOT NULL REFERENCES music.songs(id) ON DELETE CASCADE,
    repo_path   TEXT        NOT NULL,
    kind        TEXT        NOT NULL CHECK (kind IN ('chordpro','alphatex','guitarpro','musicxml','midi','pdf','other')),
    blob_sha    TEXT,                                  -- Forgejo content sha (optimistic locking on save)
    commit_sha  TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (song_id, repo_path)
);

-- ============================================================================
-- Musician Platform phase 2 (2026-10-08): recording analysis.
-- music-analysis worker (Vamp: Chordino, qm bar/beat + key + tempo,
-- Segmentino, NNLS tuning) over every recording >= 6 s, keyed on CONTENT
-- (audio_files.id) so duplicates are analysed once. Human corrections from
-- Sonic Visualiser are separate runs (source = 'human') and win per kind.
-- Everything here is IF NOT EXISTS — safe to re-run every deploy.
-- ============================================================================

-- One row per (recording, analysis version) for the machine batch, plus one
-- per human import. Doubles as the job queue (status), claimed with
-- FOR UPDATE SKIP LOCKED.
CREATE TABLE IF NOT EXISTS music.analysis_runs (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    audio_id     UUID        NOT NULL REFERENCES music.audio_files(id) ON DELETE CASCADE,
    source       TEXT        NOT NULL DEFAULT 'auto' CHECK (source IN ('auto','human')),
    version      TEXT        NOT NULL,                  -- worker ANALYSIS_VERSION, or 'sv:<kind>' for imports
    status       TEXT        NOT NULL DEFAULT 'queued'
                             CHECK (status IN ('queued','running','done','failed','skipped')),
    priority     INTEGER     NOT NULL DEFAULT 100,      -- lower first; songs' recordings get bumped
    attempts     INTEGER     NOT NULL DEFAULT 0,
    worker       TEXT,
    error        TEXT,
    timings      JSONB,                                 -- per-plugin ms
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_analysis_runs_auto ON music.analysis_runs (audio_id, version) WHERE source = 'auto';
CREATE INDEX IF NOT EXISTS idx_analysis_runs_queue ON music.analysis_runs (status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_analysis_runs_audio ON music.analysis_runs (audio_id);

-- Labelled, timed results: chords, sections, key changes. (Beats/bars are
-- arrays on audio_analysis — ~350 per song would dominate this table.)
CREATE TABLE IF NOT EXISTS music.analysis_events (
    run_id      UUID          NOT NULL REFERENCES music.analysis_runs(id) ON DELETE CASCADE,
    audio_id    UUID          NOT NULL REFERENCES music.audio_files(id) ON DELETE CASCADE,
    kind        TEXT          NOT NULL CHECK (kind IN ('chord','section','key')),
    t_start     NUMERIC(12,3) NOT NULL,
    t_end       NUMERIC(12,3),
    label       TEXT          NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analysis_events_audio ON music.analysis_events (audio_id, kind, t_start);
CREATE INDEX IF NOT EXISTS idx_analysis_events_run   ON music.analysis_events (run_id);

-- One summary row per recording (latest successful auto run) — what search
-- and the UI read. Global key = the key held longest; tempo = median.
CREATE TABLE IF NOT EXISTS music.audio_analysis (
    audio_id      UUID         PRIMARY KEY REFERENCES music.audio_files(id) ON DELETE CASCADE,
    run_id        UUID         NOT NULL REFERENCES music.analysis_runs(id) ON DELETE CASCADE,
    key           TEXT,
    key_share     NUMERIC(4,3),          -- fraction of the recording in that key
    tempo_bpm     NUMERIC(6,2),
    tuning_hz     NUMERIC(6,2),
    chords        TEXT[]       NOT NULL DEFAULT '{}',   -- distinct chord labels, by total duration
    n_sections    INTEGER,
    beats         REAL[],                -- seconds
    beat_numbers  SMALLINT[],            -- metrical position (1 = downbeat)
    bars          REAL[],                -- seconds
    peaks         REAL[],                -- waveform envelope, 0..1, ~1 point / 0.1 s (max 4000)
    peaks_rate    REAL,                  -- peaks per second
    analyzed_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audio_analysis_key ON music.audio_analysis (key);

-- Effective events: per (recording, kind), the latest human run if any,
-- otherwise the latest successful auto run.
CREATE OR REPLACE VIEW music.analysis_events_effective AS
WITH ranked AS (
    SELECT DISTINCT ON (e.audio_id, e.kind) e.audio_id, e.kind, r.id AS run_id, r.source
    FROM music.analysis_events e JOIN music.analysis_runs r ON r.id = e.run_id
    WHERE r.status = 'done'
    ORDER BY e.audio_id, e.kind, (r.source = 'human') DESC, r.finished_at DESC
)
SELECT e.audio_id, e.kind, e.t_start, e.t_end, e.label, k.source, k.run_id
FROM music.analysis_events e JOIN ranked k ON k.run_id = e.run_id AND k.kind = e.kind;

-- ============================================================================
-- Musician Platform phase 3 (2026-10-09): draft lead sheets.
-- One recording of a song -> Demucs vocals (stem-api, gpu-02) -> Whisper
-- word timestamps (gpu-02, VAD on) -> aligned with the recording's chords and
-- sections -> draft ChordPro. Drafts live HERE, not in the songbook repo:
-- they're machine output; a human opens one in the editor and saves it into
-- song.cho (that save is the commit). Queue = status, worked by the
-- music-analysis container one at a time.
-- ============================================================================
CREATE TABLE IF NOT EXISTS music.lead_sheet_drafts (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    song_id       UUID        NOT NULL REFERENCES music.songs(id) ON DELETE CASCADE,
    audio_id      UUID        NOT NULL REFERENCES music.audio_files(id) ON DELETE CASCADE,
    status        TEXT        NOT NULL DEFAULT 'queued'
                              CHECK (status IN ('queued','running','done','failed')),
    stage         TEXT,                       -- decode | separate | transcribe | align (while running)
    vocals_path   TEXT,                       -- cached stem under /music/stems/<audio_id>/vocals.flac
    voiced_share  NUMERIC(4,3),               -- share of 0.5 s windows above -40 dBFS in the vocal stem
    language      TEXT,
    whisper       JSONB,                      -- raw verbose_json (segments + words) for re-alignment
    chordpro      TEXT,
    error         TEXT,
    timings       JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lead_sheet_drafts_song  ON music.lead_sheet_drafts (song_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_sheet_drafts_queue ON music.lead_sheet_drafts (status, created_at);

-- Musician Platform phase 4 (2026-10-10): harmony + meter catalogue, built
-- around SONGS. Recording-level facts (audio_harmony) come from the stored
-- analysis; song-level facts (song_harmony) come from the human-corrected
-- song.cho when it has chords, else from the song's best recording; the
-- progression catalogue counts songs first, recordings second.
-- Chords are Roman numerals relative to the key in force at that moment,
-- so progressions compare across keys. Meter: the bar tracker always
-- assumes 4/4, so 3/4 vs 4/4 is decided from where chord changes fall.
-- Rebuilt by the catalog (POST /api/v1/harmony/rebuild): nightly, after a
-- sheet save, on demand. Everything IF NOT EXISTS.
-- ============================================================================
CREATE TABLE IF NOT EXISTS music.audio_harmony (
    audio_id          UUID         PRIMARY KEY REFERENCES music.audio_files(id) ON DELETE CASCADE,
    meter             TEXT,                  -- '3/4' | '4/4' | NULL (undecided)
    meter_confidence  NUMERIC(4,3),
    downbeat_phase    SMALLINT,              -- beat index (mod meter) of the downbeat
    lift3             NUMERIC(5,3),
    lift4             NUMERIC(5,3),
    chords            JSONB        NOT NULL, -- [[t, end, label, full_roman, simple_roman, key]] smoothed
    loops             JSONB        NOT NULL, -- [[t_start, t_end, [simple...], repeats, key, [chord labels...]]]
    version           TEXT         NOT NULL,
    computed_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS music.song_harmony (
    song_id       UUID         PRIMARY KEY REFERENCES music.songs(id) ON DELETE CASCADE,
    source        TEXT         NOT NULL CHECK (source IN ('sheet','recording')),
    audio_id      UUID         REFERENCES music.audio_files(id) ON DELETE SET NULL,
    key           TEXT,                       -- e.g. 'Am'
    meter         TEXT,
    meter_source  TEXT,                       -- 'sheet' | 'detected'
    sections      JSONB        NOT NULL,      -- [{label, t_start, chords:[...], roman:[...], loops:[[...]]}]
    loops         TEXT[]       NOT NULL DEFAULT '{}',   -- canonical patterns, e.g. 'I–V–vi–IV'
    computed_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_song_harmony_loops ON music.song_harmony USING GIN (loops);

CREATE TABLE IF NOT EXISTS music.progressions (
    pattern       TEXT         PRIMARY KEY,   -- canonical loop, e.g. 'i–bVI–bIII–bVII'
    mode          TEXT         NOT NULL,      -- 'major' | 'minor' (from the tonic numeral)
    length        SMALLINT     NOT NULL,
    songs         INTEGER      NOT NULL DEFAULT 0,
    recordings    INTEGER      NOT NULL DEFAULT 0,
    occurrences   INTEGER      NOT NULL DEFAULT 0,
    meters        JSONB        NOT NULL DEFAULT '{}',   -- {"4/4": n, "3/4": n}
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_progressions_rank ON music.progressions (songs DESC, recordings DESC);

CREATE TABLE IF NOT EXISTS music.progression_hits (
    pattern   TEXT          NOT NULL REFERENCES music.progressions(pattern) ON DELETE CASCADE,
    audio_id  UUID          REFERENCES music.audio_files(id) ON DELETE CASCADE,
    song_id   UUID          REFERENCES music.songs(id) ON DELETE CASCADE,
    t_start   NUMERIC(12,3),
    t_end     NUMERIC(12,3),
    repeats   SMALLINT,
    key       TEXT,
    chords    TEXT,                            -- as played, e.g. 'Am F C G'
    section   TEXT
);
CREATE INDEX IF NOT EXISTS idx_progression_hits_pattern ON music.progression_hits (pattern);
CREATE INDEX IF NOT EXISTS idx_progression_hits_song ON music.progression_hits (song_id);


-- ============================================================================
-- Studio partner (2026-10-24): DOMO in the studio. Every Live set under
-- My Recordings is a music.sessions row; each save a snapshot (als_reader
-- summary) and a 'save' event saying what changed; notes/ideas/to-dos said to
-- DOMO and studio-session summaries are events too. studio_files links a set
-- to its audio in the catalogue (audio_locations). Written by studio_partner.py.
-- UUID keys like the rest of the schema: the app user is granted table rights
-- only, not sequences.
-- ============================================================================
CREATE TABLE IF NOT EXISTS music.studio_snapshots (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID        NOT NULL REFERENCES music.sessions(id) ON DELETE CASCADE,
    als_path    TEXT        NOT NULL,                 -- the set file read (a Backup/ copy for history)
    mtime_ns    BIGINT      NOT NULL,
    size_bytes  BIGINT      NOT NULL,
    saved_at    TIMESTAMPTZ NOT NULL,                 -- the file's own save time
    source      TEXT        NOT NULL CHECK (source IN ('save','backup')),
    summary     JSONB       NOT NULL,
    UNIQUE (als_path, mtime_ns)
);
CREATE INDEX IF NOT EXISTS idx_studio_snapshots_session ON music.studio_snapshots (session_id, saved_at);

CREATE TABLE IF NOT EXISTS music.studio_events (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID        REFERENCES music.sessions(id) ON DELETE CASCADE,   -- NULL = not filed under a set
    at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    kind        TEXT        NOT NULL CHECK (kind IN ('save','note','idea','todo','talk')),
    text        TEXT        NOT NULL,
    detail      JSONB,
    snapshot_id UUID        REFERENCES music.studio_snapshots(id) ON DELETE SET NULL,
    speaker     TEXT,
    done        BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_studio_events_session ON music.studio_events (session_id, at);
CREATE INDEX IF NOT EXISTS idx_studio_events_at ON music.studio_events (at);

CREATE TABLE IF NOT EXISTS music.studio_files (
    session_id  UUID        NOT NULL REFERENCES music.sessions(id) ON DELETE CASCADE,
    path        TEXT        NOT NULL,                 -- container path (/import/nasty/...)
    role        TEXT        NOT NULL,   -- project-audio | sample | outside (check below, widened 2026-10-25)
    audio_id    UUID        REFERENCES music.audio_files(id) ON DELETE SET NULL,
    present     BOOLEAN     NOT NULL,
    PRIMARY KEY (session_id, path)
);

CREATE TABLE IF NOT EXISTS music.studio_talks (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    speaker     TEXT        NOT NULL,
    session_id  UUID        REFERENCES music.sessions(id) ON DELETE SET NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at    TIMESTAMPTZ,
    turns       JSONB       NOT NULL DEFAULT '[]',
    summary     TEXT,
    status      TEXT        NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed'))
);
CREATE INDEX IF NOT EXISTS idx_music_sessions_project_file ON music.sessions (project_file);
-- 2026-10-25: 'outside' = a file the set uses that is NOT inside its project folder -- the
-- "files all over the place" case the Mac's `studio done` refuses to check in.
ALTER TABLE music.studio_files DROP CONSTRAINT IF EXISTS studio_files_role_check;
ALTER TABLE music.studio_files ADD CONSTRAINT studio_files_role_check
    CHECK (role IN ('project-audio','sample','outside'));

-- ============================================================================
-- Field recorders (2026-10-25, field_ingest.py): Zoom H2n / Tascam (handheld)
-- and Zoom R16 (multitrack). music.field_recordings gains what the WAV header
-- and the import know; music.field_sessions groups takes into music.sessions
-- (handheld: new session after a 30-min gap, per recorder; R16: one project = one
-- live session; a handheld session may attach to an R16 one, never the reverse).
-- ============================================================================
ALTER TABLE music.field_recordings
    ADD COLUMN IF NOT EXISTS audio_id        UUID REFERENCES music.audio_files(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS kind            TEXT CHECK (kind IN ('handheld','multitrack')),
    ADD COLUMN IF NOT EXISTS take            TEXT,          -- one take (an R16 project; H2n 4CH MS+XY share one)
    ADD COLUMN IF NOT EXISTS track           TEXT,          -- 'track 3' (R16), 'MS'/'XY' (H2n 4CH), NULL = stereo take
    ADD COLUMN IF NOT EXISTS clock_ok        BOOLEAN,       -- recorded_at from a set recorder clock
    ADD COLUMN IF NOT EXISTS recorder        TEXT,          -- bext originator as written by the device
    ADD COLUMN IF NOT EXISTS channels        SMALLINT,
    ADD COLUMN IF NOT EXISTS sample_rate     INTEGER,
    ADD COLUMN IF NOT EXISTS bits            SMALLINT,
    ADD COLUMN IF NOT EXISTS size_bytes      BIGINT,
    ADD COLUMN IF NOT EXISTS mtime_ns        BIGINT,
    ADD COLUMN IF NOT EXISTS batch           TEXT,          -- Field Recordings/<device>/<batch> for new imports
    ADD COLUMN IF NOT EXISTS spoken_note     TEXT,          -- Whisper of the first 20 s ("take three, chorus idea")
    ADD COLUMN IF NOT EXISTS spoken_tried_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS field_session   TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_field_recordings_path ON music.field_recordings (file_path);

CREATE TABLE IF NOT EXISTS music.field_sessions (
    key          TEXT        PRIMARY KEY,                  -- 'm:<R16 project dir>' | 'h:<device>:<start or folder>'
    session_id   UUID        NOT NULL REFERENCES music.sessions(id) ON DELETE CASCADE,
    kind         TEXT        NOT NULL CHECK (kind IN ('handheld','multitrack')),
    device       TEXT        NOT NULL,
    starts_at    TIMESTAMPTZ,
    ends_at      TIMESTAMPTZ,
    attached_to  TEXT        REFERENCES music.field_sessions(key) ON DELETE SET NULL,   -- handheld -> R16 only
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_field_recordings_session ON music.field_recordings (field_session);

-- ============================================================================
-- Practice (2026-10-31, practice.py + studio_partner.py): ear training and
-- keyboard drills at studio.home.example.org/ui/practice. One row per answer; the
-- exercise (with its grading spec) is kept so a miss can be looked at again.
-- Weighting reads the last tries per item; domo-winddown asks studio-partner
-- for tonight's count to tick "play an instrument".
-- ============================================================================
CREATE TABLE IF NOT EXISTS music.practice_attempts (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    speaker     TEXT        NOT NULL,
    at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deck        TEXT        NOT NULL,
    level       SMALLINT    NOT NULL,
    item        TEXT        NOT NULL,              -- 'P5 down', 'Ebm', 'ii–V–I in F major'
    correct     BOOLEAN     NOT NULL,
    hint_used   BOOLEAN     NOT NULL DEFAULT FALSE,   -- "Show me" pressed: counts as a miss for weighting
    ms          INTEGER,                           -- time to answer
    answer      JSONB,
    exercise    JSONB       NOT NULL,
    result      JSONB
);
CREATE INDEX IF NOT EXISTS idx_practice_speaker_item ON music.practice_attempts (speaker, deck, item, at DESC);
CREATE INDEX IF NOT EXISTS idx_practice_speaker_at ON music.practice_attempts (speaker, at);
