"""Tests for patch_notify's pure parts. Run: python3 -m pytest IaC/scripts/test_patch_notify.py -q"""

import patch_notify as p

VMS = [
    "git-01: newest backup 10.7h old (max 26h), from the latest vzdump run: True, VM lock: '' -> PASS",
    "git-01: apt=6 upgradable pkgs, docker=0 image updates, will_patch=True",
    "✓ git-01 (VM 251) patched successfully. apt=6 packages, docker=0 images. Pre-patch snapshot pre-patch-1 deleted. NetBox: patch_status=completed.",
    "dns-01: apt=0 upgradable pkgs, docker=0 image updates, will_patch=False",
    "SKIP dl-01: unreachable (Task failed: ssh timed out). Stays pending for the next window.",
    "SKIP web-02: health check fails on the unpatched VM (port 8080: Timeout; url x: -1). Stays pending",
    "Health checks FAILED for gpu-01 after patching. Rolling back to snapshot pre-patch-2.",
    "es-01: newest backup 30h old (max 26h), from the latest vzdump run: False, VM lock: '' -> SKIP",
]
CTS = ["tandoor: all 1 container(s) already on the newest image for their tag",
       "homepage updated: ghcr.io/x/homepage:v1.2. Rollback images kept as :pc-rollback-20261017-0505."]


def test_summary_sorts_every_outcome():
    s = p.summarize(VMS, CTS)
    assert s["patched"] == ["git-01 (6 pkgs)"] and s["nothing"] == ["dns-01"]
    assert s["skipped"] == ["dl-01 (off/unreachable)", "web-02 (its health check fails before patching)"]
    assert s["rolled_back"] == ["gpu-01"] and s["gate"] == ["es-01"]
    assert s["ct_updated"] == ["homepage (ghcr.io/x/homepage:v1.2)"] and s["ct_rolled_back"] == []


def test_notice_names_problems_first_and_stays_quiet_on_quiet_mornings():
    text = p.notice(p.summarize(VMS, CTS), True, (0, 2, 0))
    assert text.startswith("⚠️ Patch window: 1 VM patched, 1 rolled back, 3 waiting")
    assert "ROLLED BACK to the pre-patch snapshot (writes since then are gone): gpu-01" in text
    assert "Weekly queue: every patchable VM set pending" in text and "Exit codes: VMs 2" in text
    quiet = p.summarize(["dns-01: apt=0 upgradable pkgs, docker=0 image updates, will_patch=False"], CTS[:1])
    assert p.notice(quiet, False, (0, 0, 0)) is None
    assert p.notice(quiet, True, (0, 0, 0)).startswith("✅ Patch window: 0 VMs patched")
