#!/usr/bin/env bash
# =============================================================================
# preflight.sh — Bootstrap pre-flight checker + auto-fixer
#
# Validates every prerequisite that bootstrap.sh depends on, then reports
# status with clear pass/warn/fail indicators.  In --fix mode it auto-builds
# missing Proxmox cloud-init templates via the existing Ansible playbook.
#
# Usage:
#   bash scripts/preflight.sh              # check only, exit 1 if blockers found
#   bash scripts/preflight.sh --fix        # check + auto-build missing templates
#   bash scripts/preflight.sh --dry-run    # show what --fix would do, no changes
#   bash scripts/preflight.sh --start-layer 2   # only check prereqs for layer 2+
#
# Exit codes:
#   0  All checks pass (or only non-blocking warnings)
#   1  One or more blocking failures — do not run bootstrap.sh
#
# Called automatically by bootstrap.sh as the first step.
# =============================================================================

set -uo pipefail   # -e intentionally omitted: preflight must continue past failures

# ── Paths (resolved relative to this script's location) ───────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IAC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ANSIBLE_DIR="${IAC_DIR}/ansible"
INVENTORY="${ANSIBLE_DIR}/inventory/services.yml"

# Ensure Ansible finds the right config + roles regardless of cwd
export ANSIBLE_CONFIG="${ANSIBLE_DIR}/ansible.cfg"
export ANSIBLE_ROLES_PATH="${ANSIBLE_DIR}/roles"
HOST_VARS_PVE01="${ANSIBLE_DIR}/inventory/host_vars/pve-01.yml"
ANSIBLE_REQUIREMENTS="${ANSIBLE_DIR}/requirements.yml"
PY_REQUIREMENTS="${SCRIPT_DIR}/requirements.txt"
PIPX_ANSIBLE_BIN="${HOME}/.local/share/pipx/venvs/ansible/bin"
LOCAL_BIN="${HOME}/.local/bin"
MIN_ANSIBLE_CORE="2.17"   # minimum ansible-core version (pipx install)

# ── Colours ────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m';  BOLD='\033[1m';      NC='\033[0m'

pass()  { echo -e "  ${GREEN}✔${NC}  $*"; }
warn()  { echo -e "  ${YELLOW}⚠${NC}  $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail()  { echo -e "  ${RED}✘${NC}  $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
info()  { echo -e "  ${CYAN}ℹ${NC}  $*"; }
section() { echo -e "\n${BOLD}── $* ──${NC}"; }

WARN_COUNT=0
FAIL_COUNT=0
FIX_MODE=false
DRY_RUN=false
START_LAYER=1

while [[ $# -gt 0 ]]; do
  case $1 in
    --fix)          FIX_MODE=true;  shift ;;
    --dry-run)      DRY_RUN=true;   shift ;;
    --start-layer)  START_LAYER="$2"; shift 2 ;;
    --help|-h)
      grep '^#' "$0" | head -20 | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# ── Derived config ─────────────────────────────────────────────────────────
PVE_HOST="${PVE_HOST:-10.10.0.11}"     # pve-01 / pve-01 management IP
PVE_USER="${PVE_USER:-root}"
PVE_NODE="${PVE_NODE:-pve-01}"
SSH_KEY="${SSH_KEY:-${HOME}/.ssh/id_ansible}"
VAULT_ADDR="${VAULT_ADDR:-}"
MISSING_TEMPLATES=()

REQUIRED_TEMPLATES=(9000 9001 9002)
declare -A TEMPLATE_NAMES=(
  [9000]="debian-12-cloud"
  [9001]="ubuntu-2204-cloud"
  [9002]="ubuntu-2404-cloud"
)

# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║   Elementary Particles — Bootstrap Pre-flight Check          ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo -e "   IAC_DIR : ${IAC_DIR}"
echo -e "   Mode    : $( [[ "$FIX_MODE" == true ]] && echo "FIX" || [[ "$DRY_RUN" == true ]] && echo "DRY-RUN" || echo "CHECK" )"
echo -e "   Layers  : ${START_LAYER}+"

# ═══════════════════════════════════════════════════════════════════════════
# CHECK 1 — Ansible version + PATH
# Ensures the pipx ansible-core (>= MIN_ANSIBLE_CORE) is active, not the
# stale system apt package (2.10.x) which fails with six.moves errors.
# ═══════════════════════════════════════════════════════════════════════════
section "Ansible (minimum core ${MIN_ANSIBLE_CORE})"

_ansible_path="$(command -v ansible-playbook 2>/dev/null || true)"
_ansible_core_ver="$("${_ansible_path:-/usr/bin/ansible-playbook}" --version 2>/dev/null \
  | grep -oP 'core \K[0-9]+\.[0-9]+' || echo "0.0")"

_pipx_ok=false
[[ -x "${PIPX_ANSIBLE_BIN}/ansible" ]] && _pipx_ok=true

# Check whether the active ansible-playbook is the pipx one
if [[ "$_ansible_path" == "${LOCAL_BIN}/ansible-playbook" ]] || \
   [[ "$_ansible_path" == "${PIPX_ANSIBLE_BIN}/ansible-playbook" ]]; then
  _from_pipx=true
else
  _from_pipx=false
fi

# Version comparison (major.minor only)
_meets_min=false
_major="$(echo "$_ansible_core_ver" | cut -d. -f1)"
_minor="$(echo "$_ansible_core_ver" | cut -d. -f2)"
_min_major="$(echo "$MIN_ANSIBLE_CORE" | cut -d. -f1)"
_min_minor="$(echo "$MIN_ANSIBLE_CORE" | cut -d. -f2)"
if [[ "$_major" -gt "$_min_major" ]] || \
   { [[ "$_major" -eq "$_min_major" ]] && [[ "$_minor" -ge "$_min_minor" ]]; }; then
  _meets_min=true
fi

if [[ "$_meets_min" == true ]] && [[ "$_from_pipx" == true ]]; then
  pass "ansible-core ${_ansible_core_ver}  (pipx — ${_ansible_path})"
else
  if [[ "$_pipx_ok" == false ]]; then
    fail "pipx ansible not found at ${PIPX_ANSIBLE_BIN}"
    info "Fix: pipx install ansible"
  elif [[ "$_meets_min" == false ]]; then
    fail "ansible-core ${_ansible_core_ver} is below minimum ${MIN_ANSIBLE_CORE}"
    info "Fix: pipx upgrade ansible"
  else
    # pipx ansible exists but not in PATH — create symlinks
    fail "ansible-playbook resolves to system apt version (${_ansible_core_ver}) — PATH needs fixing"
    if [[ "$FIX_MODE" == true ]] || [[ "$DRY_RUN" == true ]]; then
      info "$([[ "$DRY_RUN" == true ]] && echo "DRY-RUN: ")Symlinking pipx ansible binaries into ${LOCAL_BIN}/"
      if [[ "$DRY_RUN" == false ]]; then
        for _cmd in ansible ansible-playbook ansible-galaxy ansible-vault ansible-doc ansible-inventory; do
          [[ -x "${PIPX_ANSIBLE_BIN}/${_cmd}" ]] && \
            ln -sf "${PIPX_ANSIBLE_BIN}/${_cmd}" "${LOCAL_BIN}/${_cmd}"
        done
        # Re-check after fix
        _new_path="$(command -v ansible-playbook 2>/dev/null || true)"
        _new_ver="$("${_new_path}" --version 2>/dev/null | grep -oP 'core \K[0-9]+\.[0-9]+' || echo "0.0")"
        pass "Fixed: ansible-core ${_new_ver} now active  (${_new_path})"
        FAIL_COUNT=$((FAIL_COUNT-1))
      fi
    else
      info "Fix: bash scripts/preflight.sh --fix  (will symlink pipx binaries)"
    fi
  fi
fi

# ── Other required CLI tools ──────────────────────────────────────────────
for tool in terraform python3 ssh; do
  if command -v "$tool" &>/dev/null; then
    ver="$(${tool} --version 2>/dev/null | head -1 | tr -d '\n' || true)"
    pass "${tool}  (${ver})"
  else
    fail "${tool} not found — install before running bootstrap"
  fi
done

# ═══════════════════════════════════════════════════════════════════════════
# CHECK 1b — Python package requirements (scripts/requirements.txt)
# ═══════════════════════════════════════════════════════════════════════════
section "Python Requirements  (${PY_REQUIREMENTS##*/})"

if [[ ! -f "$PY_REQUIREMENTS" ]]; then
  warn "requirements.txt not found at ${PY_REQUIREMENTS}"
else
  _pip_missing=()
  while IFS= read -r line; do
    # Skip comments and blank lines; strip version specifiers for the import check
    [[ "$line" =~ ^#|^$ ]] && continue
    pkg="$(echo "$line" | sed 's/[>=<!].*//' | tr -d ' ')"
    # Map install name → importable name where they differ
    case "$pkg" in
      pyyaml)           import_name="yaml" ;;
      psycopg2-binary)  import_name="psycopg2" ;;
      *)                import_name="$pkg" ;;
    esac
    if python3 -c "import ${import_name}" 2>/dev/null; then
      ver="$(pip3 show "$pkg" 2>/dev/null | grep '^Version:' | awk '{print $2}')"
      pass "${pkg}  ${ver:+(${ver})}"
    else
      fail "${pkg} not installed"
      _pip_missing+=("$pkg")
    fi
  done < <(grep -v '^#\|^$' "$PY_REQUIREMENTS")

  if [[ "${#_pip_missing[@]}" -gt 0 ]]; then
    if [[ "$FIX_MODE" == true ]]; then
      info "Installing missing Python packages: ${_pip_missing[*]}"
      pip3 install "${_pip_missing[@]}" && {
        pass "Installed: ${_pip_missing[*]}"
        FAIL_COUNT=$((FAIL_COUNT - ${#_pip_missing[@]}))
      }
    elif [[ "$DRY_RUN" == true ]]; then
      info "DRY-RUN: would run: pip3 install ${_pip_missing[*]}"
    else
      info "Fix: pip3 install ${_pip_missing[*]}"
      info "  or: bash scripts/preflight.sh --fix"
    fi
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# CHECK 1c — Ansible collection requirements (Ansible/requirements.yml)
# ═══════════════════════════════════════════════════════════════════════════
section "Ansible Collections  (${ANSIBLE_REQUIREMENTS##*/})"

if [[ ! -f "$ANSIBLE_REQUIREMENTS" ]]; then
  warn "Ansible requirements.yml not found at ${ANSIBLE_REQUIREMENTS}"
else
  _collections_missing=false
  while IFS= read -r line; do
    # Parse "  - name: community.hashi_vault" lines
    if [[ "$line" =~ ^[[:space:]]*-[[:space:]]*name:[[:space:]]*(.+)$ ]]; then
      col="${BASH_REMATCH[1]}"
      col_path="${col//.//}"
      # ansible-galaxy lists installed collections; check namespace/name dir
      if ansible-galaxy collection list "$col" 2>/dev/null | grep -q "$col"; then
        col_ver="$(ansible-galaxy collection list "$col" 2>/dev/null | grep "$col" | awk '{print $2}' | head -1)"
        pass "${col}  ${col_ver:+(${col_ver})}"
      else
        fail "${col} not installed"
        _collections_missing=true
      fi
    fi
  done < "$ANSIBLE_REQUIREMENTS"

  if [[ "$_collections_missing" == true ]]; then
    if [[ "$FIX_MODE" == true ]]; then
      info "Installing missing Ansible collections..."
      ansible-galaxy collection install -r "$ANSIBLE_REQUIREMENTS" && {
        pass "Collections installed"
        # Recount: clear collection failures (exact count hard to track, rerun will confirm)
      }
    elif [[ "$DRY_RUN" == true ]]; then
      info "DRY-RUN: would run: ansible-galaxy collection install -r ${ANSIBLE_REQUIREMENTS}"
    else
      info "Fix: ansible-galaxy collection install -r ${ANSIBLE_REQUIREMENTS}"
      info "  or: bash scripts/preflight.sh --fix"
    fi
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# CHECK 2 — Required environment variables
# ═══════════════════════════════════════════════════════════════════════════
section "Environment Variables"

check_var() {
  local var="$1" label="${2:-$1}"
  if [[ -n "${!var:-}" ]]; then
    pass "${label} is set"
  else
    fail "${label} is not set — source /opt/vault/.env before running"
  fi
}

check_var VAULT_ADDR
check_var TF_VAR_role_id    "TF_VAR_role_id"
check_var TF_VAR_secret_id  "TF_VAR_secret_id"

if [[ -n "${WGER_HOME:-}" ]]; then
  pass "WGER_HOME = ${WGER_HOME}"
else
  warn "WGER_HOME not set — vault_preflight.py will default to /home/n8n/wger"
  warn "  Fix: export WGER_HOME=${IAC_DIR%/IaC}"
fi

# ═══════════════════════════════════════════════════════════════════════════
# CHECK 3 — DNS / hostname resolution
# ═══════════════════════════════════════════════════════════════════════════
section "DNS Resolution"

if getent hosts vault.internal &>/dev/null; then
  resolved="$(getent hosts vault.internal | awk '{print $1}')"
  pass "vault.internal → ${resolved}"
else
  fail "vault.internal does not resolve"
  info "Fix: echo \"$(hostname -I | awk '{print $1}')  vault.internal  iac.internal\" >> /etc/hosts"
fi

if getent hosts iac.internal &>/dev/null; then
  pass "iac.internal → $(getent hosts iac.internal | awk '{print $1}')"
else
  warn "iac.internal does not resolve (needed post-bootstrap)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# CHECK 4 — Vault health
# ═══════════════════════════════════════════════════════════════════════════
section "Vault (${VAULT_ADDR:-http://10.10.0.15:8200})"

VAULT_URL="${VAULT_ADDR:-http://10.10.0.15:8200}"
vault_health="$(curl -sf "${VAULT_URL}/v1/sys/health" 2>/dev/null || echo '{}')"

initialized="$(echo "$vault_health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('initialized','?'))" 2>/dev/null || echo '?')"
sealed="$(echo "$vault_health"      | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('sealed','?'))"      2>/dev/null || echo '?')"

if [[ "$initialized" == "True" ]] || [[ "$initialized" == "true" ]]; then
  pass "Vault initialized"
else
  fail "Vault not initialized (or unreachable) — run vault operator init"
fi

if [[ "$sealed" == "False" ]] || [[ "$sealed" == "false" ]]; then
  pass "Vault unsealed"
else
  fail "Vault is SEALED — run vault operator unseal before bootstrap"
  info "Unseal key location: check your secure password manager"
fi

# ═══════════════════════════════════════════════════════════════════════════
# CHECK 5 — Proxmox SSH reachability
# ═══════════════════════════════════════════════════════════════════════════
section "Proxmox Host (${PVE_USER}@${PVE_HOST} / node: ${PVE_NODE})"

PVE_SSH_OK=false
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes"
if [[ -f "$SSH_KEY" ]]; then
  SSH_CMD="ssh ${SSH_OPTS} -i ${SSH_KEY} ${PVE_USER}@${PVE_HOST}"
else
  SSH_CMD="ssh ${SSH_OPTS} ${PVE_USER}@${PVE_HOST}"
  warn "SSH key ${SSH_KEY} not found — trying without key (may prompt for password)"
fi

if $SSH_CMD "echo ok" &>/dev/null 2>&1; then
  pass "SSH to ${PVE_HOST} as ${PVE_USER}"
  PVE_SSH_OK=true

  # Check node name matches
  actual_node="$($SSH_CMD "hostname" 2>/dev/null | tr -d '\n')"
  if [[ "$actual_node" == "$PVE_NODE" ]]; then
    pass "Proxmox node name matches: ${PVE_NODE}"
  else
    warn "Proxmox hostname is '${actual_node}' but PVE_NODE='${PVE_NODE}'"
    warn "  If Terraform fails, set: export PVE_NODE=${actual_node}"
  fi

  # Proxmox API reachable
  if $SSH_CMD "pvesh get /nodes/${PVE_NODE}/status --output-format json" &>/dev/null 2>&1; then
    pass "Proxmox API responding on node ${PVE_NODE}"
  else
    warn "Proxmox API check failed — node may be mis-named"
  fi
else
  fail "Cannot SSH to ${PVE_HOST} as ${PVE_USER}"
  info "Fix: ensure ~/.ssh/id_ansible pubkey is in root@${PVE_HOST}:~/.ssh/authorized_keys"
fi

# ═══════════════════════════════════════════════════════════════════════════
# CHECK 6 — Cloud-init templates (9000, 9001, 9002)
# ═══════════════════════════════════════════════════════════════════════════
section "Cloud-init Templates on ${PVE_NODE}"

if [[ "$PVE_SSH_OK" == true ]]; then
  # Get list of all VMs/templates from Proxmox
  qm_output="$($SSH_CMD "qm list 2>/dev/null" || true)"

  for tmpl_id in "${REQUIRED_TEMPLATES[@]}"; do
    tmpl_name="${TEMPLATE_NAMES[$tmpl_id]}"
    if echo "$qm_output" | awk '{print $1}' | grep -qx "${tmpl_id}"; then
      # Check it's actually a template (not just a VM with that ID)
      tmpl_check="$($SSH_CMD "qm config ${tmpl_id} 2>/dev/null | grep '^template:'" || true)"
      if [[ -n "$tmpl_check" ]]; then
        pass "Template ${tmpl_id} (${tmpl_name}) — present ✓"
      else
        warn "VM ${tmpl_id} exists but is NOT marked as a template"
        warn "  Fix: ssh ${PVE_USER}@${PVE_HOST} 'qm template ${tmpl_id}'"
        MISSING_TEMPLATES+=("$tmpl_id")
      fi
    else
      fail "Template ${tmpl_id} (${tmpl_name}) — MISSING"
      MISSING_TEMPLATES+=("$tmpl_id")
    fi
  done

  # Show which VMs/templates currently exist (summary)
  if [[ -n "$qm_output" ]]; then
    info "All VMs/templates on ${PVE_NODE}:"
    echo "$qm_output" | while IFS= read -r line; do
      echo "       ${line}"
    done
  fi
else
  warn "Skipping template check — Proxmox SSH unavailable"
fi

# ═══════════════════════════════════════════════════════════════════════════
# CHECK 7 — Ansible inventory sanity
# ═══════════════════════════════════════════════════════════════════════════
section "Ansible Inventory"

if [[ -f "$INVENTORY" ]]; then
  pass "Inventory found: ${INVENTORY}"

  # Check that proxmox_node is not 'pve-01' anywhere (should all be 'pve-01' or pve-02/pve-03)
  bad_node_count="$(grep -c 'proxmox_node: pve-01' "$INVENTORY" 2>/dev/null || true)"
  if [[ "$bad_node_count" -eq 0 ]]; then
    pass "No stale 'proxmox_node: pve-01' entries found"
  else
    fail "${bad_node_count} hosts still have 'proxmox_node: pve-01' — run: sed -i 's/proxmox_node: pve-01/proxmox_node: pve-01/g' ${INVENTORY}"
  fi

  # Check ansible can parse the inventory
  if ansible-inventory -i "$INVENTORY" --list &>/dev/null 2>&1; then
    host_count="$(ansible-inventory -i "$INVENTORY" --list 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
hosts=set()
def walk(g):
  for k,v in g.items():
    if k=='hosts': hosts.update(v.keys())
    elif isinstance(v,dict): walk(v)
walk(d.get('_meta',{}).get('hostvars',{}))
print(len(hosts))
" 2>/dev/null || echo "?")"
    pass "Ansible inventory parses cleanly (${host_count} hosts)"
  else
    fail "Ansible inventory has parse errors — run: ansible-inventory -i ${INVENTORY} --list"
  fi
else
  fail "Inventory not found: ${INVENTORY}"
fi

# ═══════════════════════════════════════════════════════════════════════════
# CHECK 8 — Terraform env directories
# ═══════════════════════════════════════════════════════════════════════════
section "Terraform Envs (Layer ${START_LAYER}+)"

declare -A LAYER_ENVS=(
  [1]="dns-01"
  [2]="vpn"
  [3]="pg-01"
  [4]="web-01"
)

for layer in "${!LAYER_ENVS[@]}"; do
  [[ "$layer" -lt "$START_LAYER" ]] && continue
  env="${LAYER_ENVS[$layer]}"
  env_dir="${IAC_DIR}/terraform/envs/${env}"
  if [[ -d "$env_dir" ]] && [[ -f "${env_dir}/main.tf" ]]; then
    # Check proxmox_node default in variables.tf
    bad_node="$(grep -c '"pve-01"' "${env_dir}/variables.tf" 2>/dev/null || true)"
    if [[ "$bad_node" -gt 0 ]]; then
      fail "Layer ${layer} (${env}): variables.tf still has default=\"pve-01\" — should be \"pve-01\""
    else
      pass "Layer ${layer} (${env}): Terraform env OK"
    fi
  else
    fail "Layer ${layer} (${env}): Terraform env dir missing: ${env_dir}"
  fi
done

# ═══════════════════════════════════════════════════════════════════════════
# AUTO-FIX — Build missing templates
# ═══════════════════════════════════════════════════════════════════════════
if [[ "${#MISSING_TEMPLATES[@]}" -gt 0 ]]; then
  echo ""
  echo -e "${BOLD}── Missing Templates: Auto-fix ──${NC}"

  # Run without build_template_ids filter — the playbook skips existing VMs via
  # qm status check, so it's safe to run all three templates every time.
  playbook_cmd="ansible-playbook -i ${INVENTORY} \
    ${ANSIBLE_DIR}/playbooks/build_templates.yml"

  if [[ "$DRY_RUN" == true ]]; then
    info "DRY-RUN: would build templates: ${MISSING_TEMPLATES[*]}"
    info "Command: ${playbook_cmd}"
  elif [[ "$FIX_MODE" == true ]]; then
    info "Building missing templates: ${MISSING_TEMPLATES[*]}"
    info "This downloads ~600MB per template and runs virt-customize — may take 10-20 min."
    echo ""
    eval "$playbook_cmd"
    if [[ $? -eq 0 ]]; then
      pass "Template build complete — re-run preflight to verify"
      FAIL_COUNT=$((FAIL_COUNT - ${#MISSING_TEMPLATES[@]}))
    else
      fail "Template build failed — check Ansible output above"
    fi
  else
    echo -e "  ${YELLOW}⚠${NC}  Missing templates: ${MISSING_TEMPLATES[*]}"
    info "Run with --fix to build them automatically:"
    info "  bash scripts/preflight.sh --fix"
    info "Or build manually:"
    info "  ${playbook_cmd}"
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
if [[ "$FAIL_COUNT" -eq 0 && "$WARN_COUNT" -eq 0 ]]; then
  echo -e "${BOLD}║  ${GREEN}✔ ALL CHECKS PASSED — bootstrap.sh is ready to run${NC}${BOLD}          ║${NC}"
elif [[ "$FAIL_COUNT" -eq 0 ]]; then
  echo -e "${BOLD}║  ${YELLOW}⚠ ${WARN_COUNT} WARNING(S) — bootstrap can run but review above${NC}${BOLD}       ║${NC}"
else
  echo -e "${BOLD}║  ${RED}✘ ${FAIL_COUNT} FAILURE(S), ${WARN_COUNT} WARNING(S) — fix issues before bootstrap${NC}${BOLD}  ║${NC}"
fi
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

if [[ "$FAIL_COUNT" -gt 0 ]]; then
  if [[ "${#MISSING_TEMPLATES[@]}" -gt 0 ]] && [[ "$FIX_MODE" == false ]]; then
    echo -e "  Quick fix for templates: ${CYAN}bash scripts/preflight.sh --fix${NC}"
    echo ""
  fi
  exit 1
fi

exit 0
