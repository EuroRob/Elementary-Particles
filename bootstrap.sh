#!/usr/bin/env bash
# =============================================================================
# bootstrap.sh — First-run stack bootstrap (Layers 1-4)
#
# Runs from the IaC VM. Deploys the foundational layers directly via
# Terraform + Ansible, WITHOUT needing n8n (which doesn't exist yet).
#
# After this script completes:
#   - fw-01 (OPNSense) is running         [manual step — see STEP 0]
#   - dns-01 (Pi-hole) is serving DNS
#   - vpn-01 (WireGuard) is reachable
#   - nas-01 NFS shares are exported
#   - pg-01 (PostgreSQL + Elasticsearch) is ready
#   - web-01 (Traefik + n8n + Prometheus + Grafana + NetBox) is up
#
# Then: import full_stack_deployment.yaml into n8n on web-01, and use it
#       to deploy Layers 5-8 (GitLab, apps, GPU, media).
#
# Usage:
#   export TF_VAR_role_id=<vault-role-id>
#   export TF_VAR_secret_id=<vault-secret-id>
#   export VAULT_ADDR=http://vault.internal:8200
#   bash IaC/bootstrap.sh
#
#   Resume from a specific layer:
#   bash IaC/bootstrap.sh --start-layer 3
# =============================================================================

set -euo pipefail

IAC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INVENTORY="${IAC_DIR}/ansible/inventory/services.yml"
TF_DIR="${IAC_DIR}/terraform/envs"
ANSIBLE="${IAC_DIR}/ansible"

# Point Ansible at the correct config + roles path regardless of cwd
export ANSIBLE_CONFIG="${ANSIBLE}/ansible.cfg"
export ANSIBLE_ROLES_PATH="${ANSIBLE}/roles"

# ANSI colours
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

START_LAYER=${START_LAYER:-1}
DRY_RUN=${DRY_RUN:-false}

while [[ $# -gt 0 ]]; do
  case $1 in
    --start-layer) START_LAYER="$2"; shift 2 ;;
    --dry-run)     DRY_RUN=true; shift ;;
    *) error "Unknown argument: $1" ;;
  esac
done

TF_PLAN_FLAG=$([[ "$DRY_RUN" == "true" ]] && echo "-target=null" || echo "")
ANSIBLE_CHECK=$([[ "$DRY_RUN" == "true" ]] && echo "--check" || echo "")

# ── Preflight ──────────────────────────────────────────────────────────────
# Run the dedicated preflight checker — validates tools, env vars, Vault,
# DNS, Proxmox SSH, cloud-init templates, and Ansible inventory sanity.
# Pass --fix to auto-build any missing cloud-init templates before deploying.
PREFLIGHT="${IAC_DIR}/scripts/preflight.sh"
if [[ -x "$PREFLIGHT" ]]; then
  info "Running pre-flight checks (scripts/preflight.sh)..."
  if ! bash "$PREFLIGHT" --start-layer "$START_LAYER" ${DRY_RUN:+--dry-run}; then
    error "Pre-flight checks failed. Fix issues above, then re-run bootstrap.sh.\n  To auto-build missing templates: bash scripts/preflight.sh --fix"
  fi
else
  # Fallback minimal checks if preflight.sh is somehow missing
  info "Checking prerequisites (minimal — preflight.sh not found)..."
  command -v terraform  >/dev/null || error "terraform not found"
  command -v ansible-playbook >/dev/null || error "ansible-playbook not found"
  [[ -n "${TF_VAR_role_id:-}" ]]   || error "TF_VAR_role_id not set"
  [[ -n "${TF_VAR_secret_id:-}" ]] || error "TF_VAR_secret_id not set"
  [[ -n "${VAULT_ADDR:-}" ]]       || error "VAULT_ADDR not set"
  if ! getent hosts vault.internal >/dev/null 2>&1; then
    error "vault.internal does not resolve. Add to /etc/hosts:\n  $(hostname -I | awk '{print $1}')  vault.internal  iac.internal"
  fi
fi
info "vault.internal resolves to: $(getent hosts vault.internal | awk '{print $1}')"

tf_apply() {
  local env="$1"
  info "Terraform: applying ${env}..."
  pushd "${TF_DIR}/${env}" >/dev/null
  terraform init -input=false -upgrade >/dev/null
  if [[ "$DRY_RUN" == "true" ]]; then
    terraform plan -input=false
  else
    terraform apply -input=false -auto-approve
  fi
  popd >/dev/null
}

ansible_run() {
  local tags="$1"
  local limit="${2:-all}"
  info "Ansible: tags=${tags} limit=${limit}"
  ansible-playbook -i "${INVENTORY}" \
    "${ANSIBLE}/playbooks/deploy_all.yml" \
    --tags "${tags}" \
    --limit "${limit}" \
    ${ANSIBLE_CHECK}
}

# ═══════════════════════════════════════════════════════════════════════════
# STEP 0 — Templates (must exist before any VM can be cloned)
# ═══════════════════════════════════════════════════════════════════════════
if [[ "$START_LAYER" -le 0 ]]; then
  info "═══ STEP 0: Build cloud-init templates (9001 + 9002) ═══"
  ansible-playbook -i "${INVENTORY}" \
    "${ANSIBLE}/playbooks/build_templates.yml" \
    ${ANSIBLE_CHECK}
fi

# ═══════════════════════════════════════════════════════════════════════════
# LAYER 1 — Network Foundation
# ═══════════════════════════════════════════════════════════════════════════
if [[ "$START_LAYER" -le 1 ]]; then
  echo ""
  warn "═══ LAYER 1: fw-01 (OPNSense) — MANUAL STEP REQUIRED ═══"
  warn "  1. In Proxmox UI: run terraform in envs/opnsense OR create VM manually"
  warn "  2. Mount OPNSense ISO and complete installation wizard"
  warn "  3. Configure WAN/LAN interfaces"
  warn "  4. Then run: ansible-playbook playbooks/deploy_network_stack.yml --tags fw-01"
  warn "  Press ENTER when fw-01 is up and accessible, or Ctrl+C to abort."
  read -r

  info "═══ LAYER 1: Deploying dns-01 (Pi-hole) ═══"
  tf_apply "dns-01"
  sleep 30   # wait for cloud-init boot
  ansible_run "layer1,dns-01" "dns-01"

  info "L1: Verifying Pi-hole DNS..."
  dig +short pve-01.$(hostname -d || echo "homelab.local") @10.10.30.10 \
    && info "Pi-hole DNS: OK" \
    || warn "Pi-hole DNS check failed — verify manually before proceeding"
fi

# ═══════════════════════════════════════════════════════════════════════════
# LAYER 2 — VPN
# ═══════════════════════════════════════════════════════════════════════════
if [[ "$START_LAYER" -le 2 ]]; then
  info "═══ LAYER 2: Deploying vpn-01 (WireGuard) ═══"
  # vpn-01 Terraform env already exists
  tf_apply "vpn"
  sleep 30
  ansible_run "layer2,vpn-01"
  info "L2: WireGuard admin UI should be at http://10.10.10.5:51821"
fi

# ═══════════════════════════════════════════════════════════════════════════
# LAYER 3 — Storage & Databases
# pg-01 and nas-01 can be started in parallel
# ═══════════════════════════════════════════════════════════════════════════
if [[ "$START_LAYER" -le 3 ]]; then
  info "═══ LAYER 3: Deploying pg-01 (PostgreSQL + Elasticsearch) ═══"
  tf_apply "pg-01"
  sleep 30
  ansible_run "layer3,pg-01" "pg-01"

  info "═══ LAYER 3: Configuring nas-01 NFS shares ═══"
  # nas-01 is bare-metal OMV — Terraform not needed; Ansible configures shares
  if [[ "${NAS_FS_UUID:-}" == "" ]]; then
    warn "omv_data_fs_uuid not set — running discovery..."
    warn "Run: ansible-playbook playbooks/deploy_nas.yml --tags discover --limit nas-01"
    warn "Then export NAS_FS_UUID=<uuid> and re-run this script with --start-layer 3"
    warn "Skipping nas-01 for now — set NAS_FS_UUID and re-run layer 3."
  else
    ansible-playbook -i "${INVENTORY}" \
      "${ANSIBLE}/playbooks/deploy_nas.yml" \
      --limit "nas-01" \
      ${ANSIBLE_CHECK}
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# LAYER 4 — Reverse Proxy, n8n, Monitoring
# After this layer completes, n8n is available on web-01.
# ═══════════════════════════════════════════════════════════════════════════
if [[ "$START_LAYER" -le 4 ]]; then
  info "═══ LAYER 4: Deploying web-01 (Traefik + n8n + Prometheus + Grafana + NetBox) ═══"
  tf_apply "web-01"
  sleep 45   # larger boot + Docker pull time
  ansible_run "layer4,web-01" "web-01"

  info "L4: Waiting for Traefik health endpoint..."
  for i in $(seq 1 20); do
    curl -sf http://10.10.30.20:8080/ping >/dev/null && break
    sleep 10
    [[ "$i" -eq 20 ]] && warn "Traefik not responding — check web-01 Docker logs"
  done
  info "Traefik: OK"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Bootstrap complete
# ═══════════════════════════════════════════════════════════════════════════
echo ""
info "══════════════════════════════════════════════════"
info "  BOOTSTRAP COMPLETE — Layers 1-4 deployed"
info "══════════════════════════════════════════════════"
echo ""
info "Services now available:"
info "  Pi-hole admin:    http://10.10.30.10/admin"
info "  WireGuard UI:     http://10.10.10.5:51821"
info "  Traefik dashboard:http://10.10.30.20:8080"
info "  n8n:              http://10.10.30.20:5678"
info "  Grafana:          http://10.10.30.20:3000"
echo ""
info "NEXT STEPS — Layers 5-8 via n8n:"
info "  1. Open n8n at http://10.10.30.20:5678"
info "  2. Import workflow: IaC/orchestration/full_stack_deployment.yaml"
info "  3. Trigger via: POST http://10.10.30.20:5678/webhook/deploy-layers-5-8"
info "     body: { \"layer_start\": 5, \"layer_end\": 8 }"
echo ""
warn "  Or continue manually:"
warn "    ansible-playbook -i ansible/inventory/services.yml \\"
warn "      ansible/playbooks/deploy_all.yml --tags layer5,layer6,layer7,layer8"
