# Alerting pipeline: self-heal + dead man's switch

Added 2026-10-22. Alertmanager on web-01 sat **dead for 3 days**
(2026-10-19 03:42 → 2026-10-22 18:12) after web-01 went down hard during a
patch run: on boot, dockerd restarted every other `unless-stopped`
container but only removed Alertmanager's stale sandbox, with no error.
Prometheus kept evaluating rules; nothing was delivered. The same thing
happened after the 2026-09-08 pve-01 incident. A restart policy alone is
not enough, and an alert about Alertmanager can't be delivered *by*
Alertmanager.

## Layers

| Layer | Where | Catches | Notifies via |
|---|---|---|---|
| Self-heal timer `monitoring-watchdog.timer` | web-01 (systemd, 3 min after boot, then every 2 min) | alertmanager / prometheus / ha-bridge not running; alertmanager running but not `/-/ready` | HA REST API directly (rate-limited 1 per container per 30 min) |
| Dead man's switch | Prometheus `Watchdog` (always firing) → Alertmanager route `ha-deadman` every 2 min → HA webhook | anything that stops heartbeats: Alertmanager or Prometheus down, web-01 down, routing broken, self-heal failed | HA automation `Alerting deadman alarm` → phone (time-sensitive), repeats hourly; `✅ recovered` push when heartbeats return |
| `AlertmanagerNotificationsFailing` | Prometheus scrapes `alertmanager:9093` | a receiver failing repeatedly (e.g. n8n → Matrix) | default receiver (n8n-matrix **and** ha-bridge) |

## Pieces

- Repo: `Ansible/Roles/monitoring` — `templates/alertmanager.yml.j2`
  (route + `ha-deadman` receiver), `templates/rules/critical_infra.yml.j2`
  (`alerting_pipeline` group), `templates/prometheus.yml.j2` (`alertmanager`
  job), `tasks/watchdog.yml`, `files/monitoring_watchdog.sh`.
- Vault: `secret/home-assistant/deadman` → `webhook_id`.
- Home Assistant (created via REST config API, not in the repo):
  - `automation.alerting_deadman_heartbeat` — webhook trigger (local only,
    POST). Its `last_triggered` **is** the heartbeat record.
  - `automation.alerting_deadman_alarm` — every 5 min; fires if the
    heartbeat is >10 min old and it hasn't already alerted since the last
    heartbeat (or it's been >1 h).
  - Both notify `notify.mobile_app_robs_iphone`.

## Operating it

- **Planned Alertmanager/Prometheus maintenance:** `sudo touch
  /etc/monitoring-watchdog.pause` on web-01 (remove afterwards), otherwise
  the timer starts the container again within 2 min. Stopping for >10 min
  also triggers the HA alarm — expected; you'll get `✅ recovered` after.
- **Never silence `Watchdog`** in Alertmanager — a silence is
  indistinguishable from an outage and will page.
- Logs: `journalctl -u monitoring-watchdog` on web-01.
- Applying role changes: the full monitoring role pulls `:latest` images and
  its "Create directories" task chowns the data dirs to root before later
  tasks chown them back (pre-existing, not yet fixed) — prefer a targeted
  apply with `validate:` (amtool / promtool), and restart (not HUP) the
  container after changing a single-file bind-mounted config.

## Verified 2026-10-22

- Heartbeats from Alertmanager land in HA every ~2–3 min.
- Alarm logic evaluated via HA `/api/template` (false at 3 s silence, true
  with a simulated 11 min gap).
- `docker stop alertmanager` at 18:20:38 → watchdog restarted it at
  18:21:38, `/-/ready` at 18:21:44, phone push sent, heartbeats resumed,
  alarm did not fire.

## Known residual gaps

- If Home Assistant itself is down, the dead man's switch is silent too
  (HA is both the receiver and the notifier).
- web-01, HA and fw-01 all run on pve-01: a full pve-01 outage takes out
  everything, including the notification path. Only an off-site check
  (e.g. a hosted dead man's switch) would cover that.
