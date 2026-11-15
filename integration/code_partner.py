"""
code-partner -- DOMO at the workbench (IaC/docs/CODE_PARTNER.md).

A coding partner that runs entirely in the house: the model is gpu01-primary on
gpu-01, the repo is a clone on code-01, the voice is DOMO's (app -> gateway ->
domo-router -> here). The rule is the household's: **AI suggests, checks code,
people decide.**

    the model may:   list, read and search files; propose an edit; run a check
    code decides:    which paths it may see or touch, whether an edit applies
                     exactly once, whether the result still parses, whether a
                     secret is in it, how big a change may be
    a person does:   apply (= commit on the session's domo/ branch), reject,
                     push the branch, open the pull request. Merging is not in
                     this file at all; Forgejo protects master as well.

Every session is its own git worktree on its own `domo/<date>-<slug>` branch,
cut from origin/master. Nothing here holds a household-data credential or an
SSH key: code-01 can reach gpu-01 (model) and git-01 (Forgejo), nothing else.

Run: uvicorn code_partner:app --host 0.0.0.0 --port 8210
Tests: integration/test_code_partner.py
"""

from __future__ import annotations

import asyncio
from collections import deque
import difflib
import fnmatch
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Optional

import httpx
import yaml
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

log = logging.getLogger("code-partner")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")

HERE = Path(__file__).resolve().parent
POLICY_PATH = Path(os.environ.get("CODE_PARTNER_POLICY", HERE / "code_partner_policy.yml"))
REPO_DIR = Path(os.environ.get("CODE_PARTNER_REPO", "/srv/code-partner/repo"))
STATE_DIR = Path(os.environ.get("CODE_PARTNER_STATE", "/var/lib/code-partner"))
AUDIT_LOG = Path(os.environ.get("CODE_PARTNER_AUDIT", "/var/log/code-partner/audit.jsonl"))
ROUTER_KEY = os.environ.get("CODE_PARTNER_ROUTER_KEY", "")
# Authelia's Remote-User -> person. Trusted only from the proxy (web-01's Traefik).
UI_USERS: dict[str, str] = json.loads(os.environ.get("UI_USERS_JSON", '{"owner": "owner"}'))
TRUSTED_PROXIES = set(json.loads(os.environ.get("TRUSTED_PROXIES_JSON", '["10.0.30.20"]')))
FORGEJO_URL = os.environ.get("FORGEJO_URL", "http://10.0.30.31:3000").rstrip("/")
FORGEJO_REPO = os.environ.get("FORGEJO_REPO", "owner/GitOps")
FORGEJO_KEY_FILE = Path(os.environ.get("FORGEJO_KEY_FILE", "/etc/code-partner/forgejo_key"))
PR_REVIEWER = os.environ.get("PR_REVIEWER", "owner")
# Voice in the browser (2026-11-05): Whisper on gpu-02 (DOMO's), Piper in this process
# with DOMO's voice from nas-02 (never downloaded at runtime: on-premise rule).
WHISPER_URL = os.environ.get("WHISPER_URL", "http://10.0.30.51:5000").rstrip("/")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "mobiuslabsgmbh/faster-whisper-large-v3-turbo")
PIPER_VOICE_PATH = Path(os.environ.get("PIPER_VOICE_PATH", "/opt/code-partner/voices/en_GB-alan-medium.onnx"))
MAX_AUDIO_BYTES = 12 * 1024 * 1024
SSE_MAX_SECONDS = 600            # one /api/events stream; the browser reconnects after it


# ── Live progress (CODE_PARTNER_NEXT.md, step 1) ─────────────────────────────

class Events:
    """What happens during a turn, step by step, per person, for the page's live
    panel (GET /api/events). In memory only: it may carry diffs, which never go to
    disk; audit.jsonl stays content-free. Gone on restart."""

    def __init__(self, keep: int = 200):
        self.keep = keep
        self.lock = threading.Lock()
        self.seq = 0
        self.items: dict[str, deque] = {}

    def emit(self, speaker: str, kind: str, **data) -> dict:
        with self.lock:
            self.seq += 1
            ev = {"id": self.seq, "ts": round(time.time(), 2), "kind": kind, **data}
            self.items.setdefault(speaker, deque(maxlen=self.keep)).append(ev)
        return ev

    def after(self, speaker: str, last: int) -> list[dict]:
        with self.lock:
            if last > self.seq:              # the service restarted: the browser's id is from before
                last = 0
            return [e for e in self.items.get(speaker, ()) if e["id"] > last]


EVENTS = Events()


def tool_outcome(name: str, res: dict) -> str:
    """One line for the live panel: what a tool call came back with."""
    if "refused" in res:
        return "refused: " + str(res["refused"])[:200]
    if "error" in res:
        return "error: " + str(res["error"])[:200]
    if name == "propose_edit":
        return f"change {res.get('change')} ready, {res.get('changed_lines')} lines"
    if name == "read_file":
        return f"lines {res.get('lines', '?')}"
    for key in ("hits", "files"):
        if isinstance(res.get(key), list):
            return f"{res.get('total', len(res[key]))} {key}" + (" (truncated)" if res.get("truncated") else "")
    return "ok"


def tool_target(args: dict) -> str:
    return str(next((args[k] for k in ("path", "name", "query", "pattern") if args.get(k)), ""))[:160]


# ── Policy ───────────────────────────────────────────────────────────────────

class PolicyError(Exception):
    """A guardrail said no. The message is safe to show and to speak."""


@dataclass
class Policy:
    raw: dict

    @classmethod
    def load(cls, path: Path = POLICY_PATH) -> "Policy":
        with open(path, encoding="utf-8") as f:
            return cls(yaml.safe_load(f))

    def __getitem__(self, k):
        return self.raw[k]

    @property
    def limits(self) -> dict:
        return self.raw["limits"]

    @property
    def git(self) -> dict:
        return self.raw["git"]

    def model(self, name: str) -> dict:
        return self.raw["models"]["allowed"][name]

    def resolve_model(self, spoken: str) -> Optional[str]:
        s = spoken.strip().lower()
        for name, m in self.raw["models"]["allowed"].items():
            if s == name.lower() or s in [a.lower() for a in m.get("aliases", [])]:
                return name
        return None

    def _match(self, rel: str, patterns: list[str]) -> Optional[str]:
        for p in patterns:
            if fnmatch.fnmatchcase(rel, p):
                return p
        return None

    def check_read(self, rel: str) -> None:
        if p := self._match(rel, self.raw["paths"]["deny_read"]):
            raise PolicyError(f"{rel} is off limits (rule {p}).")

    def check_write(self, rel: str) -> None:
        self.check_read(rel)
        if p := self._match(rel, self.raw["paths"]["deny_write"]):
            raise PolicyError(f"I may read {rel} but not change it (rule {p}). That one is yours to edit.")
        if PurePosixPath(rel).suffix not in self.raw["paths"]["allowed_suffixes_for_write"]:
            raise PolicyError(f"I don't edit {PurePosixPath(rel).suffix or 'extension-less'} files.")

    def secret_hits(self, added_lines: list[str]) -> list[int]:
        """Indexes of added lines that look like a credential. Never returns the text."""
        pats = [re.compile(p) for p in self.raw["secret_patterns"]]
        return [i for i, line in enumerate(added_lines) if any(p.search(line) for p in pats)]


def safe_rel(root: Path, path: str) -> str:
    """Repo-relative POSIX path inside root, or PolicyError. Symlinks are resolved,
    so a link can't point the model outside the worktree."""
    p = (path or "").strip()
    while p.startswith("./"):
        p = p[2:]
    if p in (".", ""):
        p = ""
    if p.startswith("/") or "\x00" in p:
        raise PolicyError("Paths are relative to the repo root.")
    full = (root / p).resolve()
    root_r = root.resolve()
    if full != root_r and root_r not in full.parents:
        raise PolicyError(f"{path} is outside the repo.")
    return full.relative_to(root_r).as_posix() if full != root_r else ""


# ── Audit ────────────────────────────────────────────────────────────────────

_audit_lock = threading.Lock()


def audit(event: str, **fields) -> None:
    """One JSON line per action. Paths, sizes and outcomes only: never file
    content, diffs or what was said."""
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **fields}
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _audit_lock, open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError as e:                      # auditing never breaks the work itself
        log.warning("audit write failed: %s", e)


# ── Git ──────────────────────────────────────────────────────────────────────

GIT_ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"),
           "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C.UTF-8"}


def git(cwd: Path, *args: str, env_extra: Optional[dict] = None, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, env={**GIT_ENV, **(env_extra or {})},
                       capture_output=True, text=True, timeout=120)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[:300]}")
    return r.stdout


def slugify(text: str, n: int = 32) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:n].rstrip("-") or "session")


# ── Edits ────────────────────────────────────────────────────────────────────

def apply_edit(text: Optional[str], search: str, replace: str) -> str:
    """The new file text. `search` must occur exactly once; empty search creates
    a new file. CRLF files stay CRLF (the model writes "\\n")."""
    if not search:
        if text is not None:
            raise PolicyError("That file exists already; give me the exact text to replace.")
        return replace
    if text is None:
        raise PolicyError("That file doesn't exist. To create it, propose it with an empty search text.")
    crlf = "\r\n" in text
    if crlf and "\r\n" not in search:
        search, replace = search.replace("\n", "\r\n"), replace.replace("\r\n", "\n").replace("\n", "\r\n")
    n = text.count(search)
    if n == 0:
        raise PolicyError("The search text isn't in the file. Read the file again and copy the lines exactly.")
    if n > 1:
        raise PolicyError(f"The search text occurs {n} times; include more surrounding lines so it is unique.")
    return text.replace(search, replace, 1)


class _AnyTagLoader(yaml.SafeLoader):
    pass


_AnyTagLoader.add_multi_constructor("", lambda loader, suffix, node: None)


def validate(rel: str, content: str, policy: Policy) -> list[str]:
    """Problems with a file's new content; [] = fine. Parses or compiles only."""
    checks, suffix, problems = policy["checks"], PurePosixPath(rel).suffix, []
    try:
        if suffix in (".yml", ".yaml") and checks.get("yaml"):
            list(yaml.load_all(content, Loader=_AnyTagLoader))
        elif suffix == ".json" and checks.get("json"):
            json.loads(content)
        elif suffix == ".py" and checks.get("python"):
            compile(content, rel, "exec")
        elif suffix == ".sh" and checks.get("shell") and shutil.which("bash"):
            r = subprocess.run(["bash", "-n"], input=content, capture_output=True, text=True, timeout=20)
            if r.returncode:
                problems.append(f"bash -n: {r.stderr.strip()[:200]}")
        elif suffix == ".tf" and checks.get("terraform_fmt") and shutil.which("terraform"):
            r = subprocess.run(["terraform", "fmt", "-check", "-"], input=content, capture_output=True,
                               text=True, timeout=30)
            if r.returncode:
                problems.append("terraform fmt would change it (formatting)")
    except (yaml.YAMLError, ValueError, SyntaxError) as e:
        problems.append(f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")
    return problems


def unified(rel: str, before: Optional[str], after: str) -> tuple[str, int, list[str]]:
    """(diff text, changed line count, added lines)."""
    a = (before or "").splitlines(keepends=True)
    b = after.splitlines(keepends=True)
    diff = list(difflib.unified_diff(a, b, f"a/{rel}" if before is not None else "/dev/null", f"b/{rel}", n=2))
    added = [l[1:].rstrip("\r\n") for l in diff if l.startswith("+") and not l.startswith("+++")]
    removed = [l for l in diff if l.startswith("-") and not l.startswith("---")]
    return "".join(diff), len(added) + len(removed), added


# ── Sessions ─────────────────────────────────────────────────────────────────

@dataclass
class FileEdit:
    path: str
    before_sha: Optional[str]        # sha256 of the file when proposed; None = new file
    content: str


@dataclass
class Change:
    number: int
    why: str
    edits: list[FileEdit]
    diff: str
    changed_lines: int
    created: float
    status: str = "pending"          # pending | applied | rejected | expired | stale
    commit: Optional[str] = None
    decided_by: Optional[str] = None


@dataclass
class Session:
    id: str
    speaker: str
    title: str
    branch: str
    base_sha: str
    model: str
    created: float
    last_active: float
    history: list[dict] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    active: bool = True
    pushed_sha: Optional[str] = None
    pr_url: Optional[str] = None

    @property
    def workdir(self) -> Path:
        return STATE_DIR / "work" / self.id

    def pending(self) -> list[Change]:
        return [c for c in self.changes if c.status == "pending"]

    def change(self, number: Optional[int]) -> Change:
        pend = self.pending()
        if number is None:
            if len(pend) == 1:
                return pend[0]
            if not pend:
                raise PolicyError("Nothing is waiting to apply: no change has been proposed yet.")
            raise PolicyError("Which change? " + ", ".join(f"change {c.number}" for c in pend))
        for c in self.changes:
            if c.number == number:
                return c
        raise PolicyError(f"There is no change {number}.")


def sha(text: Optional[str]) -> Optional[str]:
    return None if text is None else hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def read_text(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
        return f.read()


class Workbench:
    """Sessions, tools and the confirmed actions. One per process."""

    def __init__(self, policy: Policy, repo: Path = REPO_DIR, state: Path = STATE_DIR):
        global STATE_DIR
        repo, state = Path(repo).resolve(), Path(state).resolve()     # git runs in other cwds
        self.policy, self.repo = policy, repo
        STATE_DIR = state
        (state / "sessions").mkdir(parents=True, exist_ok=True)
        (state / "work").mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.sessions: dict[str, Session] = {}
        for f in sorted((state / "sessions").glob("*.json")):
            try:
                self.sessions[f.stem] = self._decode(json.loads(f.read_text()))
            except (ValueError, KeyError, TypeError) as e:
                log.warning("skipping unreadable session %s: %s", f.name, e)

    # persistence
    @staticmethod
    def _decode(d: dict) -> Session:
        d["changes"] = [Change(**{**c, "edits": [FileEdit(**e) for e in c["edits"]]}) for c in d.get("changes", [])]
        return Session(**d)

    def save(self, s: Session) -> None:
        p = STATE_DIR / "sessions" / f"{s.id}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(s)))
        os.replace(tmp, p)

    # lifecycle
    def active(self, speaker: str) -> Optional[Session]:
        idle = self.policy.limits["session_idle_minutes"] * 60
        for s in self.sessions.values():
            if s.active and s.speaker == speaker:
                if time.time() - s.last_active > idle:
                    self.end(s, reason="idle")
                    return None
                return s
        return None

    def start(self, speaker: str, title: str) -> Session:
        if speaker not in self.policy["people"]:
            raise PolicyError("Coding sessions are only set up for owner so far.")
        with self.lock:
            if old := self.active(speaker):
                self.end(old, reason="new session")
            g = self.policy.git
            git(self.repo, "fetch", "--quiet", g["remote"], g["base_branch"])
            base = git(self.repo, "rev-parse", f"{g['remote']}/{g['base_branch']}").strip()
            stamp = datetime.now().strftime("%Y%m%d-%H%M")
            branch = f"{g['branch_prefix']}{stamp}-{slugify(title)}"
            sid = secrets.token_hex(4)
            s = Session(id=sid, speaker=speaker, title=title or "coding session", branch=branch, base_sha=base,
                        model=self.policy["models"]["default"], created=time.time(), last_active=time.time())
            self._assert_branch_ok(branch)
            git(self.repo, "worktree", "add", "--quiet", "-b", branch, str(s.workdir), base)
            self.sessions[sid] = s
            self.save(s)
            audit("session_start", session=sid, speaker=speaker, branch=branch, base=base[:12])
            return s

    def end(self, s: Session, reason: str = "asked") -> None:
        with self.lock:
            for c in s.pending():
                c.status = "expired"
            s.active = False
            if s.workdir.exists():
                git(self.repo, "worktree", "remove", "--force", str(s.workdir), check=False)
            self.save(s)
            audit("session_end", session=s.id, reason=reason, commits=len([c for c in s.changes if c.commit]))

    def _assert_branch_ok(self, branch: str) -> None:
        g = self.policy.git
        if not branch.startswith(g["branch_prefix"]) or branch in g["protected_branches"]:
            raise PolicyError(f"I only work on {g['branch_prefix']} branches.")

    def _current_branch(self, s: Session) -> str:
        b = git(s.workdir, "rev-parse", "--abbrev-ref", "HEAD").strip()
        self._assert_branch_ok(b)
        if b != s.branch:
            raise PolicyError(f"The worktree is on {b}, not {s.branch}; I've stopped to be safe.")
        return b

    # ── tools the model may call ──
    def _must_exist(self, s: Session, rel: str) -> None:
        """A wrong path gets the closest real ones back, so the model looks
        instead of concluding "it isn't there" (seen live 2026-11-05)."""
        if not rel or (s.workdir / rel).exists():
            return
        tracked = git(s.workdir, "ls-files").splitlines()
        tracked = [p for p in tracked if not self.policy._match(p, self.policy["paths"]["deny_read"])]
        name = PurePosixPath(rel).name.lower()
        close = [p for p in tracked if name and name in p.lower()]
        if not close:
            by_name = {PurePosixPath(p).name.lower(): p for p in tracked}
            close = [by_name[n] for n in difflib.get_close_matches(name, list(by_name), n=5, cutoff=0.5)]
        hint = ("Did you mean: " + ", ".join(close[:5])) if close else "Use list_files or search without a path."
        raise PolicyError(f"There is no {rel}. {hint}")

    def repo_map(self, s: Session, depth: int = 3, cap: int = 60) -> str:
        """Folders up to `depth` levels with their file counts, for the system prompt."""
        counts: dict[str, int] = {}
        for p in git(s.workdir, "ls-files").splitlines():
            parts = PurePosixPath(p).parts[:-1][:depth]
            for i in range(1, len(parts) + 1):
                counts["/".join(parts[:i])] = counts.get("/".join(parts[:i]), 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:cap]
        return ", ".join(f"{d}/ ({n})" for d, n in sorted(top))

    def tool_list_files(self, s: Session, path: str = "", pattern: str = "") -> dict:
        rel = safe_rel(s.workdir, path)
        self._must_exist(s, rel)
        out = git(s.workdir, "ls-files", "--", rel or ".").splitlines()
        out = [p for p in out if not self.policy._match(p, self.policy["paths"]["deny_read"])]
        if pattern:
            out = [p for p in out if fnmatch.fnmatch(PurePosixPath(p).name, pattern)]
        cap = self.policy.limits["max_list_entries"]
        return {"files": out[:cap], "total": len(out), "truncated": len(out) > cap}

    def tool_find_files(self, s: Session, name: str) -> dict:
        """Tracked files whose path contains `name` (case-insensitive); the way to
        find a file by name, which content search is bad at."""
        if not name or len(name) < 3:
            raise PolicyError("Give at least three characters of the file name.")
        want = name.lower().strip("/")
        out = [p for p in git(s.workdir, "ls-files").splitlines()
               if want in p.lower() and not self.policy._match(p, self.policy["paths"]["deny_read"])]
        out.sort(key=lambda p: (not p.startswith("Infrastructure/"), want not in PurePosixPath(p).name.lower(), len(p)))
        cap = self.policy.limits["max_search_hits"]
        return {"files": out[:cap], "total": len(out), "truncated": len(out) > cap}

    def tool_read_file(self, s: Session, path: str, start_line: int = 1, end_line: int = 0) -> dict:
        rel = safe_rel(s.workdir, path)
        self.policy.check_read(rel)
        self._must_exist(s, rel)
        text = read_text(s.workdir / rel)
        if text is None or (s.workdir / rel).is_dir():
            raise PolicyError(f"{rel} is not a file.")
        if "\x00" in text[:4096]:
            raise PolicyError(f"{rel} is a binary file.")
        lines = text.splitlines()
        cap = self.policy.limits["max_read_lines"]
        start = max(1, int(start_line or 1))
        end = min(len(lines), int(end_line) if end_line else start + cap - 1, start + cap - 1)
        return {"path": rel, "lines": f"{start}-{end} of {len(lines)}",
                "line_endings": "CRLF" if "\r\n" in text else "LF",
                "text": "\n".join(lines[start - 1:end])}

    def tool_search(self, s: Session, query: str, path: str = "") -> dict:
        if not query or len(query) < 3:
            raise PolicyError("Search for at least three characters.")
        rel = safe_rel(s.workdir, path)
        self._must_exist(s, rel)
        out = git(s.workdir, "grep", "-n", "-I", "-F", "--no-color", "-e", query, "--", rel or ".", check=False)
        hits = []
        for line in out.splitlines():
            fname = line.split(":", 1)[0]
            if self.policy._match(fname, self.policy["paths"]["deny_read"]):
                continue
            hits.append(line[:240])
        cap = self.policy.limits["max_search_hits"]
        return {"hits": hits[:cap], "total": len(hits), "truncated": len(hits) > cap}

    def tool_propose_edit(self, s: Session, path: str, search: str, replace: str, why: str = "") -> dict:
        """Adds one edit to the session's open proposal (created if needed). Nothing
        touches the worktree until a person applies it."""
        rel = safe_rel(s.workdir, path)
        self.policy.check_write(rel)
        lim = self.policy.limits
        with self.lock:
            open_change = next((c for c in s.pending() if c.status == "pending" and not c.commit), None)
            prior = next((e for e in open_change.edits if e.path == rel), None) if open_change else None
            on_disk = read_text(s.workdir / rel)
            base_text = prior.content if prior else on_disk
            new_text = apply_edit(base_text, search, replace)
            if problems := validate(rel, new_text, self.policy):
                raise PolicyError(f"After that edit {rel} doesn't pass its check: {problems[0]}")
            _, _, added = unified(rel, on_disk, new_text)
            if hits := self.policy.secret_hits(added):
                audit("secret_blocked", session=s.id, path=rel, lines=len(hits))
                raise PolicyError("That edit adds something that looks like a credential. Secrets go to Vault, "
                                  "and the file should reference them by path.")
            edits = [e for e in (open_change.edits if open_change else []) if e.path != rel]
            edits.append(FileEdit(rel, sha(on_disk), new_text))
            if len(edits) > lim["max_files_per_change"]:
                raise PolicyError(f"A change may touch at most {lim['max_files_per_change']} files; "
                                  "let's apply this one first.")
            diff, n = self._diff(s, edits)
            if n > lim["max_changed_lines"]:
                raise PolicyError(f"That would be {n} changed lines; the limit per change is "
                                  f"{lim['max_changed_lines']}. Let's do it in smaller steps.")
            if open_change:
                open_change.edits, open_change.diff, open_change.changed_lines = edits, diff, n
                open_change.why = why or open_change.why
                ch = open_change
            else:
                ch = Change(number=len(s.changes) + 1, why=why or "edit", edits=edits, diff=diff,
                            changed_lines=n, created=time.time())
                s.changes.append(ch)
            self.save(s)
        audit("propose", session=s.id, change=ch.number, path=rel, changed_lines=n)
        EVENTS.emit(s.speaker, "proposed", change=ch.number, files=[e.path for e in ch.edits], changed_lines=n,
                    why=ch.why, diff=diff[:6000])
        return {"change": ch.number, "files": [e.path for e in ch.edits], "changed_lines": n,
                "status": "waiting on code.home.example.org for owner to apply or reject"}

    def tool_show_diff(self, s: Session) -> dict:
        committed = git(s.workdir, "diff", "--stat", f"{s.base_sha}..HEAD").strip()
        pend = [{"change": c.number, "why": c.why, "diff": c.diff[:6000]} for c in s.pending()]
        return {"branch": s.branch, "committed": committed or "nothing yet", "pending": pend}

    def _diff(self, s: Session, edits: list[FileEdit]) -> tuple[str, int]:
        parts, total = [], 0
        for e in edits:
            d, n, _ = unified(e.path, read_text(s.workdir / e.path), e.content)
            parts.append(d)
            total += n
        return "".join(parts), total

    # ── actions only a person triggers ──
    def apply(self, s: Session, number: Optional[int], by: str) -> Change:
        with self.lock:
            ch = s.change(number)
            if ch.status != "pending":
                raise PolicyError(f"Change {ch.number} is {ch.status}.")
            if time.time() - ch.created > self.policy.limits["pending_ttl_minutes"] * 60:
                ch.status = "expired"
                self.save(s)
                raise PolicyError(f"Change {ch.number} waited too long and expired; ask me to propose it again.")
            self._current_branch(s)
            for e in ch.edits:                         # the world must still look as it did when proposed
                self.policy.check_write(e.path)
                if sha(read_text(s.workdir / e.path)) != e.before_sha:
                    ch.status = "stale"
                    self.save(s)
                    raise PolicyError(f"{e.path} changed since I proposed this; I'll need to redo it.")
                if problems := validate(e.path, e.content, self.policy):
                    raise PolicyError(f"{e.path}: {problems[0]}")
                _, _, added = unified(e.path, read_text(s.workdir / e.path), e.content)
                if self.policy.secret_hits(added):
                    raise PolicyError("This change contains something that looks like a credential.")
            for e in ch.edits:
                target = s.workdir / e.path
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
                    f.write(e.content)
            git(s.workdir, "add", "--", *[e.path for e in ch.edits])
            g = self.policy.git
            msg = (f"DOMO: {ch.why}\n\nProposed by code-partner ({s.model}), session {s.id}, change {ch.number}.\n"
                   f"Approved-by: {by}\n")
            git(s.workdir, "-c", f"user.name={g['author_name']}", "-c", f"user.email={g['author_email']}",
                "commit", "--quiet", "--no-verify", "-m", msg)
            ch.commit = git(s.workdir, "rev-parse", "HEAD").strip()
            ch.status, ch.decided_by = "applied", by
            s.last_active = time.time()
            self.save(s)
        audit("apply", session=s.id, change=ch.number, commit=ch.commit[:12], by=by,
              files=[e.path for e in ch.edits])
        EVENTS.emit(s.speaker, "command", text=f"Change {ch.number} committed ({ch.commit[:10]}) by {by}.")
        # Applying is the owner's decision; the branch then goes to Forgejo right away so he
        # can see it there and in VS Code (2026-11-06). The PR stays a separate step.
        try:
            self.push_branch(s, by)
        except (RuntimeError, OSError) as e:
            log.warning("push after apply failed: %s", e)
            EVENTS.emit(s.speaker, "command", ok=False, text="Push to Forgejo failed; say 'push it' to retry.")
            audit("push_failed", session=s.id, branch=s.branch, error=str(e)[:160])
        return ch

    def reject(self, s: Session, number: Optional[int], by: str) -> Change:
        with self.lock:
            ch = s.change(number)
            if ch.status != "pending":
                raise PolicyError(f"Change {ch.number} is {ch.status}.")
            ch.status, ch.decided_by = "rejected", by
            self.save(s)
        audit("reject", session=s.id, change=ch.number, by=by)
        EVENTS.emit(s.speaker, "command", text=f"Change {ch.number} rejected by {by}.")
        return ch

    @staticmethod
    def _forgejo_key() -> str:
        return FORGEJO_KEY_FILE.read_text().strip() if FORGEJO_KEY_FILE.exists() else ""

    def push_branch(self, s: Session, by: str) -> str:
        """Push the session branch (never anything else, never --force). No PR."""
        with self.lock:
            branch = self._current_branch(s)
            head = git(s.workdir, "rev-parse", "HEAD").strip()
            if head == s.base_sha:
                raise PolicyError("There's nothing committed on this branch yet.")
            if s.pushed_sha != head:
                key = self._forgejo_key()
                env = {}
                if key:                       # header via env, so the key is never on a command line
                    env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
                           "GIT_CONFIG_VALUE_0": f"Authorization: token {key}"}
                git(s.workdir, "push", "--quiet", self.policy.git["remote"], f"HEAD:refs/heads/{branch}",
                    env_extra=env)
                s.pushed_sha = head
                self.save(s)
                audit("push", session=s.id, branch=branch, head=head[:12], by=by)
                EVENTS.emit(s.speaker, "command", text=f"Branch {branch} pushed to Forgejo ({head[:10]}).")
        return branch

    def push(self, s: Session, by: str) -> str:
        """Push the branch if needed and open or update the draft PR. Returns the PR URL
        (or the branch name when there's no Forgejo key, as in tests)."""
        branch = self.push_branch(s, by)
        key = self._forgejo_key()
        if key:
            with self.lock:
                s.pr_url = self._open_pr(s, key)
                self.save(s)
            audit("pull_request", session=s.id, branch=branch, by=by, pr=s.pr_url)
            EVENTS.emit(s.speaker, "command", text="Draft pull request open.", url=s.pr_url)
        return s.pr_url or branch

    def _open_pr(self, s: Session, key: str) -> Optional[str]:
        h = {"Authorization": f"token {key}"}
        applied = [c for c in s.changes if c.status == "applied"]
        body = (f"Made with DOMO's code partner, by voice and on code.home.example.org.\n\n"
                f"- Branch `{s.branch}` from `{s.base_sha[:10]}`\n- Model `{s.model}` (on premise)\n"
                + "".join(f"- Change {c.number}: {c.why} ({c.commit[:10]}, approved by {c.decided_by})\n"
                          for c in applied)
                + "\nCode checked: paths, exact-once edits, parse/compile, secret scan. "
                  "Not run: ansible/terraform against real hosts. Review before merging.\n")
        with httpx.Client(timeout=20, headers=h) as c:
            r = c.get(f"{FORGEJO_URL}/api/v1/repos/{FORGEJO_REPO}/pulls", params={"state": "open", "limit": 50})
            r.raise_for_status()
            for pr in r.json():
                if pr.get("head", {}).get("ref") == s.branch:
                    c.patch(f"{FORGEJO_URL}/api/v1/repos/{FORGEJO_REPO}/pulls/{pr['number']}", json={"body": body})
                    return pr["html_url"]
            r = c.post(f"{FORGEJO_URL}/api/v1/repos/{FORGEJO_REPO}/pulls",
                       json={"head": s.branch, "base": self.policy.git["base_branch"],
                             "title": f"WIP: DOMO: {s.title}", "body": body})
            r.raise_for_status()
            pr = r.json()
            c.post(f"{FORGEJO_URL}/api/v1/repos/{FORGEJO_REPO}/pulls/{pr['number']}/requested_reviewers",
                   json={"reviewers": [PR_REVIEWER]})
            return pr["html_url"]

    def set_model(self, s: Session, spoken: str) -> str:
        name = self.policy.resolve_model(spoken)
        if not name:
            allowed = ", ".join(self.policy["models"]["allowed"])
            raise PolicyError(f"I don't have a model called {spoken}. Available: {allowed}.")
        s.model = name
        self.save(s)
        audit("model", session=s.id, model=name)
        return name


# ── The model ────────────────────────────────────────────────────────────────

TOOLS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "List tracked files under a folder of the repo.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "folder, relative to the repo root; '' for the root"},
            "pattern": {"type": "string", "description": "optional filename glob, e.g. '*.yml'"}}}}},
    {"type": "function", "function": {
        "name": "find_files", "description": "Find files by (part of) their name or path, e.g. 'studio01' or 'services.yml'.",
        "parameters": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read lines of a file (at most 250 per call).",
        "parameters": {"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "search", "description": "Find a fixed string in the repo (like git grep). Returns file:line:text.",
        "parameters": {"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"}, "path": {"type": "string", "description": "optional folder"}}}}},
    {"type": "function", "function": {
        "name": "propose_edit",
        "description": ("Propose replacing an exact piece of a file. `search` must be copied exactly from "
                        "read_file output and occur once; empty `search` creates a new file. Nothing is "
                        "written until owner applies it."),
        "parameters": {"type": "object", "required": ["path", "search", "replace", "why"], "properties": {
            "path": {"type": "string"}, "search": {"type": "string"}, "replace": {"type": "string"},
            "why": {"type": "string", "description": "one short line for the commit message"}}}}},
    {"type": "function", "function": {
        "name": "show_diff", "description": "What is committed on this branch so far and what is waiting.",
        "parameters": {"type": "object", "properties": {}}}},
]

SYSTEM_PROMPT = """You are DOMO's code partner. You are talking to owner; call him "you", never
"owner". You work together on the homelab GitOps repo (Terraform, Ansible, Python services,
docs) on branch {branch}.
- Use the tools to look before you suggest. Read the lines you want to change first.
- To change something call propose_edit through the tool interface. A change exists only
  when propose_edit returned a change number; never say something is proposed, pending or
  visible otherwise. To change an existing file, read it and replace an exact piece of it;
  an empty search only creates a new file.
- You cannot apply, commit or push. owner does that by saying "apply change N" (commits and
  pushes the branch to Forgejo) and "open the pull request". The code checks every edit.
  If a tool says no, explain why in one sentence; don't try to work around a guardrail.
- Where owner sees things: proposed changes and their diffs on code.home.example.org; applied
  changes on branch {branch} in Forgejo, git.home.example.org, and in VS Code after a git fetch.
- Secrets never go into files: reference Vault paths instead.
- Your answer is spoken aloud: 1 to 3 short sentences, no markdown, no code, no file
  contents. Just say what you proposed and why.
- If the request is unclear, ask one short question instead of guessing.
- Never guess a path: find_files by name first; search looks inside files.
Where things are:
{repo_hints}
Session: "{title}". Today is {today}."""


CLAIMS_EDIT = re.compile(r"\b(?:I'?ll|I will|I'?ve|I have|I(?:'m| am) going to|let me|I)\s+(?:(?:now|already|just|also)\s+)*"
                         r"(?:change|changed|update|updated|set|edit|edited|modify|modified|replace|replaced|"
                         r"add|added|remove|removed|bump|bumped|increase|increased|raise|raised|propose|proposed|"
                         r"proposing|changing|updating|setting)\b", re.I)
# FOUND live 2026-11-06: with nothing proposed, "it's now visible as a pending diff on
# the owner's screen". Any talk of a waiting diff is checked against what code knows.
CLAIMS_WAITING = re.compile(r"\b(?:pending|visible|on (?:your|owner'?s|the) screen|the diff|proposed|waiting)\b", re.I)
NUDGE_PROPOSE = ("Internal note, not from owner: you described an edit but did not call propose_edit, so nothing "
                 "is proposed. Call propose_edit now with the exact search text from read_file, or say in one "
                 "sentence what you still need.")

# FOUND live 2026-11-06: Ministral also prints "propose_edit[ARGS]{...}" with no
# [TOOL_CALLS] prefix, sometimes several in a row; nothing was proposed all session.
_LEAKED_MISTRAL = re.compile(r"(?:\[TOOL_CALLS\]\s*)?([A-Za-z_]\w*)\s*\[ARGS\]\s*(?=\{)")
LEAK_MARKERS = re.compile(r"\[(?:TOOL_CALLS|ARGS)\]")


def leaked_tool_calls(content: str) -> list[dict]:
    """Tool calls the model printed as text instead of calling (seen with
    Ministral: "[TOOL_CALLS]name[ARGS]{...}", "name[ARGS]{...}" or a bare JSON object)."""
    if not content:
        return []
    if LEAK_MARKERS.search(content):
        out, dec = [], json.JSONDecoder(strict=False)       # strict=False: raw newlines inside strings
        for m in _LEAKED_MISTRAL.finditer(content):
            try:
                args, _ = dec.raw_decode(content, m.end())
            except ValueError:
                continue
            out.append({"function": {"name": m.group(1), "arguments": args}})
        return out
    t = content.strip().strip("`").removeprefix("json").strip()
    if t.startswith("[TOOL_CALLS]"):
        t = t[len("[TOOL_CALLS]"):].strip()
    try:
        obj = json.loads(t)
    except ValueError:
        return []
    items = obj if isinstance(obj, list) else [obj]
    out = []
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("name"), str) and it["name"] in {x["function"]["name"] for x in TOOLS}:
            out.append({"function": {"name": it["name"], "arguments": it.get("arguments") or it.get("parameters") or {}}})
    return out


class Partner:
    """The conversation: deterministic commands first, then the model with tools."""

    def __init__(self, bench: Workbench, chat=None):
        self.bench = bench
        self.chat = chat or self._ollama_chat

    def _ollama_chat(self, model_name: str, messages: list[dict]) -> dict:
        m = self.bench.policy.model(model_name)
        body = {"model": m["model"], "messages": messages, "tools": TOOLS, "stream": False,
                "options": {"num_ctx": m["num_ctx"], "temperature": m.get("temperature", 0.2)}}
        r = httpx.post(f"{m['url'].rstrip('/')}/api/chat", json=body, timeout=110)
        r.raise_for_status()
        return r.json()["message"]

    def run_tool(self, s: Session, name: str, args: dict) -> dict:
        fn = {"list_files": self.bench.tool_list_files, "find_files": self.bench.tool_find_files,
              "read_file": self.bench.tool_read_file,
              "search": self.bench.tool_search, "propose_edit": self.bench.tool_propose_edit,
              "show_diff": self.bench.tool_show_diff}.get(name)
        res = self._run_tool(s, fn, name, args)
        EVENTS.emit(s.speaker, "tool", tool=name,
                    target=tool_target(args) if isinstance(args, dict) else "",
                    ok=not ({"refused", "error"} & res.keys()), outcome=tool_outcome(name, res))
        return res

    def _run_tool(self, s: Session, fn, name: str, args: dict) -> dict:
        if fn is None:
            return {"error": f"unknown tool {name}"}
        if not isinstance(args, dict):
            return {"error": "arguments must be an object"}
        try:
            res = fn(s, **args)
            audit("tool", session=s.id, tool=name, path=args.get("path"), ok=True)
            return res
        except PolicyError as e:
            audit("tool", session=s.id, tool=name, path=args.get("path"), ok=False, refused=str(e)[:160])
            return {"refused": str(e)}
        except TypeError as e:
            return {"error": f"bad arguments: {e}"}
        except RuntimeError as e:
            return {"error": str(e)}

    def turn(self, s: Session, text: str) -> str:
        lim = self.bench.policy.limits
        s.last_active = time.time()
        system = {"role": "system", "content": SYSTEM_PROMPT.format(
            branch=s.branch, title=s.title, today=datetime.now().strftime("%A %Y-%m-%d"),
            repo_hints="\n".join(f"- {h}" for h in self.bench.policy.raw.get("repo_hints", [])))}
        s.history.append({"role": "user", "content": text})
        before = {c.number: c.changed_lines for c in s.pending()}
        deadline = time.monotonic() + lim["max_turn_seconds"]
        answer, nudged, tools_run = "", False, 0
        for rnd in range(lim["max_tool_rounds"]):
            EVENTS.emit(s.speaker, "thinking", round=rnd + 1, of=lim["max_tool_rounds"], model=s.model,
                        seconds_left=max(0, int(deadline - time.monotonic())))
            msg = self.chat(s.model, [system, *s.history[-lim["history_messages"]:]])
            calls = msg.get("tool_calls")
            if not calls and (calls := leaked_tool_calls(msg.get("content", ""))):
                EVENTS.emit(s.speaker, "note", text="The model wrote its tool call as text; code ran it anyway.")
            if not calls:
                if not tools_run:
                    EVENTS.emit(s.speaker, "no_tool", text="The model answered without using any tool.")
                answer = (msg.get("content") or "").strip()
                proposed = any(before.get(c.number) != c.changed_lines for c in s.pending())
                # FOUND live 2026-11-05: "I'll change retries from 10 to 20" with no
                # propose_edit call. One internal nudge (as a system note: sent as
                # "user", the studio partner's model apologised to owner instead).
                if not proposed and not nudged and CLAIMS_EDIT.search(answer):
                    nudged = True
                    s.history.append({"role": "assistant", "content": answer})
                    s.history.append({"role": "system", "content": NUDGE_PROPOSE})
                    continue
                break
            s.history.append({"role": "assistant", "content": "", "tool_calls": calls})
            for call in calls:
                fn = call.get("function", {})
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                res = self.run_tool(s, fn.get("name", ""), args)
                tools_run += 1
                s.history.append({"role": "tool", "tool_name": fn.get("name", ""),
                                  "content": json.dumps(res)[:12000]})
            if time.monotonic() > deadline:
                EVENTS.emit(s.speaker, "note", text=f"Time box reached ({lim['max_turn_seconds']} s).")
                break
        if not answer:
            answer = "I ran out of steps on that one. Can you narrow it down?"
        if LEAK_MARKERS.search(answer) or answer.lstrip().startswith("{"):
            # A tool call we couldn't read: never show or speak it as if it were an answer.
            answer = "I tried to propose that, but my edit came out garbled. Ask me again, please."
        # Spoken: no markdown. Underscores only at word edges, so file names survive
        # (2026-11-06: GITOPS_REPO_SUMMARY.md was read back as GITOPSREPOSUMMARY.md).
        answer = re.sub(r"[`*#]+|(?<!\w)_+|_+(?!\w)", "", answer)
        nothing_applied = not any(c.status == "applied" for c in s.changes)
        if CLAIMS_DONE.search(answer) and (nothing_applied or s.pending()):
            answer = ("I can only propose changes; nothing is applied, committed or pushed until you say so. "
                      + CLAIMS_DONE.sub("proposed", answer))
        proposed = any(before.get(c.number) != c.changed_lines for c in s.pending())
        if not proposed and not s.pending() and (CLAIMS_EDIT.search(answer) or CLAIMS_WAITING.search(answer)):
            answer += " Correction from the code: nothing is proposed right now, so there is nothing to apply."
        s.history.append({"role": "assistant", "content": answer})
        # The facts about what is waiting come from code, not from the model.
        for c in s.pending():
            if before.get(c.number) != c.changed_lines:
                files = ", ".join(PurePosixPath(e.path).name for e in c.edits)
                answer += (f" Change {c.number} is ready: {c.changed_lines} changed lines in {files}. "
                           f"Say 'apply change {c.number}' or 'reject it'.")
        self.bench.save(s)
        return answer


# ── What owner says (handled by code, never by the model) ──────────────────────

_NUM = r"(?:change\s+(?:number\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten))"
_WORDNUM = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten".split())}
CMD_APPLY = re.compile(rf"^\W*(?:yes,?\s+|okay,?\s+|ok,?\s+)?(?:please\s+)?(?:apply|commit)\s+(?:it|that|this|the change|{_NUM})\W*$", re.I)
CMD_REJECT = re.compile(rf"^\W*(?:no,?\s+)?(?:reject|discard|drop|skip)\s+(?:it|that|this|the change|{_NUM})\W*$", re.I)
CMD_PUSH = re.compile(r"^\W*(?:please\s+)?(?:push (?:it|the branch|that)|open (?:a|the) (?:pull request|pr)|"
                      r"push and open (?:a|the) (?:pull request|pr)|make (?:a|the) (?:pull request|pr))\W*$", re.I)
# Said naturally (FOUND live 2026-11-06, both went to the model, which "couldn't"):
# "Proceed with the proposed edit for X.md", "commit this and push it as well as create a PR".
# Short sentences only, so a request *about* commits ("add a git push step") still reaches the model.
_LEAD = (r"^\W*(?:(?:yes|yeah|ok|okay|alright|right|great|good|perfect|so|then|now|and)\W+)*"
         r"(?:(?:let'?s|let us|please|can you|could you|would you|you can|i'?d like to|i want to|we can|"
         r"go ahead and|just)\s+)*")
_OBJ = (rf"(?:it|that|this|them|these|those|{_NUM}|the\s+(?:proposed\s+|pending\s+)?(?:changes|change|edits|edit|diff)\b"
        r"(?:\s+(?:for|to|in|on)\s+\S+)?)")
CMD_APPLY_LOOSE = re.compile(rf"{_LEAD}(?:apply|commit|accept|approve|proceed with)\s+{_OBJ}(?P<tail>.*)$", re.I)
CMD_PUSH_LOOSE = re.compile(rf"{_LEAD}(?:push\b|(?:open|create|make|raise)\s+(?:a|the)\s+(?:draft\s+)?"
                            r"(?:pull request|pr)\b)", re.I)
PUSH_WORDS = re.compile(r"\b(?:push|pull request|pr)\b", re.I)
# After "apply it": nothing, or "and push / open a PR". "apply this to the inventory too" is a request.
APPLY_TAIL_OK = re.compile(r"^\W*$|^\W*(?:and|then|also|as well as|plus|&)\b.*\b(?:push|pull request|pr)\b", re.I)
LOOSE_MAX_WORDS = 16
CMD_NEW_CONV = re.compile(r"^\W*(?:new conversation|start (?:a )?new conversation|clear (?:the )?(?:conversation|context)|"
                          r"forget (?:that|the conversation))\W*$", re.I)
CMD_MODEL = re.compile(r"^\W*(?:switch|change)\s+(?:the\s+)?model\s+to\s+(.+?)\W*$|^\W*use\s+the\s+(.+?)\s+model\W*$", re.I)
CMD_WHICH_MODEL = re.compile(r"^\W*(?:which|what) model(?: are you using| is this)?\W*$", re.I)
CMD_STATUS = re.compile(r"^\W*(?:status|what'?s the status|where are we|what (?:have we|did we) change[d]?(?: so far)?|"
                        r"what'?s (?:waiting|pending))\W*$", re.I)
# A bare yes is not consent (Whisper invents short words), but it shouldn't reach the
# model either: FOUND live 2026-11-05, after "yes" the model said "Diff applied".
CMD_BARE_YES = re.compile(r"^\W*(?:yes|yeah|yep|ok|okay|sure|do it|go ahead|please do|ja|mach das)\W*$", re.I)
# The model can't apply, commit, push or merge; a sentence saying it did is corrected.
CLAIMS_DONE = re.compile(r"\b(?:applied|committed|pushed|merged)\b", re.I)
CMD_END = re.compile(r"^\W*(?:end (?:the )?(?:coding|code) session|that'?s it for (?:coding|code|now)|stop coding)\W*$", re.I)
# Opening phrases (domo-router uses the same pattern to send the turn here).
CMD_START = re.compile(r"^\W*(?:let'?s\s+)?(?:start\s+(?:a\s+|the\s+)?)?(?:coding|code)\s+session(?:\s+(?:on|about|for)\s+(.+?))?\W*$|"
                       r"^\W*let'?s\s+(?:code|do some coding)(?:\s+(?:on|about)\s+(.+?))?\W*$", re.I)


def _num(m: re.Match) -> Optional[int]:
    g = next((x for x in m.groups() if x), None)
    if g is None:
        return None
    return int(g) if g.isdigit() else _WORDNUM.get(g.lower())


def status_text(s: Session) -> str:
    applied = [c for c in s.changes if c.status == "applied"]
    pend = s.pending()
    parts = [f"We're on branch {s.branch.split('/', 1)[1]} with {len(applied)} applied "
             f"change{'s' if len(applied) != 1 else ''}."]
    if pend:
        parts.append("Waiting: " + ", ".join(f"change {c.number}, {c.why.rstrip('.')}" for c in pend) + ".")
    if s.pr_url:
        parts.append("The pull request is open.")
    elif s.pushed_sha:
        parts.append("The branch is pushed." if applied and applied[-1].commit == s.pushed_sha
                     else "Newer commits aren't pushed yet.")
    elif applied:
        parts.append("Nothing is pushed yet.")
    return " ".join(p for p in parts if p)


def handle(partner: Partner, speaker: str, text: str, channel: str = "voice") -> dict:
    """One spoken or typed sentence -> {"answer", "active", "session"}. The live
    panel sees it arrive and sees the answer, whichever way it came in."""
    EVENTS.emit(speaker, "heard", text=text[:500], channel=channel)
    started = time.monotonic()
    res = _handle(partner, speaker, text, channel)
    EVENTS.emit(speaker, "answer", text=res.get("answer", ""), seconds=round(time.monotonic() - started, 1),
                active=res.get("active", False))
    return res


def _handle(partner: Partner, speaker: str, text: str, channel: str) -> dict:
    bench = partner.bench
    by = f"{speaker} ({channel})"
    try:
        if m := CMD_START.match(text):
            title = next((g for g in m.groups() if g), "") or "coding session"
            s = bench.start(speaker, title)
            return {"answer": f"Coding session open on a new branch, {s.branch.split('/', 1)[1]}. "
                              "Proposed changes show on code.home.example.org; the branch reaches Forgejo with the "
                              "first change you apply. What are we working on?", "active": True, "session": s.id}
        s = bench.active(speaker)
        if s is None:
            return {"answer": "There's no coding session open. Say 'start a coding session'.", "active": False}
        if CMD_END.match(text):
            summary = status_text(s)
            bench.end(s)
            return {"answer": f"Session closed. {summary}", "active": False, "session": s.id}
        short = len(text.split()) <= LOOSE_MAX_WORDS
        m = CMD_APPLY.match(text) or None
        if m is None and short and (lm := CMD_APPLY_LOOSE.match(text)) and APPLY_TAIL_OK.match(lm.group("tail")):
            m = lm
        if m:
            ch = bench.apply(s, _num(m), by)
            pushed = s.pushed_sha == ch.commit
            tail = m.groupdict().get("tail") or ""
            if pushed and PUSH_WORDS.search(tail) and not s.pending():
                url = bench.push(s, by)
                return {"answer": f"Applied change {ch.number}, pushed the branch, and the draft pull request "
                                  "is open for you to review." if url.startswith("http") else
                                  f"Applied change {ch.number} and pushed the branch.",
                        "active": True, "session": s.id, "pr_url": url if url.startswith("http") else None}
            if pushed:
                return {"answer": f"Applied change {ch.number}: committed and pushed to branch "
                                  f"{s.branch.split('/', 1)[1]} on git.home.example.org. Say 'open the pull request' "
                                  "when you're ready.", "active": True, "session": s.id}
            return {"answer": f"Committed change {ch.number}, but pushing to Forgejo failed. Say 'push it' to "
                              "try again.", "active": True, "session": s.id}
        if m := CMD_REJECT.match(text):
            ch = bench.reject(s, _num(m), by)
            s.history.append({"role": "user", "content": f"(owner rejected change {ch.number}.)"})
            bench.save(s)
            return {"answer": f"Dropped change {ch.number}.", "active": True, "session": s.id}
        if CMD_BARE_YES.match(text):
            pend = s.pending()
            if pend:
                n = pend[0].number
                return {"answer": f"To commit it, say 'apply change {n}'. A plain yes isn't enough for me.",
                        "active": True, "session": s.id}
            return {"answer": "Nothing is waiting. What should we change?", "active": True, "session": s.id}
        if CMD_PUSH.match(text) or (short and CMD_PUSH_LOOSE.match(text)):
            if s.pending():
                return {"answer": "There's still a change waiting. Apply or reject it first.", "active": True,
                        "session": s.id}
            url = bench.push(s, by)
            return {"answer": "The draft pull request is open for you to review and merge."
                    if url.startswith("http") else "Pushed the branch.", "active": True, "session": s.id,
                    "pr_url": url if url.startswith("http") else None}
        if CMD_NEW_CONV.match(text):
            s.history = []
            bench.save(s)
            return {"answer": "Fresh conversation, same branch. What next?", "active": True, "session": s.id}
        if m := CMD_MODEL.match(text):
            name = bench.set_model(s, m.group(1) or m.group(2))
            return {"answer": f"Switched to {name}.", "active": True, "session": s.id}
        if CMD_WHICH_MODEL.match(text):
            mm = bench.policy.model(s.model)
            return {"answer": f"{s.model}, on {mm['url'].split('//')[1].split(':')[0]}, "
                              f"with a {mm['num_ctx'] // 1024}K context.", "active": True, "session": s.id}
        if CMD_STATUS.match(text):
            return {"answer": status_text(s), "active": True, "session": s.id}
        return {"answer": partner.turn(s, text), "active": True, "session": s.id}
    except PolicyError as e:
        return {"answer": str(e), "active": bench.active(speaker) is not None}
    except (httpx.HTTPError, RuntimeError) as e:
        log.warning("turn failed: %s", e)
        return {"answer": "Something went wrong on my side; the details are in the log.",
                "active": bench.active(speaker) is not None, "error": str(e)[:300]}


# ── Voice ────────────────────────────────────────────────────────────────────

# Whisper's inventions on noise/silence; same list as DOMO's gateway (bot.py), which
# can't be imported here (pipecat). Never sent on as a request.
PHANTOMS = {
    "thank you", "thanks", "thank you guys", "thank you very much", "thank you so much", "thanks for watching",
    "thank you for watching", "that was amazing", "bye", "bye bye", "goodbye", "you", "oh", "uh", "um", "hmm",
    "okay", "ok", "yeah", "so", "i'm sorry", "sorry", "please subscribe", "vielen dank", "danke", "tschüss",
    "untertitel der amara org community", "untertitel im auftrag des zdf",
}


def clean_utterance(text: str) -> str:
    """The text without phantom sentences; "" if nothing real is left."""
    def first(p: str) -> str:
        return re.sub(r"[^\w' äöüß-]", "", re.split(r"[.!?;]+", p)[0]).strip().lower()
    kept = [p.strip() for p in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if p.strip() and first(p) not in PHANTOMS]
    return " ".join(kept)


def speakable(text: str) -> str:
    """File names and branch names read aloud: deploy_studio01.yml -> deploy studio01 dot yml."""
    t = re.sub(r"(?<=\w)\.(yml|yaml|py|md|tf|json|sh|j2|html|toml)\b", r" dot \1", text)
    t = re.sub(r"(?<=\w)[_/](?=\w)", " ", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def transcribe(audio: bytes, filename: str, content_type: str, prompt: str) -> str:
    r = httpx.post(f"{WHISPER_URL}/v1/audio/transcriptions", timeout=60,
                   files={"file": (filename or "voice.webm", audio, content_type or "application/octet-stream")},
                   data={"model": WHISPER_MODEL, "language": "en", "prompt": prompt})
    r.raise_for_status()
    return (r.json().get("text") or "").strip()


_voice = None
_voice_lock = threading.Lock()


def synthesize(text: str) -> bytes:
    """WAV bytes in DOMO's voice. The model loads once (~2 s, ~240 MB)."""
    global _voice
    import io
    import wave
    with _voice_lock:
        if _voice is None:
            from piper import PiperVoice           # piper-tts; only needed when someone listens
            _voice = PiperVoice.load(str(PIPER_VOICE_PATH))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            _voice.synthesize_wav(speakable(text), w)
    return buf.getvalue()


# ── HTTP ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="code-partner", version="0.1.0")
_partner: Optional[Partner] = None


def partner() -> Partner:
    global _partner
    if _partner is None:
        _partner = Partner(Workbench(Policy.load()))
    return _partner


def router_auth(key: str) -> None:
    if not ROUTER_KEY or not hmac.compare_digest(key, ROUTER_KEY):
        raise HTTPException(401, "bad key")


def ui_person(request: Request) -> str:
    """Authelia's user, trusted only when the request came through the proxy.
    Writes also need the X-Code-Partner header: another site can't send it
    without a CORS preflight, which this app never grants."""
    if (request.client.host if request.client else "") not in TRUSTED_PROXIES:
        raise HTTPException(403, "only via code.home.example.org")
    if request.method == "POST" and request.headers.get("x-code-partner") != "ui":
        raise HTTPException(403, "missing X-Code-Partner header")
    person = UI_USERS.get((request.headers.get("remote-user") or "").lower())
    if not person:
        raise HTTPException(403, "no code-partner person for this login")
    return person


class Turn(BaseModel):
    speaker: str
    text: str
    channel: str = "voice"


@app.get("/health")
def health():
    p = partner()
    return {"ok": True, "repo": REPO_DIR.exists(), "sessions_active": sum(s.active for s in p.bench.sessions.values()),
            "model": p.bench.policy["models"]["default"]}


@app.get("/talk/active")
def talk_active(speaker: str, x_code_partner_key: str = Header(default="")):
    router_auth(x_code_partner_key)
    return {"active": partner().bench.active(speaker) is not None}


@app.post("/talk/turn")
def talk_turn(t: Turn, x_code_partner_key: str = Header(default="")):
    router_auth(x_code_partner_key)
    return handle(partner(), t.speaker, t.text, t.channel)


# UI (code.home.example.org, Authelia two_factor in front)
class UiTurn(BaseModel):
    text: str


@app.get("/api/session")
def ui_session(request: Request):
    who = ui_person(request)
    s = partner().bench.active(who)
    if not s:
        return {"active": False}
    return {"active": True, "id": s.id, "branch": s.branch, "title": s.title, "model": s.model,
            "models": list(partner().bench.policy["models"]["allowed"]), "pr_url": s.pr_url,
            "history": [{"role": h["role"], "content": h["content"]} for h in s.history
                        if h["role"] in ("user", "assistant") and h.get("content")][-30:],
            "changes": [{"number": c.number, "why": c.why, "status": c.status, "diff": c.diff,
                         "changed_lines": c.changed_lines, "commit": c.commit} for c in s.changes][-10:]}


@app.post("/api/say")
def ui_say(t: UiTurn, request: Request):
    return handle(partner(), ui_person(request), t.text, "ui")


@app.post("/api/changes/{number}/{action}")
def ui_change(number: int, action: str, request: Request):
    who = ui_person(request)
    bench = partner().bench
    s = bench.active(who)
    if not s:
        raise HTTPException(409, "no active session")
    try:
        if action == "apply":
            ch = bench.apply(s, number, f"{who} (ui)")
        elif action == "reject":
            ch = bench.reject(s, number, f"{who} (ui)")
        else:
            raise HTTPException(404, "unknown action")
    except PolicyError as e:
        raise HTTPException(409, str(e))
    return {"number": ch.number, "status": ch.status, "commit": ch.commit}


@app.post("/api/voice")
async def ui_voice(request: Request):
    """Browser microphone: audio in -> Whisper (gpu-02) -> the same handle() as
    any turn. Returns what was heard, so the page shows it."""
    who = ui_person(request)
    form = await request.form()
    up = form.get("audio")
    if up is None or not hasattr(up, "read"):
        raise HTTPException(400, "no audio")
    audio = await up.read()
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "audio empty or too long")
    prompt = partner().bench.policy.raw.get("voice", {}).get("whisper_prompt", "")
    try:
        heard = clean_utterance(await asyncio.to_thread(transcribe, audio, up.filename, up.content_type, prompt))
    except httpx.HTTPError as e:
        log.warning("whisper failed: %s", e)
        raise HTTPException(502, "speech recognition unavailable")
    audit("voice_in", speaker=who, bytes=len(audio), heard_chars=len(heard))
    if not heard:
        return {"heard": "", "answer": "", "active": partner().bench.active(who) is not None}
    res = await asyncio.to_thread(handle, partner(), who, heard, "voice")
    return {"heard": heard, **res}


class SpeakReq(BaseModel):
    text: str


@app.post("/api/speak")
async def ui_speak(req: SpeakReq, request: Request):
    ui_person(request)
    text = req.text.strip()[:1200]
    if not text:
        raise HTTPException(400, "empty")
    try:
        wav = await asyncio.to_thread(synthesize, text)
    except Exception as e:                       # a missing voice must not break the page
        log.warning("tts failed: %s", e)
        raise HTTPException(503, "speech unavailable")
    return Response(content=wav, media_type="audio/wav")


@app.get("/api/events")
async def ui_events(request: Request, after: int = 0):
    """Server-Sent Events: each step of the current turn as it happens. The browser's
    EventSource reconnects by itself and sends Last-Event-ID, so nothing is lost."""
    who = ui_person(request)
    last = int(request.headers.get("last-event-id") or after or 0)

    async def stream():
        nonlocal last
        yield "retry: 3000\n\n"
        started, quiet = time.monotonic(), 0
        while time.monotonic() - started < SSE_MAX_SECONDS:
            if await request.is_disconnected():
                return
            evs = EVENTS.after(who, last)
            for e in evs:
                last = e["id"]
                yield f"id: {e['id']}\nevent: step\ndata: {json.dumps(e)}\n\n"
            quiet = 0 if evs else quiet + 1
            if quiet and quiet % 50 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.3)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/push")
def ui_push(request: Request):
    return handle(partner(), ui_person(request), "push it", "ui")


@app.get("/", response_class=HTMLResponse)
@app.get("/ui", response_class=HTMLResponse)
def ui_page(request: Request):
    ui_person(request)
    return HTMLResponse((HERE / "code_partner_ui.html").read_text(encoding="utf-8"))
