# Code partner: next steps (live progress, undo, checks that run, LiteLLM)

**Status 2026-11-06:**
- **Step 1:** being built, on branch `code-partner-progress`.
- **Steps 2–4:** planned.

Builds on [CODE_PARTNER.md](CODE_PARTNER.md), which describes what is live and the smoke-test fixes of 2026-11-06.

## Why, and the reference

The goal is a code partner you can use **by voice, on the go**: dictate a change through DOMO, hear what it did, look at the
diff later.

[cloudflare/vibesdk](https://github.com/cloudflare/vibesdk) is the reference: Cloudflare's open-source "build an app by
talking" platform. We take its ideas, not its software. Its runtime is Cloudflare-only (Durable Objects, Workers for
Platforms, Cloudflare Artifacts, AI Gateway), and even local development needs a Cloudflare account. That breaks the
on-premise rule.

| VibeSDK | Here |
|---|---|
| `ThinkAgent`: the model/tool loop | `Partner.turn()` on code-01 |
| `SpaceDO`: an isolated workspace per project | One git worktree per session |
| Streams tool calls and file changes to the UI | **Step 1** |
| Artifacts: restore points, rollback as a new commit | **Step 2** |
| Verify and repair: run, read the errors, fix | **Step 3** |
| AI Gateway: model routing | **Step 4**, LiteLLM, which already runs on web-01 |
| No bash for the agent, only explicit tools | Same (six tools); stays that way |

**What doesn't change:** AI suggests, checks code, people decide.
- The model gets no shell.
- Applying, undoing and opening the PR are things owner says or clicks; code matches the phrase, not the model.
- Master stays protected in Forgejo.

## Step 1: live progress on code.home.example.org

**Problem.** The page shows only the final answer, after up to 120 s. On 2026-11-06 the model never called a tool for a
whole session, and nothing on screen showed it.

**Build.**
- **Event feed.** An in-memory feed per person (the last 200 events), filled by code at each step:

  | Event | When |
  |---|---|
  | `heard` | What arrived, and from where: voice through DOMO, the page's mic, or typed |
  | `thinking` | A model round started (round *n* of the limit), with the seconds left |
  | `tool` | Which tool ran, on which file or search, and the result in one line: ok, refused (why), or error |
  | `proposed` | Change *n*, its files and line count, and the diff |
  | `no_tool` | The model answered without using any tool. This is the 2026-11-06 case, now visible |
  | `answer` | The reply as spoken |
  | `command` | apply, push, undo or PR, and what code did |

- **Live stream.** `GET /api/events` streams the feed (Server-Sent Events), behind the same Authelia login as the
  page. The browser reconnects by itself and resumes from the last event it saw (`Last-Event-ID`).
- **The page.** A "Working…" panel lists the current turn's steps live, with a timer. The page reloads the session when
  a turn ends. The 4-second poll becomes a 20-second fallback.
- **Voice turns show too.** Turns through DOMO from the phone appear on the page as well, so an open laptop or iPad
  follows a conversation held on the phone.
- **What the feed carries.**
  - It carries diffs, because the page is behind two-factor login.
  - It is never written to disk. `audit.jsonl` stays content-free.
  - It is gone on restart; the session JSON keeps the history.

**Not in step 1:** token-by-token text. Ollama returns a tool call only once complete, so a tool step is the smallest
useful unit.

## Step 2: undo as a new commit

- **"Undo change 2" / "revert change 2" / an Undo button.** Code runs `git revert --no-edit <commit>` on the session's
  `domo/` branch and pushes it.
- **History is never rewritten:** no reset, no force push. That matches VibeSDK's rollback and Forgejo's protection.
- **Refusals:**
  - the change isn't applied, or is already reverted;
  - the revert conflicts with a later change. The reply says which change to undo first.
- **Recording.** The revert becomes its own entry (`status: reverted`, `reverted_by: <commit>`). Commit message:
  `DOMO: revert change 2 (…)` with `Approved-by:`.
- **Effort:** small. About 40 lines plus tests.

## Step 3: checks that run, and a repair loop

The policy already reserves this (`pytest: false  # only with a sandbox`). Two facts decide the design:
- code-01 has neither bubblewrap nor podman;
- the service's own unit blocks everything but three IPs.

**The sandbox is a second systemd unit**, `code-check@.service`, started through a spool directory. It's the same
trigger-file pattern as the iac-api runner.

1. **Job.** On a proposal, code-partner writes a job into `/var/lib/code-partner/checks/in/<id>/`: a copy of the
   worktree with the pending change in it, plus `job.json` listing which checks.
2. **Trigger.** A `.path` unit starts `code-check@<id>` as root-managed systemd. It runs with:
   - `DynamicUser=yes`, `PrivateNetwork=yes` (no network at all), `ProtectSystem=strict`;
   - `ReadOnlyPaths=` the job copy, `InaccessiblePaths=/etc/code-partner` (no Forgejo key, no router key);
   - `MemoryMax=768M`, `CPUQuota=150%`, `RuntimeMaxSec=180`.
3. **Checks**, chosen by which files the change touches:

   | Files | Check |
   |---|---|
   | Ansible | `ansible-playbook --syntax-check` on the affected playbook, `ansible-lint` on the role |
   | Python | pytest on the matching test file (the `test_<module>.py` next to it) |
   | Terraform | `terraform validate` with providers from a local mirror built at deploy time. There's no network, so no `terraform init` download |

4. **Result.** `result.json` holds pass/fail per check and the first 40 lines of each failure.
5. **Repair loop.**
   - The model gets the result as a tool reply (`check_results`) and may propose a fix.
   - At most **2 repair rounds** per turn, inside the turn's time box.
   - The spoken answer says what failed and whether it's fixed.
   - Apply still waits for owner.
6. **Also** a `run_checks` tool the model may call, and "run the checks" as a phrase owner can say.

**RAM.** code-01 has 2 GB, of which the service uses about 50 MB idle and up to 1 GB at its cap. `ansible-lint` is the
heavy one. Measure on the first build; resize the VM to 3 GB only if needed (the Terraform env already allows it).

**Effort:** medium.
- the role: new unit, venv with ansible-core, ansible-lint and pytest, plus the provider mirror;
- about 200 lines in `code_partner.py`;
- tests with a fake runner.

## Step 4: models through LiteLLM (evaluate, then switch)

**Already there.** LiteLLM runs on web-01 (`litellm` role, port 4100) with aliases. It includes `coder-llm`
(`qwen2.5-coder:14b` on gpu-02), and code-01 may already reach web-01.

**What it would give the code partner:**
- **Switch by alias.** "Switch model to coder" picks a LiteLLM alias. Moving a model between GPUs is one line in
  `config.yaml.j2`, not a change on code-01.
- **One log.** Tokens, latency and errors per call, next to DOMO's other consumers.
- **Fallbacks.** If gpu-01 is busy or down, use another alias instead of failing the turn.

**Questions to answer before switching** (an evaluation run, not assumptions):

1. **Tool calls through LiteLLM.** All aliases use the `ollama/` prefix, which goes through Ollama's generate
   endpoint. Native tool calls need `ollama_chat/`.
   - Test both with the code partner's six tools.
   - Count how often calls leak as text (the 2026-11-06 failure).
2. **Context.** Does `num_ctx` reach Ollama through the OpenAI-compatible route? The HA alias avoided the question by
   baking context into a `-ctx16k` model tag. A request that reloads gpu01-primary at another context stalls DOMO
   ([[ollama num_ctx]]).
3. **Coder model vs gpu-01.** gpu-01 keeps `gpu01-primary` resident for DOMO. A second large model there evicts it, and
   DOMO stalls while it reloads. Options:
   - **(a) coder model on gpu-02**, behind `coder-llm`. gpu-02 is woken on demand and holds DOMO's router model.
     Measure VRAM with both loaded.
   - **(b) time window:** the coder model only while a coding session is open, with DOMO's primary as fallback. That
     costs a reload on every switch.
   - **(c) stay on gpu01-primary** and spend the effort on steps 1–3, which help any model.

   **Decide on numbers:** tool-call success, leaks, seconds per turn, and VRAM, on a fixed test set of 10 requests.
4. **Auth.** code-01 would need a LiteLLM key with access to the coding aliases only. Ansible copies it at deploy time,
   like the Forgejo key. The `IPAddressAllow` list doesn't change.

**Time-boxing and batching.** These apply whichever model wins:
- Each turn has 120 s and 10 model rounds.
- **Batching:** say several requests in one go. The partner works through them one proposal at a time and lists what
  is ready when it's done.
- **Later:** a background turn. "I'll tell you when change 3 is ready": DOMO speaks or sends a notification when it's
  done, so the phone conversation needn't wait.

## Ideas from other projects (reviewed 2026-11-07)

**[vibecoder](https://github.com/dublyo/vibecoder)** is not adopted.
- It's young: 10 commits, 5 stars.
- It uses only cloud models through OpenRouter, and it's built to generate and deploy new Next.js apps.
- Its license is fair-code, not open source.

Three of its ideas fit our weak spot, a 14B model with a 16K window doing slow tool rounds:

| Idea | Here | Where |
|---|---|---|
| **Classify first, then pick a path** | A cheap first call decides "simple edit" (one setting, one file) vs "task". Simple ones go straight to `propose_edit` with a short prompt; tasks get the full loop. | Step 4: the classifier is a small alias in LiteLLM |
| **Code selects the files** | Before the model starts, code picks 3–10 candidate files: ripgrep on the request's words, the policy's `repo_hints`, and files changed recently on the branch. They go into the prompt as a short list with first lines. That saves the `find_files`/`search` rounds that ran out of steps in the 2026-11-05 voice test. | Step 4, or before it: it helps `gpu01-primary` too |
| **Plan before diff (task path only)** | For voice: "I'd change X in A and Y in B, OK?" owner corrects the plan in one sentence, then the partner proposes. Not for simple edits, which stay one round. | After step 3 |

Its build-and-fix loop ("Ralph") is our step 3, and its atomic multi-file commits are what `apply` already does.
Live preview, deploy, web research and team credits don't apply to this repo.

**[Vibe coding with a local LLM (dev.to)](https://dev.to/sreeraj-sreenivasan/your-guide-to-vibe-coding-with-a-local-llm-10pm)**
was a sanity check, and gpu-01 already follows its advice:
- Ollama with `OLLAMA_FLASH_ATTENTION=1`.
- The model is kept resident (`KEEP_ALIVE=-1`), which covers its "pre-warm" tip.
- A 14B at Q4_K_M, its recommended trade-off.
- 16K context, against its 8K; we need it for repo files.

Its coding-model table (Qwen2.5-Coder 7B/14B/32B) supports option (a) of step 4. gpu-01 runs at 14.5 of 16 GB, so a
second 14B doesn't fit there. The article says nothing about tool calling, our hardest problem.

One thing to measure in step 4 that the article doesn't mention: `OLLAMA_KV_CACHE_TYPE=q8_0`. With flash attention
already on, it roughly halves context memory. That could fit 32K context, or a little headroom, on gpu-01 without
changing the model. Measure quality and VRAM before keeping it. `OLLAMA_NUM_PARALLEL=2` doubles the context
allocation as well.

## Order

| # | What | Status |
|---|---|---|
| 0 | Smoke-test fixes (CODE_PARTNER.md) | Deployed 2026-11-06, branch `code-partner-smoke` |
| 1 | Live progress | Building |
| 2 | Undo | Next |
| 3 | Checks + repair loop | After 2 |
| 4 | LiteLLM evaluation; coder model decision | Can run alongside 2–3; switch only on numbers |
| 4a | File pre-selection by code; classify → simple/task path | With 4; pre-selection can come first |
| 5 | Plan before diff (task path, voice) | After 3 |
