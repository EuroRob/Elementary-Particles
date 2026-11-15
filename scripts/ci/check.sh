#!/usr/bin/env bash
# Repository checks. Run by .forgejo/workflows/ci.yml on every pull request and on
# master, and by hand before opening a PR:
#
#     scripts/ci/check.sh            # everything
#     scripts/ci/check.sh yaml tests # a subset
#
# Needs on PATH: python3 (+ pyyaml, pytest and the test deps), ansible-playbook,
# terraform, gitleaks. On the runner these come from /opt/ci-tools (role
# forgejo_runner); on the IaC VM they are the system tools plus ~/.local/bin.
#
# Every check prints "ok" or "FAILED" and the script exits 1 if any failed, so a
# red pipeline always says which check and why. Known-broken files are listed
# below with a reason instead of being silently skipped.
set -uo pipefail

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
# Two layouts: the private repo (IaC under Infrastructure/) and the public edition
# (IaC at the root, directories lower-case). Same checks either way.
if [ -d Infrastructure/IaC ]; then
  IAC=Infrastructure/IaC; INT=Infrastructure/integration; TF=$IAC/Terraform; ANS=$IAC/Ansible
else
  IAC=.; INT=integration; TF=terraform; ANS=ansible
fi
BASE_REF=${BASE_REF:-origin/master}
ONLY=("$@")

fail=0
want() { [ ${#ONLY[@]} -eq 0 ] || printf '%s\n' "${ONLY[@]}" | grep -qx "$1"; }
step() { printf '\n== %s\n' "$*"; }
result() { if [ "$1" -eq 0 ]; then echo "   ok"; else echo "   FAILED"; fail=1; fi; }

# ---------------------------------------------------------------------------
# Files that are known not to pass, with the reason. Fix the file, then remove
# the line. A new failure anywhere else still fails the run.
# ---------------------------------------------------------------------------
PY_KNOWN_BROKEN=(
  "$IAC/core/orchestration_engine.py"          # typo "class DeploymentStep Generator" since the 2026-07-18 bulk import; not deployed
  "$INT/api_server.py"                         # garbled text at line ~897 since the 2026-07-18 bulk import; superseded by the per-service api servers
  "$INT/test_voice_integration.py"             # garbled text at line ~400, same import; needs the live voice stack anyway
)
SHELL_KNOWN_BROKEN=(
  "$INT/DEPLOYMENT_COMMANDS_QUICK_REFERENCE.sh"   # a copy-paste reference, not a script (should become .md)
  "$INT/audio-analysis/traefik-verify-setup.sh"   # unterminated backtick at line ~182 since the 2026-07-18 import
)
PLAYBOOK_KNOWN_BROKEN=(
  "$ANS/playbooks/deploy_single.yml"       # legacy: expects service.yml next to a compose file, pre-particle layout
  "$ANS/playbooks/install_docker.yml"      # legacy, replaced by Roles/docker via deploy.yml
  "$ANS/playbooks/install_node_exporter.yml"  # legacy, replaced by deploy_node_exporter.yml
  "$ANS/playbooks/site.yml"                # references role multi_mounts, which no longer exists
)
# Tests that run without any live service. Everything else under integration/
# needs Postgres, Nextcloud, a GPU box or a .env with real endpoints.
OFFLINE_TESTS=(
  "$INT/test_code_partner.py" "$INT/test_field_ingest.py" "$INT/test_practice.py"
  "$INT/test_studio_drift.py" "$INT/test_studio_partner.py" "$INT/test_mail_fetcher.py"
  "$INT/test_mail_desk.py" "$INT/test_scout_contract.py" "$INT/test_scout_versions.py"
  "$INT/test_scout_worker.py" "$INT/test_scout_watchlist.py" "$INT/test_scout_dossier.py"
  "$INT/test_voice_profiles.py"
  "$IAC/scripts/test_patch_notify.py" "$IAC/scripts/test_iot_watch_exporter.py"
)

in_list() { local x; for x in "${@:2}"; do [ "$x" = "$1" ] && return 0; done; return 1; }

# ---------------------------------------------------------------------------
if want yaml; then
  step "YAML parses (custom tags such as !vault/!secret accepted; Jinja templates skipped)"
  git ls-files "$IAC/*.yml" "$IAC/*.yaml" "$INT/*.yml" "$INT/*.yaml" | python3 -c '
import sys, yaml
class Tolerant(yaml.SafeLoader): pass
def any_tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode): return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode): return loader.construct_sequence(node)
    return loader.construct_mapping(node)
Tolerant.add_multi_constructor("!", any_tag)
Tolerant.add_multi_constructor("tag:", any_tag)
bad = 0
for f in sys.stdin.read().split():
    text = open(f, encoding="utf-8", errors="replace").read()
    if "{%" in text or "{{" in text:      # rendered by Jinja before it is YAML
        continue
    try:
        list(yaml.load_all(text, Loader=Tolerant))
    except yaml.YAMLError as e:
        bad += 1
        mark = getattr(e, "problem_mark", None)
        print(f"   {f}:{mark.line + 1 if mark else 0}: {str(e).splitlines()[0][:100]}")
sys.exit(1 if bad else 0)'
  result $?
fi

# ---------------------------------------------------------------------------
if want python; then
  step "Python compiles"
  rc=0
  while read -r f; do
    if ! python3 -m py_compile "$f" 2>/dev/null; then
      if in_list "$f" "${PY_KNOWN_BROKEN[@]}"; then echo "   known-broken: $f"; else echo "   $f"; rc=1; fi
    fi
  done < <(git ls-files "$IAC/*.py" "$INT/*.py" | grep -v __pycache__)
  find "$IAC" "$INT" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
  result $rc
fi

# ---------------------------------------------------------------------------
if want shell; then
  step "Shell scripts parse (bash -n)"
  rc=0
  while read -r f; do
    if ! bash -n "$f" 2>/dev/null; then
      if in_list "$f" "${SHELL_KNOWN_BROKEN[@]}"; then echo "   known-broken: $f"; else echo "   $f"; rc=1; fi
    fi
  done < <(git ls-files "$IAC/*.sh" "$INT/*.sh")
  result $rc
fi

# ---------------------------------------------------------------------------
if want terraform; then
  step "Terraform formatting (terraform fmt -check)"
  if command -v terraform >/dev/null; then
    terraform -chdir="$TF" fmt -check -recursive -diff | sed 's/^/   /'
    result "${PIPESTATUS[0]}"
  else
    echo "   terraform not on PATH"; result 1
  fi
fi

# ---------------------------------------------------------------------------
if want ansible; then
  step "Ansible playbooks pass --syntax-check"
  if command -v ansible-playbook >/dev/null; then
    rc=0
    export ANSIBLE_LOCALHOST_WARNING=false ANSIBLE_INVENTORY_UNPARSED_WARNING=false ANSIBLE_DEPRECATION_WARNINGS=false
    for p in "$ANS"/playbooks/*.yml; do
      if in_list "$p" "${PLAYBOOK_KNOWN_BROKEN[@]}"; then continue; fi
      if ! out=$(cd "$ANS" && ansible-playbook --syntax-check -i inventory/services.yml "playbooks/$(basename "$p")" 2>&1); then
        echo "   $p"; echo "$out" | grep -E 'ERROR|error' | head -3 | sed 's/^/      /'; rc=1
      fi
    done
    echo "   known-broken (skipped): ${#PLAYBOOK_KNOWN_BROKEN[@]} legacy playbooks, see PLAYBOOK_KNOWN_BROKEN"
    result $rc
  else
    echo "   ansible-playbook not on PATH"; result 1
  fi
fi

# ---------------------------------------------------------------------------
if want tests; then
  step "Tests that need no live service (pytest)"
  if python3 -c 'import pytest' 2>/dev/null; then
    # one pytest run per directory: the test modules import their siblings by bare name
    rc=0
    # Hermetic defaults for modules that read deployment paths at import time; on code-01
    # the real /srv/code-partner/repo exists and is not readable by the runner user.
    _ci_tmp=$(mktemp -d)
    export CODE_PARTNER_REPO="$_ci_tmp/repo" CODE_PARTNER_STATE="$_ci_tmp/state" CODE_PARTNER_AUDIT="$_ci_tmp/audit.jsonl"
    for d in "$INT" "$IAC/scripts"; do
      files=()
      for t in "${OFFLINE_TESTS[@]}"; do [ -f "$t" ] && [ "$(dirname "$t")" = "$d" ] && files+=("$(basename "$t")"); done
      [ ${#files[@]} -eq 0 ] && continue
      (cd "$d" && python3 -m pytest -q -p no:cacheprovider "${files[@]}") 2>&1 | grep -E '^(E  |ERROR|FAILED|[0-9]+ (passed|failed|error)|.*(passed|failed|errors?) in )' | tail -12 | sed "s|^|   $d: |"
      [ "${PIPESTATUS[0]}" -eq 0 ] || rc=1
    done
    find "$IAC" "$INT" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
    rm -rf "$_ci_tmp"
    result $rc
  else
    echo "   pytest not installed"; result 1
  fi
fi

# ---------------------------------------------------------------------------
if want secrets; then
  step "Secrets: nothing new in the commits under review (gitleaks git)"
  if command -v gitleaks >/dev/null; then
    if git rev-parse --verify -q "$BASE_REF" >/dev/null; then
      gitleaks git --no-banner --redact --exit-code 3 --config .gitleaks.toml --log-opts="--no-merges $BASE_REF..HEAD" . 2>&1 | grep -vE '^$' | tail -20 | sed 's/^/   /'
      rc=${PIPESTATUS[0]}; [ "$rc" -eq 3 ] && rc=1; result $rc
    else
      echo "   base $BASE_REF not available; skipped (full-tree scan below still runs)"; result 0
    fi
    step "Secrets: full tree of $IAC and $INT (gitleaks dir, allowlist in .gitleaks.toml)"
    rc=0
    for d in "$IAC" "$INT"; do     # one path per call: gitleaks dir takes a single source
      gitleaks dir "$d" --no-banner --redact --exit-code 3 --config .gitleaks.toml 2>&1 | grep -vE '^$' | tail -30 | sed "s|^|   $d: |"
      r=${PIPESTATUS[0]}; [ "$r" -eq 0 ] || rc=1
    done
    result $rc
  else
    echo "   gitleaks not on PATH"; result 1
  fi
fi

echo
if [ "$fail" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "SOME CHECKS FAILED"; fi
exit $fail
