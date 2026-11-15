#!/bin/bash
# =============================================================================
# patch_window.sh — the scheduled patch window (docs/BACKUP_PATCH_RESTORE_PLAN.md)
#
#   0. Queue:      on PATCH_QUEUE_WEEKDAYS (date +%u, default 6 = Saturday) every VM
#                  in the patchable_vms group is set patch_status=pending in NetBox
#                  (netbox_setup_patch_fields.yml: seed_vms, then reset_pending).
#                  Added 2026-10-16: until then nothing filled the queue, and in two
#                  months three VMs had been patched, by hand-queueing.
#   1. VMs:        rolling_update.yml  — every NetBox VM with patch_status=pending
#                  that passes the backup gate (newest vzdump < 26h, none running).
#                  Failing the gate leaves it pending for the next window.
#   2. Containers: container_update.yml — opt-in stacks (container_patch_stacks),
#                  same backup gate per host, rollback on failed validation.
#
# Run by the systemd --user timer patch-window.timer (05:00 Europe/Berlin).
# Own slots: patch-window-ha.timer (Sunday 06:00) runs this with PATCH_HOST=home-assistant,
# PATCH_QUEUE_GROUP=patchable_vms_sunday, PATCH_QUEUE_WEEKDAYS=7, PATCH_SKIP_CONTAINERS=1.
# patch-window-gpu02.timer (Saturday 05:45) does the same for gpu-02 (PATCH_QUEUE_GROUP=patchable_vms_gpu02,
# PATCH_QUEUE_WEEKDAYS=6), after pve-03's 03:00 vzdump. docs/AUTOMATED_PATCHING.md.
# Logs: ~/patch-logs/<date>_<time>-{vms,containers}.log (kept 60 days).
# Result: one Matrix notice in "🚨 System Alerts" (patch_notify.py), when there is news.
# =============================================================================
set -uo pipefail
IAC=/home/netops/GitLab/GitOps/Infrastructure/IaC
LOGDIR="$HOME/patch-logs"; mkdir -p "$LOGDIR"
STAMP=$(date +%F_%H%M)

# Read only the keys we need, byte-exact (never `source` the file: it mangles $ ! `).
for k in VAULT_ADDR VAULT_TOKEN VAULT_ROLE_ID VAULT_ROLE_SECRET_ID; do
  v=$(grep -m1 "^$k=" /opt/vault/.env | cut -d= -f2- | sed -E "s/^[\"']//; s/[\"']$//")
  export "$k=$v"
done
export ANSIBLE_FORCE_COLOR=0 ANSIBLE_NOCOLOR=1
# PATCH_DRY_RUN=1 ./patch_window.sh  -> full path (queue, gates, detection), no changes.
DRY=()
[ "${PATCH_DRY_RUN:-0}" = "1" ] && DRY=(-e dry_run=true -e container_patch_dry_run=true) && STAMP="$STAMP-DRYRUN"
# Own-slot runs (2026-10-23): queue another inventory group, patch only one VM, skip containers.
QGROUP="${PATCH_QUEUE_GROUP:-patchable_vms}"
HOSTSEL=()
[ -n "${PATCH_HOST:-}" ] && HOSTSEL=(-e "patch_host=$PATCH_HOST")

# FIXED 2026-09-29: the first real window (05:00) ran Debian's /usr/bin ansible 2.10.8,
# because systemd --user's PATH has no ~/.local/bin -> "couldn't resolve module
# community.docker.docker_compose_v2_pull" + six.moves errors, nothing ran. Pin the
# ansible-core that everything was tested with, and refuse anything older.
export PATH="$HOME/.local/bin:$PATH"
AP="$HOME/.local/bin/ansible-playbook"
ver=$("$AP" --version 2>/dev/null | sed -n 's/.*core \([0-9]*\.[0-9]*\).*/\1/p')
if [ -z "$ver" ] || [ "$(printf '%s\n2.15\n' "$ver" | sort -V | head -1)" != "2.15" ]; then
  echo "[$(date -Is)] ABORT: need ansible-core >= 2.15 at $AP, found '${ver:-none}'" | tee -a "$LOGDIR/$STAMP-summary.log"
  exit 2
fi

cd "$IAC/Ansible" || exit 1
echo "[$(date -Is)] patch window start (ansible-core $ver)" | tee -a "$LOGDIR/$STAMP-summary.log"

q_rc=0
if [[ " ${PATCH_QUEUE_WEEKDAYS:-6} " == *" $(date +%u) "* ]]; then
  if [ "${PATCH_DRY_RUN:-0}" = "1" ]; then
    echo "[$(date -Is)] queue day: DRY RUN, would set patch_status=pending on $QGROUP" | tee -a "$LOGDIR/$STAMP-summary.log"
  else
    { "$AP" -i inventory/services.yml playbooks/netbox_setup_patch_fields.yml --tags seed_vms -e "patch_queue_group=$QGROUP" &&
      "$AP" -i inventory/services.yml playbooks/netbox_setup_patch_fields.yml --tags reset_pending -e "patch_queue_group=$QGROUP"; } > "$LOGDIR/$STAMP-queue.log" 2>&1
    q_rc=$?
    echo "[$(date -Is)] queue day: $QGROUP set pending (rc=$q_rc)" | tee -a "$LOGDIR/$STAMP-summary.log"
  fi
fi

"$AP" -i inventory/services.yml playbooks/rolling_update.yml "${DRY[@]}" "${HOSTSEL[@]}" > "$LOGDIR/$STAMP-vms.log" 2>&1
vm_rc=$?
ct_rc=0
if [ "${PATCH_SKIP_CONTAINERS:-0}" = "1" ]; then
  echo "[$(date -Is)] containers: skipped (own-slot run)" > "$LOGDIR/$STAMP-containers.log"
else
  "$AP" -i inventory/services.yml playbooks/container_update.yml "${DRY[@]}" > "$LOGDIR/$STAMP-containers.log" 2>&1
  ct_rc=$?
fi

{
  echo "[$(date -Is)] queue rc=$q_rc, VMs rc=$vm_rc, containers rc=$ct_rc"
  echo "--- VM gate / results:"
  grep -E '"msg": ".*(PASS|SKIP|patched successfully|FAILED|DRY RUN)' "$LOGDIR/$STAMP-vms.log" | sed 's/^ *"msg": //' || true
  echo "--- containers:"
  grep -E '"msg": ".*(newest backup|already on the newest|updated:|rolled back|WOULD)' "$LOGDIR/$STAMP-containers.log" | sed 's/^ *"msg": //' || true
  echo "--- recap:"
  sed -n '/PLAY RECAP/,$p' "$LOGDIR/$STAMP-vms.log" "$LOGDIR/$STAMP-containers.log" | grep -E ':\s+ok=' || true
} | tee -a "$LOGDIR/$STAMP-summary.log"

# One notice in "🚨 System Alerts" (scripts/patch_notify.py): on queue days, on any
# failure or rollback, and when something was patched -- quiet mornings stay quiet.
python3 "$IAC/scripts/patch_notify.py" "$STAMP" "$q_rc" "$vm_rc" "$ct_rc" >> "$LOGDIR/$STAMP-summary.log" 2>&1 \
  || echo "[$(date -Is)] Matrix notice could not be sent" >> "$LOGDIR/$STAMP-summary.log"

find "$LOGDIR" -type f -mtime +60 -delete
exit $(( q_rc != 0 || vm_rc != 0 || ct_rc != 0 ))
