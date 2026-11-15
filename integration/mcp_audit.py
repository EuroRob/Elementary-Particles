"""
mcp_audit — shared audit-logging library for every per-person MCP tool
server in this fleet (serge-nextcloud-mcp, wger-mcp, outline-mcp, and
whatever comes next).

Design: IaC/docs/AUDIT_LOGGING_ARCHITECTURE.md — read that first for the
full schema, redaction policy, and the tamper-resistance design (two
Postgres roles; this module only ever holds the INSERT/SELECT-only
"writer" credential, never the admin one).

Usage, in any MCP server:

    from mcp_audit import audit_logged

    @mcp.tool()
    @audit_logged(operation="read", redact=lambda kw: {"limit": kw.get("limit")})
    async def some_read_tool(limit: int = 10) -> list[dict]:
        ...

    @mcp.tool()
    @audit_logged(
        operation="write",
        resource_id=lambda kw, result: result.get("note_id") if result else None,
        redact=lambda kw: {"length_chars": len(kw.get("content", ""))},
    )
    async def some_write_tool(note_id: str, content: str) -> dict:
        ...

Hard guarantee this module exists to provide: audit logging must NEVER
break or block a real tool call. Every failure mode here is swallowed
silently — a down or unreachable audit DB degrades to "no audit log
right now", never to "the concierge stopped working". This is checked
by the caller (each MCP server) getting the tool's real return value or
real exception back untouched; only the logging side effect can fail.
"""

import functools
import os
import time
from typing import Callable, Optional

import psycopg2
from psycopg2.extras import Json

# Writer credential ONLY (secret/concierge-audit/writer) -- INSERT+SELECT
# grant, no UPDATE/DELETE. This module must never be pointed at the admin
# DSN; there is no code path here that would use DELETE/UPDATE even if it
# had it, by design. See AUDIT_LOGGING_ARCHITECTURE.md's "Append-only, by
# privilege not by promise" section.
AUDIT_DB_DSN = os.environ.get("AUDIT_DB_DSN")  # empty/unset -> audit logging silently disabled
AUDIT_PERSON_ID = os.environ.get("AUDIT_PERSON_ID", "unknown")
AUDIT_AGENT_INSTANCE = os.environ.get("AUDIT_AGENT_INSTANCE", AUDIT_PERSON_ID)
AUDIT_SERVER_NAME = os.environ.get("AUDIT_SERVER_NAME", "unknown")

_CONNECT_TIMEOUT_SECONDS = 2


def audit_logged(
    operation: str,
    resource_id: Callable[[dict, Optional[object]], Optional[str]] = lambda kw, result: None,
    redact: Callable[[dict], dict] = lambda kw: {},
):
    """Decorator for an @mcp.tool() function.

    operation: "read" or "write" -- required, per the redaction policy
        table in AUDIT_LOGGING_ARCHITECTURE.md.
    resource_id: fn(kwargs, result_or_None) -> str | None. The specific
        thing touched (a note ID, event UID, file path). Safe to store
        plainly -- an identifier is not content. `result` is None both
        when the tool has no meaningful single-resource return AND when
        the call failed, so a resource_id function that only reads from
        kwargs (the common case for reads) is fine either way.
    redact: fn(kwargs) -> dict. Everything else worth recording, already
        sanitized per the redaction policy table. Never raw content.
    """
    if operation not in ("read", "write"):
        raise ValueError(f"audit_logged: operation must be 'read' or 'write', got {operation!r}")

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
                try:
                    rid = resource_id(kwargs, result if success else None)
                except Exception:
                    rid = None  # a broken resource_id extractor must not break the tool call either
                _write_audit_row(
                    tool_name=fn.__name__,
                    operation=operation,
                    resource_id=rid,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    success=success,
                    error_summary=error_summary,
                    args_summary=_safe_redact(redact, kwargs),
                    result_summary=_summarize(result if success else None),
                )

        return wrapper

    return decorator


def _safe_redact(redact, kwargs) -> dict:
    try:
        return redact(kwargs)
    except Exception:
        return {}  # a broken redact function must not break the tool call, and must not fall back to raw kwargs


def _write_audit_row(**fields) -> None:
    """Best-effort only -- see module docstring. Never raises."""
    if not AUDIT_DB_DSN:
        return
    try:
        with psycopg2.connect(AUDIT_DB_DSN, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tool_calls
                        (person_id, agent_instance, server_name, tool_name,
                         operation, resource_id, duration_ms, success,
                         error_summary, args_summary, result_summary)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        AUDIT_PERSON_ID,
                        AUDIT_AGENT_INSTANCE,
                        AUDIT_SERVER_NAME,
                        fields["tool_name"],
                        fields["operation"],
                        fields["resource_id"],
                        fields["duration_ms"],
                        fields["success"],
                        fields["error_summary"],
                        Json(fields["args_summary"]),
                        fields["result_summary"],
                    ),
                )
    except Exception:
        pass  # deliberately silent -- see module docstring


def _summarize(result) -> str:
    if result is None:
        return "no result"
    if isinstance(result, list):
        return f"{len(result)} item(s) returned"
    if isinstance(result, dict):
        return "object returned"
    return "ok"
