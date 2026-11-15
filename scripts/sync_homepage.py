#!/usr/bin/env python3
"""
sync_homepage.py — Homelab Homepage Service Config Generator

Reads every service.yaml under IaC/services/** and every VM manifest under
IaC/config/vms/**, then generates a complete homepage services.yaml grouped
by UI category.

Run this after deploying any new service to keep Homepage current.
The Ansible homepage role calls this automatically on deploy.

Usage:
  # Standard run (uses default paths relative to IaC dir):
  python3 IaC/scripts/sync_homepage.py

  # Custom paths:
  python3 IaC/scripts/sync_homepage.py \
    --services-dir IaC/services \
    --vms-dir      IaC/config/vms \
    --output       /opt/homepage/config/services.yaml \
    --domain       yourdomain.com

  # Dry-run — print to stdout instead of writing file:
  python3 IaC/scripts/sync_homepage.py --dry-run

  # Restart homepage container after write:
  python3 IaC/scripts/sync_homepage.py --restart

Install deps:  pip install pyyaml rich
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
    from rich.console import Console
    from rich.table import Table
except ImportError:
    print("Missing deps: pip install pyyaml rich")
    sys.exit(1)

console = Console()

# ── Category ordering (left → right in Homepage layout) ──────────────────────
CATEGORY_ORDER = [
    "Infrastructure",
    "Network",
    "Platform",
    "Automation",
    "VCS & Dev",
    "Applications",
    "Productivity",
    "Health",
    "AI & GPU",
    "Media",
    "Other",
]

# ── Static / hardcoded entries that don't come from service files ─────────────
# Adjust IPs and URLs after seeding your domain.
STATIC_ENTRIES: list[dict[str, Any]] = [

    # ── Infrastructure ────────────────────────────────────────────────────────
    {
        "category": "Infrastructure",
        "name": "Proxmox pve-01",
        "icon": "proxmox.png",
        "href": "https://10.0.0.11:8006",
        "description": "Primary hypervisor — always on",
        "ping": "10.0.0.11",
        "siteMonitor": "https://10.0.0.11:8006",
    },
    {
        "category": "Infrastructure",
        "name": "Proxmox pve-02",
        "icon": "proxmox.png",
        "href": "https://10.0.0.12:8006",
        "description": "Burst GPU host — WoL managed",
        "ping": "10.0.0.12",
    },
    {
        "category": "Infrastructure",
        "name": "Proxmox pve-03",
        "icon": "proxmox.png",
        "href": "https://10.0.0.13:8006",
        "description": "Burst media host — WoL managed",
        "ping": "10.0.0.13",
    },
    {
        "category": "Infrastructure",
        "name": "NAS-01 (OMV)",
        "icon": "openmediavault.png",
        "href": "http://10.0.40.20",
        "description": "OpenMediaVault NAS — NFS shares",
        "ping": "10.0.40.20",
    },

    # ── Network ───────────────────────────────────────────────────────────────
    {
        "category": "Network",
        "name": "OPNSense",
        "icon": "opnsense.png",
        "href": "https://10.0.99.2",
        "description": "Firewall & router (fw-01)",
        "ping": "10.0.99.2",
        "siteMonitor": "https://10.0.99.2",
    },
    {
        "category": "Network",
        "name": "Pi-hole",
        "icon": "pi-hole.png",
        "href": "http://10.0.30.10/admin",
        "description": "DNS ad-blocking (dns-01)",
        "ping": "10.0.30.10",
        "widget": {"type": "pihole", "url": "http://10.0.30.10"},
    },
    {
        "category": "Network",
        "name": "WireGuard",
        "icon": "wireguard.png",
        "href": "http://10.0.10.5:51821",
        "description": "VPN admin UI (vpn-01 / wg-easy)",
        "ping": "10.0.10.5",
    },

    # ── Platform ──────────────────────────────────────────────────────────────
    {
        "category": "Platform",
        "name": "Authelia",
        "icon": "authelia.png",
        "href": "https://auth.homelab.local",
        "description": "SSO / OIDC gateway — 2FA portal",
        "ping": "10.0.30.20",
        "siteMonitor": "https://auth.homelab.local/api/health",
    },
    {
        "category": "Platform",
        "name": "Vault",
        "icon": "vault.png",
        "href": "http://vault:8200/ui",
        "description": "HashiCorp Vault — secret management",
    },
    {
        "category": "Platform",
        "name": "Traefik",
        "icon": "traefik.png",
        "href": "http://10.0.30.20:8080/dashboard/",
        "description": "Reverse proxy (web-01)",
        "ping": "10.0.30.20",
        "widget": {"type": "traefik", "url": "http://10.0.30.20:8080"},
    },
    {
        "category": "Platform",
        "name": "NetBox",
        "icon": "netbox.png",
        "href": "http://10.0.30.20:8001",
        "description": "DCIM / IPAM — infrastructure inventory",
        "ping": "10.0.30.20",
    },
    {
        "category": "Platform",
        "name": "Grafana",
        "icon": "grafana.png",
        "href": "http://10.0.30.20:3000",
        "description": "Metrics & alerting dashboards",
        "ping": "10.0.30.20",
    },

    # ── Automation ────────────────────────────────────────────────────────────
    {
        "category": "Automation",
        "name": "n8n",
        "icon": "n8n.png",
        "href": "http://10.0.30.20:5678",
        "description": "Workflow automation (web-01)",
        "ping": "10.0.30.20",
    },
    {
        "category": "Automation",
        "name": "IaC API",
        "icon": "mdi-api",
        "href": "http://10.0.200.10:8000/docs",
        "description": "Terraform & Ansible orchestration API",
        "ping": "10.0.200.10",
    },
]

# ── Icon mapping for common service names ─────────────────────────────────────
ICON_MAP = {
    "gitlab": "gitlab.png",   # kept for reference — old git-01, decommissioned 2026-07-29
    "forgejo": "forgejo.png",
    "immich": "immich.png",
    "jellyfin": "jellyfin.png",
    "outline": "outline.png",
    "jira": "jira.png",
    "confluence": "confluence.png",
    "diskover": "mdi-magnify",
    "mempalace": "mdi-brain",
    "open-webui": "openai.png",
    "ollama": "ollama.png",
    "yolo": "mdi-eye",
    "authelia": "authelia.png",
    "paperless": "paperless-ngx.png",
    "paperless-ngx": "paperless-ngx.png",
    "wger": "mdi-dumbbell",
    "prometheus": "prometheus.png",
    "elasticsearch": "elasticsearch.png",
    "netbox": "netbox.png",
    "pihole": "pi-hole.png",
    "wireguard": "wireguard.png",
}

# ── Homepage category mapping for service tier/category tags ─────────────────
TIER_TO_CATEGORY = {
    "network":      "Network",
    "platform":     "Platform",
    "infra":        "Infrastructure",
    "hypervisor":   "Infrastructure",
    "storage":      "Infrastructure",
    "application":  "Applications",
    "media":        "Media",
    "ai":           "AI & GPU",
    "gpu":          "AI & GPU",
    "vcs":          "VCS & Dev",
    "dev":          "VCS & Dev",
    "automation":   "Automation",
}


# ─────────────────────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict | None:
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception as e:
        console.print(f"[yellow]⚠ Could not read {path}: {e}[/yellow]")
        return None


def _collect_services(services_dir: Path, domain: str) -> list[dict]:
    """Walk IaC/services/** and extract homepage-relevant metadata."""
    entries: list[dict] = []

    for svc_file in sorted(services_dir.rglob("service.yaml")):
        data = _load_yaml(svc_file)
        if not data:
            continue

        name = data.get("name", svc_file.parent.name)
        tags = data.get("tags", {})
        stype = data.get("type", "")

        # Determine category
        raw_cat = tags.get("category") or TIER_TO_CATEGORY.get(
            (tags.get("tier") or stype or "").lower(), ""
        )
        category = raw_cat if raw_cat else "Other"

        # Build URL: prefer tag-based domain name
        dns_names = []
        for iface in data.get("interfaces", []) + data.get("services", []):
            dns = (
                iface.get("dns_name")
                or (tags.get("dns") == "true" and f"{name}.{domain}")
            )
            if dns:
                dns_names.append(dns)

        href = data.get("href") or tags.get("href")
        if not href:
            if dns_names:
                scheme = "https" if tags.get("ingress") in ("https", "tls") else "http"
                href = f"{scheme}://{dns_names[0]}"
            else:
                continue  # no URL discoverable — skip

        icon = tags.get("icon") or ICON_MAP.get(name.lower(), "mdi-server")
        if icon and "." not in icon and not icon.startswith("mdi-"):
            icon = f"{icon}.png"

        entry: dict[str, Any] = {
            "category": category,
            "name": name,
            "icon": icon,
            "href": href,
            "description": data.get("description", "")[:80],
        }

        # Ping target
        for vm in data.get("interfaces", []):
            if vm.get("protocol") in ("http", "https"):
                entry["ping"] = href
                break

        entries.append(entry)

    return entries


def _collect_vms(vms_dir: Path, domain: str) -> list[dict]:
    """Walk IaC/config/vms/ and add VM cards for services that have a web UI."""
    entries: list[dict] = []

    for vm_file in sorted(vms_dir.glob("*.yaml")):
        data = _load_yaml(vm_file)
        if not data:
            continue

        name = data.get("name", vm_file.stem)
        fqdn = data.get("fqdn", f"{name}.{domain}")
        tier = data.get("tier", "")

        category = TIER_TO_CATEGORY.get(tier.lower(), "Platform")

        mgmt_port = None
        for iface in data.get("interfaces", []):
            if iface.get("type") in ("web", "management", "api"):
                mgmt_port = iface.get("port")
                break

        if mgmt_port:
            href = f"https://{fqdn}:{mgmt_port}"
        else:
            # Only add VMs that have a useful web UI
            continue

        entries.append({
            "category": category,
            "name": name,
            "icon": ICON_MAP.get(name.lower(), "mdi-server"),
            "href": href,
            "description": data.get("description", "")[:80],
            "ping": fqdn,
        })

    return entries


def _build_services_yaml(all_entries: list[dict]) -> str:
    """Group entries by category and render as homepage services.yaml."""
    grouped: dict[str, list[dict]] = {c: [] for c in CATEGORY_ORDER}

    for entry in all_entries:
        cat = entry.get("category", "Other")
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(entry)

    lines = [
        "# Homepage services — generated by sync_homepage.py",
        "# Run: python3 IaC/scripts/sync_homepage.py to regenerate",
        "",
    ]

    for cat in CATEGORY_ORDER:
        items = grouped.get(cat, [])
        if not items:
            continue
        lines.append(f"- {cat}:")
        for e in items:
            lines.append(f"    - {e['name']}:")
            lines.append(f"        icon: {e['icon']}")
            lines.append(f"        href: \"{e['href']}\"")
            if e.get("description"):
                lines.append(f"        description: \"{e['description']}\"")
            if e.get("ping"):
                lines.append(f"        ping: \"{e['ping']}\"")
            if e.get("siteMonitor"):
                lines.append(f"        siteMonitor: \"{e['siteMonitor']}\"")
            if e.get("widget"):
                lines.append("        widget:")
                for k, v in e["widget"].items():
                    lines.append(f"          {k}: {v}")
        lines.append("")

    return "\n".join(lines)


def _print_summary(all_entries: list[dict]) -> None:
    table = Table(show_header=True, header_style="bold cyan",
                  title="Homepage Services Summary")
    table.add_column("Category")
    table.add_column("Name")
    table.add_column("URL")
    table.add_column("Widget?", justify="center")

    for e in all_entries:
        table.add_row(
            e.get("category", "—"),
            e["name"],
            e["href"],
            "✓" if e.get("widget") else "",
        )
    console.print(table)


def main() -> None:
    root = Path(__file__).parent.parent   # IaC/

    parser = argparse.ArgumentParser(description="Generate homepage services.yaml")
    parser.add_argument("--services-dir", default=str(root / "services"),
                        help="Path to IaC/services/")
    parser.add_argument("--vms-dir", default=str(root / "config" / "vms"),
                        help="Path to IaC/config/vms/")
    parser.add_argument("--output", default=str(root / "docker" / "stacks" / "homepage" / "services.yaml"),
                        help="Output path for services.yaml")
    parser.add_argument("--domain", default="homelab.local",
                        help="Base domain (e.g. yourdomain.com)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print to stdout instead of writing file")
    parser.add_argument("--restart", action="store_true",
                        help="Restart the homepage Docker container after writing")

    args = parser.parse_args()

    services_dir = Path(args.services_dir)
    vms_dir = Path(args.vms_dir)
    output_path = Path(args.output)

    console.print("[bold]sync_homepage.py — Generating services.yaml[/bold]")

    # Start with static entries
    all_entries: list[dict] = list(STATIC_ENTRIES)

    # Add service-yaml discovered entries
    if services_dir.is_dir():
        discovered = _collect_services(services_dir, args.domain)
        # Deduplicate by name (static entries take priority)
        static_names = {e["name"].lower() for e in STATIC_ENTRIES}
        new = [e for e in discovered if e["name"].lower() not in static_names]
        all_entries += new
        console.print(f"  Discovered {len(new)} services from {services_dir}")
    else:
        console.print(f"[yellow]  services_dir not found: {services_dir}[/yellow]")

    # Add VM manifest entries
    if vms_dir.is_dir():
        vms = _collect_vms(vms_dir, args.domain)
        static_names = {e["name"].lower() for e in all_entries}
        new_vms = [e for e in vms if e["name"].lower() not in static_names]
        all_entries += new_vms
        console.print(f"  Discovered {len(new_vms)} VMs from {vms_dir}")

    yaml_output = _build_services_yaml(all_entries)

    if args.dry_run:
        console.print("\n[dim]── services.yaml output ──[/dim]")
        console.print(yaml_output)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(yaml_output)
        console.print(f"[green]✓ Written:[/green] {output_path}")

    _print_summary(all_entries)

    if args.restart and not args.dry_run:
        console.print("[bold]Restarting homepage container...[/bold]")
        try:
            subprocess.run(
                ["docker", "restart", "homepage"],
                check=True, capture_output=True
            )
            console.print("[green]✓ Homepage restarted[/green]")
        except subprocess.CalledProcessError as e:
            console.print(f"[yellow]⚠ Could not restart homepage: {e}[/yellow]")


if __name__ == "__main__":
    main()
