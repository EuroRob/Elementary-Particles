# Agents as infrastructure

How the AI agents are kept in check, and how pull requests are checked before a person reads them. Back to the [README](../../README.md).

Two AI agents work on this repository and both are treated as infrastructure with a written
access model rather than as a chat window:

| | Claude Code (cloud) | Code partner (on premise) |
| --- | --- | --- |
| **Does** | Builds the infrastructure: Terraform, Ansible, docs, scripts. Co-author of 447 of the 947 commits on the main branch (67 % since it started in July 2026). | Turns a spoken or typed request into a diff on its own branch and a draft pull request. Runs on a local model. |
| **May never touch** | Secrets outside its Vault lane; see [agent access lanes](../AGENT_ACCESS_LANES.md), [`vault/lanes.yml`](../../vault/lanes.yml) and the generated [policies](../../vault/policies). | `.env`, state files, keys, its own code and policy, CI, `CLAUDE.md`. No tool to apply, commit, push or run anything; no network beyond three hosts. See [code partner](../CODE_PARTNER.md) and [`code_partner_policy.yml`](../../integration/code_partner_policy.yml). |
| **A person approves** | Every change lands as a pull request on a protected main branch. A `checks` job ([`.forgejo/workflows/ci.yml`](../../.forgejo/workflows/ci.yml) running [`scripts/ci/check.sh`](../../scripts/ci/check.sh): YAML, Python, shell, `terraform fmt`, Ansible syntax, the offline tests, gitleaks) must be green before a person reviews and merges. | Apply, push and "open the pull request" are fixed phrases or buttons a person uses; merging is not in the code at all. |

### Keeping pull requests honest

Three small pieces, on two machines, so that a person only reviews work a machine has already checked:

| Piece | Where it runs | What it does | What it can touch |
| --- | --- | --- | --- |
| **`checks`** ([`ci.yml`](../../.forgejo/workflows/ci.yml)) | Forgejo Actions runner on the repo-lane VM | Runs [`scripts/ci/check.sh`](../../scripts/ci/check.sh) on every pull request. The gate itself (`scripts/ci/`, `.gitleaks.toml`) is taken from the **base branch**, so a pull request cannot weaken the checks that judge it; a change to the gate is flagged on the PR. Required to merge. | A per-job token. No stored credential, network pinned to the git host. |
| **`drift`** ([`drift.yml`](../../.forgejo/workflows/drift.yml), [`drift.py`](../../scripts/ci/drift.py)) | Same runner | After every merge and every six hours, merges each open pull request onto the *current* main branch and re-runs the checks. Sets a `checks-drift` status and comments only when the verdict changes. Advisory, deliberately not a required check. | The same per-job token. |
| **PR watch** ([n8n workflow](../../n8n/workflows)) | n8n, a different host | Hourly: lists open pull requests and posts to the alerts room when one is older than 24 hours, has no or failing checks, fails against the current main branch, or is a draft. | A read-only bot account. It cannot write to the repository. |

The runner can run code but holds no secrets; the watcher holds a credential but can only read. Neither is a model: the checks are deterministic, and a person reads and merges. The agent that writes the code is separate and described above.

The rule behind both: **AI suggests, checks code, people decide.** Every guardrail that matters
is enforced in code (path deny-lists, exactly-once edit matching, re-parsing, a secret scan on
every proposed line), not by asking the model nicely. The instructions Claude Code works from
are the root [`CLAUDE.md`](../../CLAUDE.md). What the agents produced, and how often it had to be
fixed, is measured from git history in [agent metrics](../AGENT_METRICS.md)
(`scripts/agent_metrics.py` recomputes it on any clone).

n8n is the orchestration hub: 20 live workflows are exported to git with secrets redacted
([`n8n/workflows/`](../../n8n/workflows), [operations](../N8N_OPERATIONS.md)), including the
NetBox → n8n → Ansible patch pipeline with a backup gate and rollback, and a watchdog that checks
the database path n8n's own `/healthz` does not.
