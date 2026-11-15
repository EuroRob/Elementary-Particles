"""Tests for code_partner.py: the guardrails, with a real git repo and remote
(a local bare repo) and a fake model. No network, no Ollama, no Forgejo."""

import json
import subprocess
from pathlib import Path

import pytest

import code_partner as cp

POLICY = cp.Policy.load(Path(__file__).with_name("code_partner_policy.yml"))


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def bench(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    sh(tmp_path, "git", "init", "--quiet", "--bare", "-b", "master", str(origin))
    sh(tmp_path, "git", "init", "--quiet", "-b", "master", str(seed))
    (seed / "Ansible").mkdir()
    (seed / "Ansible/services.yml").write_bytes(b"all:\r\n  hosts:\r\n    studio-01:\r\n      vm_memory_gb: 2\r\n")
    (seed / "tool.py").write_text("def f():\n    return 1\n")
    (seed / ".env").write_text("SECRET=1\n")
    (seed / "Terraform").mkdir()
    (seed / "Terraform/terraform.tfvars").write_text("x = 1\n")
    (seed / ".gitignore").write_text("*.tfstate\n")
    (seed / "README.md").write_text("# repo\n")
    sh(seed, "git", "add", "-A")
    sh(seed, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "seed")
    sh(seed, "git", "remote", "add", "origin", str(origin))
    sh(seed, "git", "push", "--quiet", "origin", "master")
    repo = tmp_path / "repo"
    sh(tmp_path, "git", "clone", "--quiet", str(origin), str(repo))
    monkeypatch.setattr(cp, "AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr(cp, "FORGEJO_KEY_FILE", tmp_path / "no-key")
    b = cp.Workbench(POLICY, repo=repo, state=tmp_path / "state")
    b.origin = origin
    return b


def audit_events(tmp_path_audit):
    return [json.loads(l) for l in tmp_path_audit.read_text().splitlines()]


# ── sessions and branches ──

def test_session_gets_own_domo_branch_and_worktree(bench):
    s = bench.start("owner", "studio memory")
    assert s.branch.startswith("domo/") and s.branch.endswith("-studio-memory")
    assert s.workdir.exists()
    assert cp.git(s.workdir, "rev-parse", "--abbrev-ref", "HEAD").strip() == s.branch


def test_only_people_in_policy_can_start(bench):
    with pytest.raises(cp.PolicyError):
        bench.start("partner", "x")


def test_new_session_ends_the_old_one(bench):
    a = bench.start("owner", "one")
    b = bench.start("owner", "two")
    assert not a.active and b.active and bench.active("owner").id == b.id


def test_refuses_to_commit_when_worktree_left_its_branch(bench):
    s = bench.start("owner", "x")
    bench.tool_propose_edit(s, "README.md", "# repo", "# repo!", "title")
    cp.git(s.workdir, "checkout", "--quiet", "-b", "sneaky")
    with pytest.raises(cp.PolicyError):
        bench.apply(s, 1, "owner (test)")


@pytest.mark.parametrize("branch", ["master", "main", "feature/x"])
def test_branch_guard(bench, branch):
    with pytest.raises(cp.PolicyError):
        bench._assert_branch_ok(branch)


# ── paths ──

@pytest.mark.parametrize("path", [".env", "Terraform/terraform.tfvars", "x/secrets/a.yml", "a/.env",
                                  "k/id_ansible", "s/terraform.tfstate.backup", ".git/config"])
def test_deny_read(path):
    with pytest.raises(cp.PolicyError):
        POLICY.check_read(path)


@pytest.mark.parametrize("path", [".gitignore", "integration/code_partner.py",
                                  "integration/code_partner_policy.yml",
                                  "integration/code_partner_ui.html", ".forgejo/workflows/ci.yml",
                                  "CLAUDE.md", "img.png"])
def test_deny_write(path):
    with pytest.raises(cp.PolicyError):
        POLICY.check_write(path)


def test_ok_write():
    POLICY.check_write("ansible/inventory/services.yml")


@pytest.mark.parametrize("path", ["../etc/passwd", "/etc/passwd", "a/../../x"])
def test_paths_stay_in_repo(bench, path):
    s = bench.start("owner", "x")
    with pytest.raises(cp.PolicyError):
        cp.safe_rel(s.workdir, path)


def test_dot_prefix_is_not_stripped_into_an_allowed_path(bench):
    s = bench.start("owner", "x")
    assert cp.safe_rel(s.workdir, "./.forgejo/ci.yml") == ".forgejo/ci.yml"
    with pytest.raises(cp.PolicyError):
        bench.tool_propose_edit(s, "./.gitignore", "*.tfstate", "", "un-ignore")


def test_symlink_out_of_repo_is_refused(bench, tmp_path):
    s = bench.start("owner", "x")
    (s.workdir / "link").symlink_to(tmp_path)
    with pytest.raises(cp.PolicyError):
        bench.tool_read_file(s, "link/audit.jsonl")


def test_list_and_search_hide_denied_files(bench):
    s = bench.start("owner", "x")
    files = bench.tool_list_files(s)["files"]
    assert ".env" not in files and "Terraform/terraform.tfvars" not in files and "tool.py" in files
    hits = bench.tool_search(s, "SECRET")["hits"]
    assert hits == []


# ── edits ──

def test_apply_edit_exactly_once():
    assert cp.apply_edit("a b a", "b", "c") == "a c a"
    with pytest.raises(cp.PolicyError):
        cp.apply_edit("a b a", "a", "c")
    with pytest.raises(cp.PolicyError):
        cp.apply_edit("abc", "zzz", "c")
    with pytest.raises(cp.PolicyError):
        cp.apply_edit("exists", "", "new")
    assert cp.apply_edit(None, "", "new\n") == "new\n"


def test_crlf_file_stays_crlf(bench):
    s = bench.start("owner", "mem")
    bench.tool_propose_edit(s, "Ansible/services.yml", "      vm_memory_gb: 2\n",
                            "      vm_memory_gb: 3\n      vm_cpus: 2\n", "studio-01 to 3 GB")
    bench.apply(s, 1, "owner (test)")
    raw = (s.workdir / "Ansible/services.yml").read_bytes()
    assert b"vm_memory_gb: 3\r\n      vm_cpus: 2\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")


def test_broken_yaml_is_refused(bench):
    s = bench.start("owner", "x")
    with pytest.raises(cp.PolicyError, match="check"):
        bench.tool_propose_edit(s, "Ansible/services.yml", "  hosts:", "  hosts: [", "break it")


def test_broken_python_is_refused(bench):
    s = bench.start("owner", "x")
    with pytest.raises(cp.PolicyError):
        bench.tool_propose_edit(s, "tool.py", "return 1", "return (", "break it")


def test_vault_tags_still_parse():
    assert cp.validate("a.yml", "x: !vault |\n  abc\ny: !unsafe '{{'\n", POLICY) == []


@pytest.mark.parametrize("line", [
    "password: Sup3rS3cretValue!!",
    "api_key = 'abcdefghijklmnop1234'",
    "token: hvs.CAESIJxxxxxxxxxxxxxxxxxxxxxxxx",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "Authorization: PVEAPIToken=terraform@pve!t=e4b8da72-1111-2222-3333-444455556666",
])
def test_secret_scan_blocks(line):
    assert POLICY.secret_hits([line]) == [0]


@pytest.mark.parametrize("line", [
    "password: \"{{ vault_pg01.secret.password }}\"",
    "token: \"{{ lookup('env', 'X') }}\"",
    "api_key: ${API_KEY}",
    "password_file: /etc/x",
    "secret: null",
    "key names only: password, token",
])
def test_secret_scan_allows_references(line):
    assert POLICY.secret_hits([line]) == []


def test_secret_in_proposal_is_blocked_and_audited(bench):
    s = bench.start("owner", "x")
    with pytest.raises(cp.PolicyError, match="credential"):
        bench.tool_propose_edit(s, "README.md", "# repo", "# repo\npassword: Sup3rS3cretValue!!", "oops")
    events = audit_events(cp.AUDIT_LOG)
    blocked = [e for e in events if e["event"] == "secret_blocked"]
    assert blocked and "Sup3r" not in cp.AUDIT_LOG.read_text()


def test_change_size_limit(bench):
    s = bench.start("owner", "x")
    big = "".join(f"line {i}\n" for i in range(200))
    with pytest.raises(cp.PolicyError, match="smaller steps"):
        bench.tool_propose_edit(s, "notes.md", "", big, "big")


def test_file_count_limit(bench):
    s = bench.start("owner", "x")
    for i in range(4):
        bench.tool_propose_edit(s, f"n{i}.md", "", "x\n", "files")
    with pytest.raises(cp.PolicyError, match="at most"):
        bench.tool_propose_edit(s, "n5.md", "", "x\n", "files")


def test_edits_accumulate_into_one_change(bench):
    s = bench.start("owner", "x")
    bench.tool_propose_edit(s, "tool.py", "return 1", "return 2", "two")
    r = bench.tool_propose_edit(s, "tool.py", "return 2", "return 3", "three")
    assert r["change"] == 1 and len(s.changes) == 1
    assert "+    return 3" in s.changes[0].diff


# ── people decide ──

def test_proposal_does_not_touch_the_worktree(bench):
    s = bench.start("owner", "x")
    bench.tool_propose_edit(s, "tool.py", "return 1", "return 2", "two")
    assert "return 1" in (s.workdir / "tool.py").read_text()
    assert cp.git(s.workdir, "status", "--porcelain").strip() == ""


def test_apply_commits_on_branch_with_approver(bench):
    s = bench.start("owner", "x")
    bench.tool_propose_edit(s, "tool.py", "return 1", "return 2", "return two")
    ch = bench.apply(s, None, "owner (voice)")
    log = cp.git(s.workdir, "log", "-1", "--format=%an|%s|%b")
    assert ch.status == "applied" and log.startswith("DOMO code partner|DOMO: return two|")
    assert "Approved-by: owner (voice)" in log
    assert cp.git(bench.repo, "rev-parse", "master").strip() == s.base_sha     # master untouched


def test_stale_change_is_not_applied(bench):
    s = bench.start("owner", "x")
    bench.tool_propose_edit(s, "tool.py", "return 1", "return 2", "two")
    (s.workdir / "tool.py").write_text("def f():\n    return 9\n")
    with pytest.raises(cp.PolicyError, match="changed since"):
        bench.apply(s, 1, "owner (test)")
    assert s.changes[0].status == "stale"


def test_expired_change_is_not_applied(bench, monkeypatch):
    s = bench.start("owner", "x")
    bench.tool_propose_edit(s, "tool.py", "return 1", "return 2", "two")
    s.changes[0].created -= 2 * 3600
    with pytest.raises(cp.PolicyError, match="expired"):
        bench.apply(s, 1, "owner (test)")


def test_push_goes_only_to_session_branch(bench):
    s = bench.start("owner", "x")
    with pytest.raises(cp.PolicyError, match="nothing committed"):
        bench.push(s, "owner")
    bench.tool_propose_edit(s, "tool.py", "return 1", "return 2", "two")
    bench.apply(s, 1, "owner")
    assert bench.push(s, "owner") == s.branch
    heads = sh(bench.origin, "git", "for-each-ref", "--format=%(refname:short)", "refs/heads")
    assert set(heads.split()) == {"master", s.branch}
    assert sh(bench.origin, "git", "rev-parse", "master").strip() == s.base_sha


# ── the model only gets tools that propose ──

def test_model_tools_cannot_apply_or_push():
    names = {t["function"]["name"] for t in cp.TOOLS}
    assert names == {"list_files", "find_files", "read_file", "search", "propose_edit", "show_diff"}


def test_unknown_tool_is_an_error(bench):
    s = bench.start("owner", "x")
    p = cp.Partner(bench, chat=lambda m, msgs: {})
    assert "error" in p.run_tool(s, "apply", {"change": 1})


def test_turn_with_fake_model(bench):
    s = bench.start("owner", "x")
    script = iter([
        {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "tool.py"}}}]},
        {"content": '[TOOL_CALLS]propose_edit[ARGS]{"path": "tool.py", "search": "return 1", '
                    '"replace": "return 2", "why": "f returns two"}'},
        {"content": "I changed f to return two."},
    ])
    seen = []

    def fake_chat(model, messages):
        seen.append(messages)
        return next(script)

    p = cp.Partner(bench, chat=fake_chat)
    answer = p.turn(s, "make f return two")
    assert answer.startswith("I changed f to return two.")
    assert "Change 1 is ready: 2 changed lines in tool.py" in answer       # from code, not the model
    assert "Say 'apply change 1'" in answer
    assert s.pending()[0].why == "f returns two"
    assert seen[1][-1]["role"] == "tool" and "def f()" in seen[1][-1]["content"]


def test_refusal_is_returned_to_model_not_raised(bench):
    s = bench.start("owner", "x")
    p = cp.Partner(bench, chat=lambda m, msgs: {})
    res = p.run_tool(s, "read_file", {"path": ".env"})
    assert "refused" in res


@pytest.mark.parametrize("text, calls", [
    ('{"name": "search", "arguments": {"query": "studio"}}', 1),
    ('[{"name": "read_file", "arguments": {"path": "a"}}]', 1),
    ("Sure, I'll do that.", 0),
    ('{"name": "rm_rf", "arguments": {}}', 0),
])
def test_leaked_tool_calls(text, calls):
    assert len(cp.leaked_tool_calls(text)) == calls


# ── what owner says ──

@pytest.mark.parametrize("text, hit, num", [
    ("apply change 3", True, 3), ("Yes, apply it", True, None), ("apply change two", True, 2),
    ("commit that", True, None), ("yes", False, None), ("okay", False, None), ("apply", False, None)])
def test_apply_phrases(text, hit, num):
    m = cp.CMD_APPLY.match(text)
    assert bool(m) is hit
    if m:
        assert cp._num(m) == num


@pytest.mark.parametrize("text, hit", [("start a coding session", True), ("coding session on the README", True),
                                       ("let's code", True), ("code", False), ("studio session", False)])
def test_start_phrases(text, hit):
    assert bool(cp.CMD_START.match(text)) is hit


def test_handle_flow(bench):
    script = iter([
        {"tool_calls": [{"function": {"name": "propose_edit", "arguments": {
            "path": "README.md", "search": "# repo", "replace": "# GitOps", "why": "rename heading"}}}]},
        {"content": "Renamed the heading."},
        {"content": "Say 'apply change 1' to commit it."},
    ])
    p = cp.Partner(bench, chat=lambda m, msgs: next(script))
    r = cp.handle(p, "owner", "start a coding session on the readme")
    assert r["active"] and "branch" in r["answer"]
    r = cp.handle(p, "owner", "rename the heading to GitOps")
    assert "Change 1 is ready" in r["answer"]
    assert "nothing" not in cp.handle(p, "owner", "what's waiting")["answer"].lower()
    r = cp.handle(p, "owner", "yes")                   # a bare yes goes to the model, never applies
    assert bench.active("owner").pending()
    r = cp.handle(p, "owner", "apply change 1")
    assert "Applied change 1" in r["answer"]
    r = cp.handle(p, "owner", "push it")
    assert r["answer"].startswith("Pushed")
    assert "The branch is pushed." in cp.handle(p, "owner", "status")["answer"]
    r = cp.handle(p, "owner", "switch model to gpt-4")
    assert "don't have a model" in r["answer"]
    r = cp.handle(p, "owner", "switch model to primary")
    assert r["answer"] == "Switched to gpu01-primary."
    r = cp.handle(p, "owner", "end coding session")
    assert not r["active"] and not bench.active("owner")


def test_bare_yes_without_session(bench):
    p = cp.Partner(bench, chat=lambda m, msgs: {"content": "?"})
    assert cp.handle(p, "owner", "yes")["active"] is False


def test_audit_has_no_content(bench):
    s = bench.start("owner", "x")
    bench.tool_propose_edit(s, "tool.py", "return 1", "return 'very-distinct-text'", "two")
    bench.apply(s, 1, "owner")
    text = cp.AUDIT_LOG.read_text()
    assert "very-distinct-text" not in text
    assert {e["event"] for e in audit_events(cp.AUDIT_LOG)} >= {"session_start", "propose", "apply"}


def test_sessions_survive_restart(bench):
    s = bench.start("owner", "x")
    bench.tool_propose_edit(s, "tool.py", "return 1", "return 2", "two")
    again = cp.Workbench(POLICY, repo=bench.repo, state=cp.STATE_DIR)
    s2 = again.active("owner")
    assert s2 and s2.id == s.id and s2.pending()[0].edits[0].content.endswith("return 2\n")


def test_wrong_path_gets_suggestions(bench):
    s = bench.start("owner", "x")
    with pytest.raises(cp.PolicyError, match="Did you mean: Ansible/services.yml"):
        bench.tool_search(s, "studio", "ansible/services.yml")
    with pytest.raises(cp.PolicyError, match="There is no"):
        bench.tool_read_file(s, "nope/tool2.py")


def test_repo_map_lists_folders_not_denied_files(bench):
    s = bench.start("owner", "x")
    m = bench.repo_map(s)
    assert "Ansible/ (1)" in m and "Terraform/ (1)" in m


def test_claimed_edit_without_proposal_is_nudged_once(bench):
    s = bench.start("owner", "x")
    script = iter([
        {"content": "I'll change f to return two."},
        {"tool_calls": [{"function": {"name": "propose_edit", "arguments": {
            "path": "tool.py", "search": "return 1", "replace": "return 2", "why": "two"}}}]},
        {"content": "Done: f returns two."},
    ])
    seen = []

    def chat(m, msgs):
        seen.append(msgs)
        return next(script)

    answer = cp.Partner(bench, chat=chat).turn(s, "make f return two")
    assert seen[1][-1] == {"role": "system", "content": cp.NUDGE_PROPOSE}
    assert "Change 1 is ready" in answer


def test_claimed_edit_still_missing_is_said_by_code(bench):
    s = bench.start("owner", "x")
    answer = cp.Partner(bench, chat=lambda m, msgs: {"content": "I'll update the file."}).turn(s, "do it")
    assert answer.endswith("nothing is proposed right now, so there is nothing to apply.") and not s.pending()


# ── HTTP layer ──

def test_http_auth(bench, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(cp, "_partner", cp.Partner(bench, chat=lambda m, msgs: {"content": "hi"}))
    monkeypatch.setattr(cp, "ROUTER_KEY", "k" * 32)
    c = TestClient(cp.app)                                    # client host is "testclient", not the proxy
    assert c.get("/health").json()["ok"]
    assert c.post("/talk/turn", json={"speaker": "owner", "text": "x"}).status_code == 401
    r = c.post("/talk/turn", json={"speaker": "owner", "text": "start a coding session"},
               headers={"X-Code-Partner-Key": "k" * 32})
    assert r.json()["active"]
    assert c.get("/api/session", headers={"Remote-User": "owner"}).status_code == 403   # not via the proxy
    monkeypatch.setattr(cp, "TRUSTED_PROXIES", {"testclient"})
    assert c.get("/api/session", headers={"Remote-User": "owner"}).json()["active"]
    assert c.get("/api/session", headers={"Remote-User": "partner"}).status_code == 403
    assert c.post("/api/say", json={"text": "status"}, headers={"Remote-User": "owner"}).status_code == 403
    r = c.post("/api/say", json={"text": "status"}, headers={"Remote-User": "owner", "X-Code-Partner": "ui"})
    assert r.status_code == 200 and r.json()["active"]


def test_find_files_by_name(bench):
    s = bench.start("owner", "x")
    assert bench.tool_find_files(s, "services")["files"] == ["Ansible/services.yml"]
    assert ".env" not in bench.tool_find_files(s, "env")["files"]


def test_prompt_carries_repo_hints(bench):
    s = bench.start("owner", "x")
    seen = []
    cp.Partner(bench, chat=lambda m, msgs: seen.append(msgs) or {"content": "ok"}).turn(s, "hi")
    assert "deploy_studio01.yml" in seen[0][0]["content"]


@pytest.mark.parametrize("text", ["I'll propose changing it to 20.", "I'm going to set retries to 20."])
def test_more_edit_claims(text):
    assert cp.CLAIMS_EDIT.search(text)


def test_bare_yes_with_pending_change_is_answered_by_code(bench):
    s = bench.start("owner", "x")
    bench.tool_propose_edit(s, "tool.py", "return 1", "return 2", "two")
    p = cp.Partner(bench, chat=lambda m, msgs: (_ for _ in ()).throw(AssertionError("model must not run")))
    r = cp.handle(p, "owner", "Yes.")
    assert "say 'apply change 1'" in r["answer"] and s.pending()


def test_model_claiming_it_applied_is_corrected(bench):
    s = bench.start("owner", "x")
    answer = cp.Partner(bench, chat=lambda m, msgs: {"content": "Diff applied to the playbook."}).turn(s, "hm")
    assert answer.startswith("I can only propose changes") and "applied to the playbook" not in answer


# ── voice in the browser ──

@pytest.mark.parametrize("raw, kept", [("Thank you.", ""), ("Thank you. Apply change 1.", "Apply change 1."),
                                       ("Okay.", ""), ("start a coding session", "start a coding session")])
def test_phantoms_dropped(raw, kept):
    assert cp.clean_utterance(raw) == kept


def test_speakable_paths():
    assert cp.speakable("Change 1 in deploy_studio01.yml on domo/x") == "Change 1 in deploy studio01 dot yml on domo x"


def test_voice_endpoint(bench, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(cp, "_partner", cp.Partner(bench, chat=lambda m, msgs: {"content": "hi"}))
    monkeypatch.setattr(cp, "TRUSTED_PROXIES", {"testclient"})
    heard = iter(["Start a coding session on voice.", "Thank you."])
    monkeypatch.setattr(cp, "transcribe", lambda audio, fn, ct, prompt: next(heard))
    monkeypatch.setattr(cp, "synthesize", lambda text: b"RIFFfake")
    c = TestClient(cp.app)
    h = {"Remote-User": "owner", "X-Code-Partner": "ui"}
    r = c.post("/api/voice", files={"audio": ("v.webm", b"\x00" * 100, "audio/webm")}, headers=h).json()
    assert r["heard"].startswith("Start a coding session") and r["active"]
    r = c.post("/api/voice", files={"audio": ("v.mp4", b"\x00" * 100, "audio/mp4")}, headers=h).json()
    assert r["heard"] == "" and r["answer"] == ""                     # a phantom never reaches handle()
    assert c.post("/api/voice", files={"audio": ("v.webm", b"x", "audio/webm")},
                  headers={"Remote-User": "owner"}).status_code == 403   # no CSRF header
    r = c.post("/api/speak", json={"text": "hello"}, headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content == b"RIFFfake"


# ── found in the smoke test of 2026-11-06 (session ecde7288) ──

def test_leaked_call_without_prefix_is_run(bench):
    """Ministral printed 'propose_edit[ARGS]{...}' with no [TOOL_CALLS]: nothing was proposed all session."""
    calls = cp.leaked_tool_calls('propose_edit[ARGS]{"path": "NEW.md", "search": "", "replace": "# New\n", "why": "w"}')
    assert calls and calls[0]["function"]["name"] == "propose_edit"
    assert calls[0]["function"]["arguments"]["replace"] == "# New\n"     # raw newline inside a string is fine
    two = cp.leaked_tool_calls('read_file[ARGS]{"path": "a"} [TOOL_CALLS]search[ARGS]{"query": "b"}')
    assert [c["function"]["name"] for c in two] == ["read_file", "search"]


def test_garbled_tool_call_is_never_spoken(bench):
    s = bench.start("owner", "x")
    p = cp.Partner(bench, chat=lambda m, msgs: {"content": 'propose_edit[ARGS]{"path": "x.md", broken'})
    answer = p.turn(s, "write a summary")
    assert "[ARGS]" not in answer and "garbled" in answer


def test_claim_of_visible_diff_is_corrected(bench):
    s = bench.start("owner", "x")
    p = cp.Partner(bench, chat=lambda m, msgs: {
        "content": "I already proposed it; it's now visible as a pending diff on the owner's screen."})
    answer = p.turn(s, "proceed")
    assert "nothing is proposed right now" in answer


def test_underscores_in_file_names_survive(bench):
    s = bench.start("owner", "x")
    p = cp.Partner(bench, chat=lambda m, msgs: {"content": "Proposed GITOPS_REPO_SUMMARY.md, see _this_."})
    assert "GITOPS_REPO_SUMMARY.md" in p.turn(s, "x") 


@pytest.mark.parametrize("text, apply_hit", [
    ("Proceed with the proposed edit for GITOPS_REPO_SUMMARY.md.", True),
    ("Let's go ahead and commit this and push it as well as create a PR.", True),
    ("okay, apply the changes", True),
    ("apply this to the inventory too", False),
    ("commit message should mention the ticket", False),
])
def test_natural_apply_phrases(text, apply_hit):
    lm = cp.CMD_APPLY_LOOSE.match(text)
    assert bool(lm and cp.APPLY_TAIL_OK.match(lm.group("tail"))) is apply_hit


def test_apply_pushes_branch_and_commit_push_pr_in_one_sentence(bench):
    script = iter([
        {"tool_calls": [{"function": {"name": "propose_edit", "arguments": {
            "path": "README.md", "search": "# repo", "replace": "# GitOps", "why": "rename heading"}}}]},
        {"content": "Proposed renaming the heading."},
    ])
    p = cp.Partner(bench, chat=lambda m, msgs: next(script))
    cp.handle(p, "owner", "start a coding session")
    r = cp.handle(p, "owner", "commit this and push it")
    assert "Nothing is waiting to apply" in r["answer"]
    cp.handle(p, "owner", "rename the heading")
    s = bench.active("owner")
    r = cp.handle(p, "owner", "Let's go ahead and commit this and push it as well as create a PR.")
    assert r["answer"].startswith("Applied change 1") and "pushed" in r["answer"]
    heads = sh(bench.origin, "git", "for-each-ref", "--format=%(refname:short)", "refs/heads")
    assert s.branch in heads.split()                       # visible in Forgejo without a separate "push it"
    assert sh(bench.origin, "git", "rev-parse", s.branch).strip() == s.changes[0].commit


def test_plain_apply_pushes_too(bench):
    s = bench.start("owner", "x")
    bench.tool_propose_edit(s, "tool.py", "return 1", "return 2", "two")
    bench.apply(s, 1, "owner")
    assert s.pushed_sha == s.changes[0].commit
    assert sh(bench.origin, "git", "rev-parse", s.branch).strip() == s.pushed_sha


# ── live progress (CODE_PARTNER_NEXT.md step 1) ──

@pytest.fixture
def events(monkeypatch):
    ev = cp.Events()
    monkeypatch.setattr(cp, "EVENTS", ev)
    return ev


def test_turn_steps_are_streamed(bench, events):
    script = iter([
        {"tool_calls": [{"function": {"name": "find_files", "arguments": {"name": "tool"}}}]},
        {"content": 'propose_edit[ARGS]{"path": "tool.py", "search": "return 1", "replace": "return 2", "why": "two"}'},
        {"content": "Proposed f returning two."},
    ])
    p = cp.Partner(bench, chat=lambda m, msgs: next(script))
    cp.handle(p, "owner", "start a coding session")
    cp.handle(p, "owner", "make f return two", "voice")
    kinds = [e["kind"] for e in events.after("owner", 0)]
    assert kinds[:2] == ["heard", "answer"]                                # the start command
    turn = kinds[2:]
    assert turn[0] == "heard" and turn[-1] == "answer"
    assert turn.count("thinking") == 3 and "note" in turn                 # the leaked call is called out
    prop = next(e for e in events.after("owner", 0) if e["kind"] == "proposed")
    assert prop["change"] == 1 and "+    return 2" in prop["diff"]
    tool = next(e for e in events.after("owner", 0) if e["kind"] == "tool")
    assert tool["tool"] == "find_files" and tool["target"] == "tool" and tool["outcome"] == "1 files"
    assert events.after("owner", 10 ** 6) == events.after("owner", 0)          # id from before a restart: replay
    assert events.after("partner", 0) == []                                 # per person


def test_no_tool_and_refusal_are_visible(bench, events):
    s = bench.start("owner", "x")
    p = cp.Partner(bench, chat=lambda m, msgs: {"content": "Sure, done."})
    p.turn(s, "write a summary")
    assert [e["kind"] for e in events.after("owner", 0)][-1] == "no_tool"
    p.run_tool(s, "read_file", {"path": ".env"})
    last = events.after("owner", 0)[-1]
    assert last["kind"] == "tool" and not last["ok"] and last["outcome"].startswith("refused")


def test_apply_and_push_are_commands_in_the_feed(bench, events):
    s = bench.start("owner", "x")
    bench.tool_propose_edit(s, "tool.py", "return 1", "return 2", "two")
    bench.apply(s, 1, "owner (ui)")
    texts = [e["text"] for e in events.after("owner", 0) if e["kind"] == "command"]
    assert texts[0].startswith("Change 1 committed") and texts[1].startswith(f"Branch {s.branch} pushed")


def test_events_endpoint_streams_only_your_steps(bench, events, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(cp, "_partner", cp.Partner(bench, chat=lambda m, msgs: {"content": "hi"}))
    monkeypatch.setattr(cp, "SSE_MAX_SECONDS", 0.5)
    c = TestClient(cp.app)
    assert c.get("/api/events", headers={"Remote-User": "owner"}).status_code == 403     # not via the proxy
    monkeypatch.setattr(cp, "TRUSTED_PROXIES", {"testclient"})
    events.emit("owner", "heard", text="hello", channel="ui")
    events.emit("partner", "heard", text="not yours", channel="ui")
    r = c.get("/api/events", headers={"Remote-User": "owner"})
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "event: step" in r.text and "hello" in r.text and "not yours" not in r.text
    r = c.get("/api/events", headers={"Remote-User": "owner", "Last-Event-ID": "1"})
    assert "hello" not in r.text                                                     # resumes after id 1
