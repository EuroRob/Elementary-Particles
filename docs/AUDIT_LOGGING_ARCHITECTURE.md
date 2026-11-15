---
type: Topic
state: planned — not started
ep_tier: concierge
ep_particle: audit-log
belongs_to: "[[elementary-particles]]"
related_to:
  - "[[project_autonomous_agent_handoff]]"
  - "[[project_serge_per_person_instances]]"
  - "SOLUTION_OVERVIEW.md"
---

# Concierge Audit Logging — design + build plan

**Status: planned, nothing built.** Raised 2026-09-15 as a direct
consequence of pitch-deck fact-checking (`SOLUTION_OVERVIEW.md` /
Fable's "Lake House" deck): slides 5, 7, and 11 all state or demo a
per-person audit log ("every tool call is logged and attributable",
"What did the AI access today?" as a live demo, "audit log per
person" as a DSGVO compliance feature). **None of this exists today.**
MCP servers log to their own container stdout for debugging only — no
structured store, nothing queryable, no per-person view. This doc is
the real plan to close that gap, written in enough detail to actually
build from, not just gesture at.

**Revised same day** with a second round of Fable feedback, now
folded in: the schema gained explicit `operation` (read/write) and
`resource_id` columns (the minimum field set needed for the
compliance story to hold with a Steuerberater/Rechtsanwalt audience),
and the storage design gained real tamper-resistance — two Postgres
roles (`concierge_audit_writer`, INSERT-only; `concierge_audit_admin`,
the only one with DELETE) so a compromised agent structurally cannot
edit its own trail, not just by policy. See "Append-only, by privilege
not by promise" below. Once this ships, this project is what turns
the deck's 📋 marks on slides 5/7/11 into 🔧-in-progress-with-a-target-
date now, and ✅ later — that status/date decision belongs on the deck
itself, not here.

## Difficulty assessment (short answer, asked directly)

**Medium, not hard.** Every individual piece is a pattern this repo
already has proven working somewhere else — dedicated Postgres DB +
user (Immich, Outline, every Layer-4 app), a shared Python library
imported by multiple FastMCP servers (the existing `outline_mcp.py`/
`wger_mcp.py`/`serge_nextcloud_mcp.py` already share no code today,
which is itself part of the work), Vault-sourced credentials, Ansible
per-instance parameterization (`serge_nextcloud_mcp.yml`'s own
pattern), n8n cron cleanup jobs, Grafana dashboards on a Postgres
datasource. Nothing here is novel technology. The real cost is
**fan-out**: touching every existing MCP server, redeploying each
(brief restart per service, same pattern as every other MCP server
change this session), and making one real architectural decision
(below) about `outline-mcp`, which is currently shared across people
and has no way to attribute a call to one person at all.

Rough sizing, for planning purposes only (not a committed estimate):
comparable in scope to the wger-mcp + workout_journal effort from
earlier this week, but touching 3 existing codebases instead of
building 1 new one, plus a new small piece of infrastructure (the
audit DB + retention job). A few real working sessions, not a
one-afternoon fix — mostly because of the "touch N existing services
carefully, one at a time, verify each still works" nature of the
rollout, not because any single piece is hard.

## Scope decision: tool-call auditing, not full conversation logging

Two different things could be meant by "audit log":
1. **Every MCP tool call** (which tool, which person, when, did it
   succeed) — this is what the pitch deck's claims actually describe
   ("every tool call is logged and attributable", "what did the AI
   access today").
2. Full conversation transcripts (everything said to/from the agent).

**This design is scoped to (1) only.** Full transcript logging is a
much bigger privacy/storage/scope question — it would mean the audit
store becomes a second copy of everything anyone ever said to their
concierge, which cuts against the platform's own "your data stays
minimal, stays yours" positioning. If conversation-level logging is
wanted later, it's a separate, explicitly-scoped follow-up project,
not bundled into this one by default.

## Current real state (facts, not assumptions)

- Three MCP server codebases exist: `integration/serge_nextcloud_mcp.py`,
  `integration/wger_mcp.py`, `integration/outline_mcp.py` — all
  FastMCP-based, all using the same `@mcp.tool()` decorator shape,
  **none share a common library today**.
- `serge_nextcloud_mcp.py` and `wger_mcp.py` are deployed **per-person**
  (separate container per person, separate Vault credential, separate
  port — admin and Partner each get their own instance,
  `Roles/gpu/tasks/serge_nextcloud_mcp.yml` /
  `Roles/gpu/tasks/wger_mcp.yml` are parameterized for this already).
  A given instance's identity is knowable from its own deployment
  config (e.g. `serge_mcp_instance: partner`).
- **`outline_mcp.py` is deployed exactly once, shared across everyone**
  (`Roles/gpu/tasks/outline_mcp.yml`, gpu-01 only, no per-person
  variant, one Vault token). It has no way today to know which person
  is calling it — both admin's and Partner's OpenClaw instances point at
  the same server. **This is the one real architectural decision this
  project needs to make** (see below) — everything else is additive.
- Postgres (pg-01) already hosts one dedicated database per
  application, each with its own app user, via the `postgres` role's
  established `postgres_databases` loop pattern
  (`Ansible/Roles/postgres/tasks/users.yml`).
- Vault, n8n, and Grafana (with Postgres datasource support, already
  used for the disk-space dashboard) are all live and reusable as-is.

## Architecture decision: fix outline-mcp's identity gap by making it per-person too

The wiki content is identical either way (Outline doesn't have
separate per-person permissions in this setup), but for audit
attribution to work, **deploy `outline-mcp` a second time as
`outline-mcp-partner`**, exactly mirroring how `serge_nextcloud_mcp.yml`
and `wger_mcp.yml` already handle this — same Vault token (no new
credential needed, Outline access itself doesn't change), different
container/port, different `AUDIT_PERSON_ID`. This is the cheapest
possible fix and keeps the "one server = one identity" invariant true
everywhere, which is what makes the rest of this design simple. The
alternative (having OpenClaw pass caller identity through to a shared
server) would require protocol-level changes upstream in OpenClaw
itself — out of scope, and undermines the "isolated by construction"
security story the pitch deck is making in the first place.

## Data model

New dedicated database on pg-01: `concierge_audit`. **Two DB roles,
not one** (see "Append-only, by privilege not by promise" below for
why) — Vault: `secret/concierge-audit/writer` (`user`, `password`) and
`secret/concierge-audit/admin` (`user`, `password`), following the
same per-app dedicated-credential pattern every other app DB uses,
just two credentials instead of the usual one.

```sql
CREATE TABLE tool_calls (
    id              BIGSERIAL PRIMARY KEY,
    person_id       TEXT NOT NULL,        -- 'admin' | 'partner' | ... (matches AUDIT_PERSON_ID)
    agent_instance  TEXT NOT NULL,        -- which OpenClaw gateway made the call, e.g. 'openclaw-admin' -- currently 1:1 with person_id, kept as its own field for when that stops being true (see open questions)
    server_name     TEXT NOT NULL,        -- 'serge-nextcloud-mcp', 'wger-mcp', 'outline-mcp-partner', ...
    tool_name       TEXT NOT NULL,        -- 'calendar_list_events', 'notes_write', ...
    operation       TEXT NOT NULL CHECK (operation IN ('read', 'write')),
    resource_id     TEXT,                 -- the specific thing touched: calendar event UID, note ID, file path, bookmark URL -- an IDENTIFIER, not content, so it's fine to store plainly. This is the field that turns "we log" into "we can show an auditor exactly what the AI touched."
    channel         TEXT,                 -- 'voice-chat' | 'nextcloud-talk' | NULL if unknown
    called_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    duration_ms     INTEGER,
    success         BOOLEAN NOT NULL,
    error_summary   TEXT,                 -- short, sanitized -- never a raw stack trace (may contain paths/secrets)
    args_summary    JSONB,                -- REDACTED per-tool policy, see below -- never raw content, only shape/metadata beyond what resource_id already captures
    result_summary  TEXT                  -- short human-readable, e.g. "3 events returned", not the full payload
);

CREATE INDEX idx_tool_calls_person_time ON tool_calls (person_id, called_at DESC);
CREATE INDEX idx_tool_calls_time ON tool_calls (called_at DESC);  -- for retention cleanup + admin view
```

**Why summaries, not raw payloads**: storing the full arguments/results
of every tool call would make this database a second, growing copy of
real personal data (calendar contents, notes text, fitness numbers) —
directly working against the "minimal footprint, nothing leaves the
building unnecessarily" story. The audit log should answer "what did
it touch and when", not "what exactly did it read" in full — but
*which specific thing* (the identifier) is not content and belongs in
its own first-class column, not buried in a JSON blob, so it's
directly queryable and scannable by an auditor: "show me every write
this agent made to calendar X in the last 30 days" should be one
`WHERE` clause, not JSONB traversal.

**Minimum field set for the compliance story to hold** (per
2026-09-15 feedback, aimed at a Steuerberater/Rechtsanwalt audience):
person, timestamp, agent instance, MCP server, tool name, resource
touched, read-or-write, outcome. All present in the schema above.

## Append-only, by privilege not by promise

**The audit log itself is personal data under DSGVO** — a record of
what a specific person's agent did, tied to that person, is personal
data about them, not a neutral technical log. That has two real
consequences beyond "store it somewhere": it needs the same
per-person access scoping as everything else in this platform (already
designed below, via the self-service tool + admin-only Grafana view),
and it needs to be genuinely tamper-resistant — **a compromised or
prompt-injected agent must not be able to edit or delete its own
trail**, or the audit log stops being evidence and becomes theater.

The cheap, idiomatic fix for this doesn't need a separate storage
system — Postgres privilege separation is enough:

```sql
-- Writer role: used by AUDIT_DB_DSN in every MCP server's environment.
-- Can INSERT new rows. Cannot UPDATE or DELETE existing ones -- not
-- even its own. This is the entire tamper-resistance mechanism: even
-- a fully compromised MCP server process, using this exact credential,
-- cannot rewrite history.
CREATE ROLE concierge_audit_writer LOGIN PASSWORD '...';
GRANT INSERT, SELECT ON tool_calls TO concierge_audit_writer;
GRANT USAGE, SELECT ON SEQUENCE tool_calls_id_seq TO concierge_audit_writer;
-- SELECT is included so the self-service audit_recent_activity tool can
-- read back a person's own rows -- it's UPDATE/DELETE that's withheld,
-- since those are what "tamper" means here. Application code (the
-- audit_recent_activity tool) still enforces the person_id WHERE clause;
-- the DB grant is the second, structural layer, not the only one.

-- Admin role: used ONLY by the n8n retention job and the Grafana
-- datasource. Never referenced by any MCP server or agent-facing
-- code path.
CREATE ROLE concierge_audit_admin LOGIN PASSWORD '...';
GRANT SELECT, DELETE ON tool_calls TO concierge_audit_admin;
```

This satisfies the "append-only" requirement without a new piece of
infrastructure to operate. A genuinely separate write-once store (a
dedicated log-shipping target, an external SIEM, object-lock storage)
would be *stronger* — it survives even a compromise of the Postgres
instance itself, which privilege separation alone doesn't — and is
worth doing if this becomes a real product sold to a
professional-secrecy client who needs that guarantee contractually.
For the MVP and for making the current pitch-deck claims true, the
two-role design above is the right scope: cheap, real, and defensible
in an auditor conversation ("the agent's own credential cannot modify
past entries — verify this yourself, it's a Postgres GRANT, not a
policy we're asking you to trust").

### Redaction policy (per-tool, decided once, applied consistently)

Each tool declares three things, not one: its `operation`
(`read`/`write`), how to pull a `resource_id` out of its arguments or
return value (an identifier — always safe to store plainly), and how
to build `args_summary` for anything beyond that identifier (redacted
per the rules below).

| Tool category | `operation` | `resource_id` source | `args_summary` example | Rule |
|---|---|---|---|---|
| Read (`calendar_list_events`, `notes_list`, `wger_recent_workouts`) | `read` | not applicable for list/range calls — NULL, the query itself is described by `args_summary` | `{"range": "2026-09-15..2026-09-16"}` | Keep query parameters (dates, filters, limits) — these aren't sensitive content, they're metadata about the request |
| Read, single-resource (`notes_read`, `outline_get_document`) | `read` | the note/document ID from the arguments | `{}` (the ID alone already answers "what did it touch") | — |
| Write (`notes_write`, `calendar_create_event`, `bookmarks_add`) | `write` | the note/event ID from arguments or the created ID from the result | `{"length_chars": 240}` | Identifier goes in `resource_id`, never in `args_summary`; keep shape (length, count) alongside it, **never the content itself** |
| Search (`outline_search`, `contacts_search`) | `read` | NULL — a search has no single resource | `{"query_length": 12}` or a query-term hash | Search queries can themselves be sensitive (what someone was looking for) — log shape, not the literal query, unless a real debugging need justifies more |

This table itself should live in the shared library (below), not be
re-decided per MCP server — one place to update if the policy changes.

## Shared library

New file: `integration/mcp_audit.py` — imported by all three (four,
after the outline-mcp split) MCP servers. Not a new service, just a
shared module deployed alongside each server (same `copy:` task
pattern already used to ship `wger_mcp.py` + its requirements file).

```python
# integration/mcp_audit.py
import os, time, json, functools
import psycopg2
from psycopg2.extras import Json

# Writer credential ONLY -- INSERT-only grant (see "Append-only, by
# privilege not by promise"). This module must never be given the
# admin DSN; there is no code path here that could use DELETE/UPDATE
# even if it had it, by design.
AUDIT_DB_DSN = os.environ.get("AUDIT_DB_DSN")  # empty/unset -> audit logging silently disabled
AUDIT_PERSON_ID = os.environ.get("AUDIT_PERSON_ID", "unknown")
AUDIT_AGENT_INSTANCE = os.environ.get("AUDIT_AGENT_INSTANCE", AUDIT_PERSON_ID)
AUDIT_SERVER_NAME = os.environ.get("AUDIT_SERVER_NAME", "unknown")

def audit_logged(operation: str, resource_id=lambda kw, result: None, redact=lambda kw: {}):
    """Decorator for an @mcp.tool() function.
    operation: 'read' or 'write', per the redaction policy table.
    resource_id: fn(kwargs, result) -> str | None -- the identifier
        touched (note ID, event UID, file path). Safe to store plainly;
        this is what makes "what did it touch" answerable.
    redact: fn(kwargs) -> dict -- everything else, sanitized per the
        redaction policy table. Never raw content.
    """
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            start = time.monotonic()
            success, error_summary, result = True, None, None
            try:
                result = await fn(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error_summary = f"{type(e).__name__}: {str(e)[:200]}"
                raise
            finally:
                _write_audit_row(
                    tool_name=fn.__name__,
                    operation=operation,
                    resource_id=resource_id(kwargs, result if success else None),
                    duration_ms=int((time.monotonic() - start) * 1000),
                    success=success,
                    error_summary=error_summary,
                    args_summary=redact(kwargs),
                    result_summary=_summarize(result),
                )
        return wrapper
    return decorator

def _write_audit_row(**fields):
    """Best-effort only -- audit logging must NEVER break or block a
    real tool call. Swallow all errors, never raise, never retry
    inline (a down audit DB should degrade to 'no audit log today',
    not 'concierge stops working'). Uses the writer (INSERT-only)
    credential -- structurally incapable of touching past rows."""
    if not AUDIT_DB_DSN:
        return
    try:
        with psycopg2.connect(AUDIT_DB_DSN, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO tool_calls
                       (person_id, agent_instance, server_name, tool_name,
                        operation, resource_id, duration_ms, success,
                        error_summary, args_summary, result_summary)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (AUDIT_PERSON_ID, AUDIT_AGENT_INSTANCE, AUDIT_SERVER_NAME,
                     fields["tool_name"], fields["operation"], fields["resource_id"],
                     fields["duration_ms"], fields["success"],
                     fields["error_summary"], Json(fields["args_summary"]),
                     fields["result_summary"]),
                )
    except Exception:
        pass  # deliberately silent -- see docstring

def _summarize(result) -> str:
    if result is None:
        return "no result"
    if isinstance(result, list):
        return f"{len(result)} item(s) returned"
    if isinstance(result, dict):
        return "object returned"
    return "ok"
```

Each MCP server then wraps its own tools, one line added per tool —
now declaring operation type and where the resource ID comes from,
per the redaction policy table:

```python
# in wger_mcp.py, for example
from mcp_audit import audit_logged

@mcp.tool()
@audit_logged(operation="read", redact=lambda kw: {"limit": kw.get("limit")})
async def wger_recent_workouts(limit: int = 10) -> list[dict]:
    ...

# in serge_nextcloud_mcp.py -- a write tool, resource_id comes from the result
@mcp.tool()
@audit_logged(
    operation="write",
    resource_id=lambda kw, result: result.get("note_id") if result else None,
    redact=lambda kw: {"length_chars": len(kw.get("content", ""))},
)
async def notes_write(note_id: str, content: str) -> dict:
    ...
```

**Resilience note**: `psycopg2.connect` with a 2-second timeout, and
the whole write is in a `try/except: pass` — an audit-DB outage
degrades to "no logging right now," never to "the concierge is down."
This matters more here than in most of this repo's other Postgres
uses, because audit logging is explicitly NOT allowed to become a new
single point of failure for daily usability — that would be a worse
outcome than the current "no audit log at all" state.

## Self-service "what did you access today" tool

To make slide 7's demo literally true, add one new tool to each
per-person MCP server (or a tiny new dedicated one,
`audit-self-mcp`, deployed per-person like the others) that lets a
person's OWN agent answer the question by querying only that person's
own rows:

```python
@mcp.tool()
async def audit_recent_activity(hours: int = 24) -> list[dict]:
    """What tools has your own concierge used recently, and on what.
    Scoped to AUDIT_PERSON_ID only -- cannot see anyone else's activity,
    enforced by this query, not by trust. Uses the writer credential's
    read access (INSERT-only doesn't preclude SELECT -- grant SELECT
    to the writer role too, scoped by this WHERE clause in application
    code; the writer role still cannot UPDATE/DELETE)."""
    # SELECT server_name, tool_name, operation, resource_id, called_at,
    #        success, result_summary
    # FROM tool_calls WHERE person_id = %s AND called_at > now() - interval '%s hours'
    # ORDER BY called_at DESC
```

This needs adding to each person's `tools.allow` list in their
`openclaw.json` (same mechanical step already done repeatedly this
session for wger/outline). The demo becomes real: person asks their
agent "what did you access today", the agent calls this tool, reads
back its own real audit trail.

## Admin / household-owner view

A Grafana dashboard on a new Postgres datasource pointed at
`concierge_audit` (same pattern as the existing disk-space dashboard) —
table panel filtered/templated by `person_id` variable, time-range
picker for "today" / "this week". Reuses existing Authelia SSO +
Grafana access control for who's allowed to see the admin (all-people)
view versus a single person's own. No new UI to build for the MVP —
the self-service MCP tool covers the demo, Grafana covers the
compliance/admin story.

## Retention

Data minimization matters here specifically because this is being
pitched as a DSGVO-forward product, and because the audit log is
itself personal data (see above) — an audit log with no retention
policy is itself a compliance smell. New n8n scheduled workflow
(matching the existing daily/weekly cron precedent, e.g. Hometown News's
06:00 schedule or the proposed weekly VM-disposal cron), using the
**admin** credential (the only place `DELETE` is granted), default 90
days:

```sql
DELETE FROM tool_calls WHERE called_at < now() - interval '90 days';
```

Retention window should be a documented, deliberate choice (not just
this default), and probably configurable per deployment once this
becomes a real product feature — noted here as a decision to make
explicit later, not resolved in this doc.

## Deployment plan (Ansible)

New role or task file, `Roles/gpu/tasks/audit_log.yml` (or a new
top-level `Roles/audit_log/` if it grows beyond gpu-01-local
concerns — the Postgres DB creation itself belongs on pg-01's role,
not gpu-01's):

1. **pg-01** (`Roles/postgres/tasks/users.yml` or a new
   `audit.yml` sibling): add `concierge_audit` to the
   `postgres_databases` loop, create the **two** dedicated roles
   (`concierge_audit_writer`, `concierge_audit_admin` — not the usual
   single app user, see "Append-only, by privilege not by promise"),
   write creds to Vault `secret/concierge-audit/writer` and
   `secret/concierge-audit/admin` separately. Run the `CREATE TABLE`
   and both `CREATE ROLE`/`GRANT` statements above via a
   `community.postgresql.postgresql_query` task or a checked-in `.sql`
   file. **Only the writer DSN ever reaches an MCP server's
   environment** — the admin DSN goes to the n8n retention workflow
   and the Grafana datasource config, nowhere else.
2. **integration/**: add `mcp_audit.py` alongside the existing MCP
   server files.
3. **Each existing MCP server task file**
   (`serge_nextcloud_mcp.yml`, `wger_mcp.yml`, `outline_mcp.yml`):
   - Add `mcp_audit.py` to the `Copy app code` loop.
   - Add `AUDIT_DB_DSN` (built from the new Vault secret, same
     pattern as every other DB-backed role), `AUDIT_PERSON_ID`, and
     `AUDIT_SERVER_NAME` to each container's `env:` block.
   - This means every existing MCP server gets **redeployed** as part
     of this rollout — expect the same brief-restart pattern hit
     repeatedly this session (immich/jellyfin), budget for verifying
     each service's health immediately after, one at a time, not all
     at once.
4. **New**: `outline_mcp.yml` gains the same `wger_mcp_instance`-style
   parameterization `serge_nextcloud_mcp.yml`/`wger_mcp.yml` already
   have, and `Roles/gpu/tasks/main.yml` gets a second import for
   `outline-mcp-partner` (mirrors the existing wger-mcp-partner entry
   exactly).
5. **Each MCP server's source file**: add the `@audit_logged(...)`
   decorator to every `@mcp.tool()` function, with a redaction
   function per the policy table above. Mechanical but needs care per
   tool (write tools especially — don't accidentally log content).
6. **Each person's `openclaw.json`**: add `audit_recent_activity` (or
   the new dedicated server, if built that way) to `tools.allow`.
7. **n8n**: new scheduled workflow for the 90-day retention delete.
8. **Grafana**: new Postgres datasource + dashboard for the admin
   view.

## Rollout order (verify each step before the next)

1. pg-01 DB + table, verified via a manual `psql` connection — no
   application changes yet, zero risk.
2. `mcp_audit.py` written + unit-testable in isolation (mock the DSN,
   confirm the decorator doesn't break a plain function when
   `AUDIT_DB_DSN` is unset — this is the "must never break the tool
   call" guarantee, worth an explicit test).
3. Wire into **one** MCP server first (suggest `wger-mcp` — smallest,
   3 tools, lowest blast radius if something's wrong) end to end:
   deploy, make a real tool call, confirm a row lands in Postgres with
   correctly redacted args.
4. Once proven, roll the same pattern into `serge_nextcloud_mcp.py`
   (both instances) and the split `outline-mcp`/`outline-mcp-partner`.
5. Add the self-service `audit_recent_activity` tool + `tools.allow`
   entries, test the actual demo script end to end (voice: "what did
   you access today").
6. Retention job + Grafana dashboard last — not needed for the demo
   to work, lower urgency.

## Open questions, not resolved by this doc

- Exact retention window (90 days used as a placeholder above).
- Whether `audit_recent_activity` should be its own tiny dedicated MCP
  server (cleaner separation) or a tool bolted onto each existing
  per-person server (less deployment overhead) — leaning toward the
  latter for the MVP, revisit if this becomes a real product.
- Whether OpenClaw's own gateway-level request log (separate from MCP
  tool calls — the chat completions themselves) has anything worth
  correlating in here later. Out of scope for now per the tool-call-only
  decision above.
