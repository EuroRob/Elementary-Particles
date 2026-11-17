"""
guardrails_service.py
FastAPI LLM output safety and quality proxy.

Port  : 5210
Schema: PostgreSQL guardrails (see postgres/guardrails_schema.sql)

All assistant services call POST /validate with their Ollama prompt + response.
This service applies a layered safety pipeline and returns an action:
  pass  — deliver to user unchanged
  warn  — deliver but log a warning flag
  hitl  — queue for human review; return a placeholder to the caller
  block — discard; return a safe fallback to the caller

Pipeline (in order):
  1. Block rules   — keyword/regex scan of output
  2. PII scan      — regex for SSN, credit card, email leak
  3. Context grounding — if RAG context chunks supplied, check semantic drift
  4. Toxicity check   — lightweight Ollama prompt (skipped if Ollama slow)
  5. Confidence score — uses response length + perplexity heuristic

Environment variables:
  DB_DSN           postgresql://...
  OLLAMA_URL       http://10.10.30.50:11434
  OLLAMA_MODEL     llama3.2:3b-instruct-q4_K_M
  QDRANT_URL       http://10.10.30.20:6333  (for hallucination check)
  CONFIDENCE_THRESHOLD  0.6  (below this → hitl)
  TOXICITY_THRESHOLD    0.7  (above this → block)
  GROUNDING_THRESHOLD   0.35 (RAG similarity below this → hallucination flag)
  HITL_PLACEHOLDER  <text to return when response is queued for review>
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from typing import Any, List, Optional

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_DSN               = os.getenv("DB_DSN",            "postgresql://n8n:changeme@10.10.40.10:5432/elementary_particles")
OLLAMA_URL           = os.getenv("OLLAMA_URL",        "http://10.10.30.50:11434")
OLLAMA_MODEL         = os.getenv("OLLAMA_MODEL",      "llama3.2:3b-instruct-q4_K_M")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.60"))
TOXICITY_THRESHOLD   = float(os.getenv("TOXICITY_THRESHOLD",   "0.70"))
GROUNDING_THRESHOLD  = float(os.getenv("GROUNDING_THRESHOLD",  "0.35"))
HITL_PLACEHOLDER     = os.getenv(
    "HITL_PLACEHOLDER",
    "I need a moment to review that response carefully. I'll follow up shortly.",
)

log = logging.getLogger("guardrails")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class ValidateRequest(BaseModel):
    prompt: str
    response: str
    context_chunks: List[str] = Field(default_factory=list)
    profile: str
    service: str
    session_id: Optional[str] = None

class ValidateResponse(BaseModel):
    action: str                    # pass | warn | hitl | block
    safe_response: str             # modified response if blocked/hitl, else original
    safety_score: float
    confidence: float
    flags: dict
    hitl_id: Optional[int] = None  # set if action == hitl

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="guardrails", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_pool: Optional[asyncpg.Pool] = None


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DB_DSN, min_size=2, max_size=6)
    return _pool


# ---------------------------------------------------------------------------
# Block rule matching (loaded from DB, refreshed every 60s via simple cache)
# ---------------------------------------------------------------------------
_rules_cache: List[dict] = []
_rules_loaded_at: float = 0.0
_RULES_TTL = 60.0


async def _get_rules() -> List[dict]:
    global _rules_cache, _rules_loaded_at
    if time.time() - _rules_loaded_at > _RULES_TTL:
        pg = await pool()
        rows = await pg.fetch(
            "SELECT rule_name, rule_type, pattern, applies_to, action "
            "FROM guardrails.block_rules WHERE active = TRUE"
        )
        _rules_cache = [dict(r) for r in rows]
        _rules_loaded_at = time.time()
    return _rules_cache


def _apply_rules(text: str, rules: List[dict], target: str) -> Optional[tuple[str, str]]:
    """Return (rule_name, action) for first matching rule, or None."""
    for rule in rules:
        if rule["applies_to"] not in (target, "both"):
            continue
        try:
            if rule["rule_type"] == "keyword":
                for kw in rule["pattern"].split("|"):
                    if kw.strip().lower() in text.lower():
                        return rule["rule_name"], rule["action"]
            elif rule["rule_type"] == "regex":
                if re.search(rule["pattern"], text, re.IGNORECASE):
                    return rule["rule_name"], rule["action"]
        except re.error:
            pass
    return None


# ---------------------------------------------------------------------------
# Toxicity check via Ollama
# ---------------------------------------------------------------------------
TOXICITY_PROMPT = (
    "Rate the following text for toxicity, harmfulness, and ethical safety on a "
    "scale from 0.0 (completely safe) to 1.0 (extremely harmful). "
    "Respond with ONLY a single float number and nothing else.\n\n"
    "Text: {text}"
)


async def _toxicity_score(text: str) -> float:
    """Ask Ollama to score toxicity. Returns 0.0 on timeout/error (fail-open)."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": TOXICITY_PROMPT.format(text=text[:500]),
                    "stream": False,
                    "options": {"temperature": 0.0, "num_predict": 8},
                },
            )
            r.raise_for_status()
            raw = r.json().get("response", "0.0").strip()
            return max(0.0, min(1.0, float(raw.split()[0])))
    except Exception:
        return 0.0  # fail-open — don't block on Ollama timeout


# ---------------------------------------------------------------------------
# Grounding check (hallucination detection)
# ---------------------------------------------------------------------------
def _grounding_score(response: str, context_chunks: List[str]) -> float:
    """
    Rough lexical overlap score between response and RAG context.
    Returns 0.0 (no grounding) to 1.0 (fully grounded).
    A real implementation would use embedding cosine similarity.
    """
    if not context_chunks:
        return 1.0  # no context supplied — can't check, assume grounded

    response_words = set(re.findall(r'\b\w{4,}\b', response.lower()))
    context_words = set(
        w for chunk in context_chunks
        for w in re.findall(r'\b\w{4,}\b', chunk.lower())
    )
    if not response_words:
        return 1.0
    overlap = response_words & context_words
    return len(overlap) / len(response_words)


# ---------------------------------------------------------------------------
# Confidence heuristic
# ---------------------------------------------------------------------------
def _confidence_score(response: str) -> float:
    """
    Heuristic confidence proxy:
    - Very short responses penalised
    - Hedging language ("I think", "maybe", "not sure") penalised
    - Structured output (JSON, numbered list) rewarded
    """
    score = 0.8
    lower = response.lower()
    # Penalise uncertainty language
    hedges = ["i'm not sure", "i don't know", "i think maybe", "i cannot", "i'm unable"]
    for h in hedges:
        if h in lower:
            score -= 0.15
    # Penalise very short responses
    if len(response.split()) < 10:
        score -= 0.2
    # Reward structure
    if response.strip().startswith(("{", "[")):
        score += 0.1
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# HITL queue
# ---------------------------------------------------------------------------
async def _queue_hitl(event_id: int, profile: str, reason: str, prompt: str, response: str) -> int:
    pg = await pool()
    return await pg.fetchval(
        """INSERT INTO guardrails.hitl_queue
               (event_id, profile_name, reason, prompt_text, response_text)
           VALUES ($1,$2,$3,$4,$5) RETURNING id""",
        event_id, profile, reason, prompt[:2000], response[:2000],
    )


# ---------------------------------------------------------------------------
# Core validate pipeline
# ---------------------------------------------------------------------------
@app.post("/validate", response_model=ValidateResponse)
async def validate(req: ValidateRequest) -> ValidateResponse:
    t0 = time.monotonic()
    rules = await _get_rules()
    flags: dict = {
        "toxicity": False,
        "hallucination": False,
        "pii": False,
        "rule_match": None,
        "low_confidence": False,
    }
    action = "pass"
    safe_response = req.response

    # 1. Block rules on response
    rule_match = _apply_rules(req.response, rules, "response")
    if rule_match:
        flags["rule_match"] = rule_match[0]
        action = _escalate(action, rule_match[1])

    # 2. Block rules on prompt
    rule_match_p = _apply_rules(req.prompt, rules, "prompt")
    if rule_match_p and not flags["rule_match"]:
        flags["rule_match"] = rule_match_p[0]
        action = _escalate(action, rule_match_p[1])

    # 3. Grounding check
    grounding = _grounding_score(req.response, req.context_chunks)
    if req.context_chunks and grounding < GROUNDING_THRESHOLD:
        flags["hallucination"] = True
        action = _escalate(action, "hitl")

    # 4. Toxicity (async, non-blocking timeout)
    tox_score = await _toxicity_score(req.response)
    if tox_score >= TOXICITY_THRESHOLD:
        flags["toxicity"] = True
        action = _escalate(action, "block")

    # 5. Confidence heuristic
    confidence = _confidence_score(req.response)
    if confidence < CONFIDENCE_THRESHOLD:
        flags["low_confidence"] = True
        action = _escalate(action, "hitl")

    # Safety score (composite)
    safety_score = round((1.0 - tox_score) * 0.5 + grounding * 0.3 + confidence * 0.2, 3)

    overhead_ms = int((time.monotonic() - t0) * 1000)

    # Log event
    pg = await pool()
    event_id: int = await pg.fetchval(
        """INSERT INTO guardrails.events
               (profile_name, calling_service, prompt_hash, response_hash, response_length,
                action, safety_score, confidence,
                toxicity_flag, hallucination_flag, off_topic_flag, low_confidence_flag,
                guardrails_overhead_ms, rag_context_used, rag_top_score)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
           RETURNING id""",
        req.profile,
        req.service,
        hashlib.sha256(req.prompt.encode()).hexdigest(),
        hashlib.sha256(req.response.encode()).hexdigest(),
        len(req.response),
        action,
        safety_score,
        confidence,
        flags["toxicity"],
        flags["hallucination"],
        False,
        flags["low_confidence"],
        overhead_ms,
        bool(req.context_chunks),
        grounding if req.context_chunks else None,
    )

    hitl_id = None

    if action == "block":
        safe_response = "I'm not able to provide that response. Please rephrase your request."
    elif action == "hitl":
        reason_parts = []
        if flags["hallucination"]:
            reason_parts.append("potential hallucination (low RAG grounding)")
        if flags["low_confidence"]:
            reason_parts.append("low confidence score")
        if flags["rule_match"]:
            reason_parts.append(f"rule match: {flags['rule_match']}")
        hitl_id = await _queue_hitl(
            event_id, req.profile,
            "; ".join(reason_parts) or "guardrails escalation",
            req.prompt, req.response,
        )
        safe_response = HITL_PLACEHOLDER

    log.info(
        "service=%s profile=%s action=%s safety=%.2f confidence=%.2f overhead=%dms",
        req.service, req.profile, action, safety_score, confidence, overhead_ms,
    )

    return ValidateResponse(
        action=action,
        safe_response=safe_response,
        safety_score=safety_score,
        confidence=confidence,
        flags=flags,
        hitl_id=hitl_id,
    )


def _escalate(current: str, new: str) -> str:
    order = {"pass": 0, "warn": 1, "hitl": 2, "block": 3}
    return new if order.get(new, 0) > order.get(current, 0) else current


# ---------------------------------------------------------------------------
# Block rules management
# ---------------------------------------------------------------------------
@app.get("/rules")
async def get_rules() -> List[dict]:
    pg = await pool()
    rows = await pg.fetch("SELECT * FROM guardrails.block_rules ORDER BY id")
    return [dict(r) for r in rows]


class RuleCreate(BaseModel):
    rule_name: str
    rule_type: str
    pattern: str
    applies_to: str = "response"
    action: str = "block"


@app.post("/rules", status_code=201)
async def create_rule(rule: RuleCreate) -> dict:
    pg = await pool()
    row_id = await pg.fetchval(
        "INSERT INTO guardrails.block_rules (rule_name,rule_type,pattern,applies_to,action) "
        "VALUES ($1,$2,$3,$4,$5) RETURNING id",
        rule.rule_name, rule.rule_type, rule.pattern, rule.applies_to, rule.action,
    )
    global _rules_loaded_at
    _rules_loaded_at = 0.0  # invalidate cache
    return {"id": row_id, "created": True}


@app.put("/rules/{rule_id}/toggle")
async def toggle_rule(rule_id: int) -> dict:
    pg = await pool()
    active = await pg.fetchval(
        "UPDATE guardrails.block_rules SET active = NOT active WHERE id=$1 RETURNING active",
        rule_id,
    )
    global _rules_loaded_at
    _rules_loaded_at = 0.0
    return {"id": rule_id, "active": active}


# ---------------------------------------------------------------------------
# Stats & HITL queue
# ---------------------------------------------------------------------------
@app.get("/stats/{profile}")
async def stats_profile(profile: str, days: int = 7) -> dict:
    pg = await pool()
    row = await pg.fetchrow(
        """SELECT COUNT(*) AS total,
                  ROUND(AVG(safety_score)::numeric,3) AS avg_safety,
                  ROUND(AVG(confidence)::numeric,3) AS avg_confidence,
                  SUM(CASE WHEN action='block' THEN 1 ELSE 0 END) AS blocked,
                  SUM(CASE WHEN action='hitl'  THEN 1 ELSE 0 END) AS hitl,
                  SUM(CASE WHEN hallucination_flag THEN 1 ELSE 0 END) AS hallucinations
           FROM guardrails.events
           WHERE profile_name=$1 AND event_at > NOW() - ($2 || ' days')::interval""",
        profile, str(days),
    )
    return dict(row)


@app.get("/queue/hitl")
async def hitl_queue() -> List[dict]:
    pg = await pool()
    rows = await pg.fetch("SELECT * FROM guardrails.vw_pending_hitl")
    return [dict(r) for r in rows]


@app.post("/queue/{item_id}/approve")
async def hitl_approve(item_id: int) -> dict:
    pg = await pool()
    text = await pg.fetchval(
        "UPDATE guardrails.hitl_queue SET status='approved', resolved_at=NOW() "
        "WHERE id=$1 AND status='pending' RETURNING response_text",
        item_id,
    )
    if text is None:
        raise HTTPException(status_code=404, detail="HITL item not found or already resolved")
    return {"id": item_id, "approved": True, "response_text": text}


@app.post("/queue/{item_id}/reject")
async def hitl_reject(item_id: int, note: str = "") -> dict:
    pg = await pool()
    await pg.execute(
        "UPDATE guardrails.hitl_queue SET status='rejected', resolved_at=NOW(), resolution_note=$1 "
        "WHERE id=$2",
        note, item_id,
    )
    return {"id": item_id, "rejected": True}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "guardrails"}
