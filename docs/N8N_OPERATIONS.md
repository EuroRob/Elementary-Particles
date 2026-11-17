# n8n — operations: watchdog + workflow export

n8n (web-01, `n8n.home.example.org`) is the homelab's orchestration hub. Two safeguards were
added on 2026-10-03 after its DB pool wedged for ~2 h unnoticed
(`incident_n8n_db_pool_wedged_2026-10-03`).

## 1. Watchdog — DB-aware health check + self-healing

| | |
|---|---|
| Code | `Ansible/Roles/n8n_watchdog/files/n8n_watchdog.py` (stdlib only) |
| Deploy | `ansible-playbook -i inventory/services.yml playbooks/deploy_n8n_watchdog.yml` |
| Runs | systemd `n8n-watchdog.timer` on web-01, every 2 min (`/opt/n8n-watchdog/`, root-only) |
| Logs | `journalctl -u n8n-watchdog.service` (silent while healthy) |
| Alerts | System Alerts Matrix room via the dedicated `alertmanager-bot` (Vault `secret/family-messaging/alertmanager-bot`) — **never through n8n** |

**Why not `/healthz`:** in n8n 2.34 `/healthz` always returns `{"status":"ok"}` — it never
touches the DB, which is how the incident went unnoticed. The watchdog requires BOTH:
1. `/healthz/readiness` = 200 (n8n's own `connected && migrated` flags), and
2. `GET /api/v1/workflows?limit=1` with the API key = 200 — validating the key is a real DB
   round trip, exactly what returned 401/timeouts during the incident.

**Behavior:** 3 consecutive failures (~6 min) → `docker restart n8n` → wait ≤120 s →
Matrix: "restarted, healthy again" or "NOT healthy, needs manual attention". At most one
automatic restart per 30 min; while still failing after that it only re-alerts hourly (no
restart loops). A container that is stopped, restarting or younger than 3 min is left alone
(deploys and deliberate stops are not "failures"). A recovery after an alert is announced.

**Tested 2026-10-03:** healthy run (silent); simulated incident with a bogus API key
(`API -> 401`), dry-run, threshold 1 → decided to restart without touching n8n; one real test
post to the Matrix room succeeded.

**Dry run / tuning:** role vars `n8n_watchdog_dry_run`, `n8n_watchdog_fail_threshold`,
`n8n_watchdog_restart_cooldown_s`, `n8n_watchdog_interval`.

## 2. Workflow export to git

```
python3 IaC/scripts/n8n_export_workflows.py          # write n8n/workflows/
python3 IaC/scripts/n8n_export_workflows.py --check  # drift check: exit 1 if git != n8n
```

One file per workflow (`<slug>__<id>.json`), keys sorted, volatile fields dropped
(versionId, timestamps, pinData, staticData, …) so diffs show real changes. **Review, then
commit by hand** — n8n is the source of truth; git is the versioned copy. Restore a flow via
`POST /api/v1/workflows` (re-enter any redacted literal).

**Secrets:** values under secret-sounding keys and token-shaped strings are replaced by
`<REDACTED>`; only their location is printed. A second high-entropy pass on 2026-10-03 found
nothing else (remaining hits were Matrix room-send URLs and iac-api paths).

### Found by the first export (2026-10-03): 6 hard-coded secrets inside workflows

They lived in plain text in n8n's DB and in every n8n export/backup. **2026-10-03:** the
catalog-api key moved to Vault `secret/download-catalog/api` (`api_key`) and the n8n credential
**`catalog-api X-API-Key`** (`httpHeaderAuth`, generic credential type); all 4 nodes were rewired
(backup in `~/n8n-backups/20261003-214302-secrets-to-credentials/`). The HA-router copies are
**archived**, and n8n rejects edits to archived workflows ("Cannot update an archived
workflow"), so they were **deleted** on 2026-10-03 at the user's request (JSON backups in
`~/n8n-backups/` and git history). Result: 26 workflows, **0** literal secrets.
Verification for the catalog flows comes from the next Daily Digest run (23:30, when pve-02,
and with it dl-01, is powered on):

| Workflow | Active | Literal |
|---|---|---|
| Download Catalog - Import Webhook | yes | `X-API-Key` header |
| Download Catalog - Daily Digest | yes | `X-API-Key` header ×2 |
| Download Catalog - Search | yes | `X-API-Key` header |
| HA → LLM Router (×2 copies) | no | `Authorization` header |

## 3. Health probes + canaries (all of the stack, not just n8n)

See `Roles/ep_probes` (`deploy_ep_probes.yml`): 14 dependency-aware probes every minute and 6
daily canary questions (07:00 Europe/Berlin) through the real front door. They are alert-only;
n8n remains the only service that restarts itself. Add a probe by adding an entry to
`ep_probes` in the role defaults.

```
sudo bash -c 'set -a; . /opt/ep-probes/probes.env; set +a; \
  python3 /opt/ep-probes/ep_probe.py --config /opt/ep-probes/probes.json --group health --force --dry-run'
journalctl -u ep-probes@health -u ep-probes@canary --since today
```
