#!/usr/bin/env python3
"""
vault_preflight.py — Homelab Vault Secret Provisioner

Reads vault_seed.env, validates all required fields, writes every secret to
HashiCorp Vault KV v2, then verifies each write by reading back.

Usage:
  # Normal run — write all secrets:
  python3 IaC/vault_preflight.py

  # Use a different env file:
  python3 IaC/vault_preflight.py --env-file /path/to/my.env

  # Check-only — verify existing Vault state without writing anything:
  python3 IaC/vault_preflight.py --check-only

  # Write then securely delete the env file:
  python3 IaC/vault_preflight.py --shred

  # Dry-run — print what would be written, no Vault calls:
  python3 IaC/vault_preflight.py --dry-run

  # Limit to one section:
  python3 IaC/vault_preflight.py --section proxmox

  # Verify IaC API + NetBox + Homepage are reachable after bootstrap:
  python3 IaC/vault_preflight.py --check-api

  # Write secrets then automatically trigger sync_homepage + sync_netbox:
  python3 IaC/vault_preflight.py --wire

Required environment variables (NOT in vault_seed.env):
  VAULT_ADDR       — e.g. http://vault.internal:8200
  VAULT_ROLE_ID    — AppRole role ID
  VAULT_SECRET_ID  — AppRole secret ID

Install deps:  pip install hvac python-dotenv rich
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import hvac
    from dotenv import dotenv_values
    from rich.console import Console
    from rich.table import Table
    from rich import print as rprint
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: pip install hvac python-dotenv rich")
    sys.exit(1)

console = Console()

# ─────────────────────────────────────────────────────────────────────────────
# Secret Map: vault_path → { vault_field: (env_var, required, description) }
#
# Convention for env_var values:
#   "KEY"         — read string value from env
#   "KEY_FILE"    — if KEY is unset, check KEY_FILE and read the file content
#   "KEY|KEY_FILE" — check KEY first, fall back to KEY_FILE
# ─────────────────────────────────────────────────────────────────────────────

SECRET_MAP: dict[str, dict[str, tuple[str, bool, str]]] = {

    # ── Infrastructure ──────────────────────────────────────────────────────
    "homelab/ssh": {
        "iac_public_key":     ("SSH_IAC_PUBLIC_KEY",     True,  "IaC VM operator SSH public key"),
        "ansible_public_key": ("SSH_ANSIBLE_PUBLIC_KEY", True,  "Ansible control node SSH public key"),
        "ringo_key":          ("SSH_RINGO_KEY",          False, "Personal admin SSH public key (optional)"),
    },

    "proxmox/api/pve-01": {
        "api_url":      ("PROXMOX_PVE01_API_URL",      True,  "PVE-01 API URL (https://10.10.0.11:8006)"),
        "node":         ("PROXMOX_PVE01_NODE",         True,  "PVE-01 node name"),
        "token_id":     ("PROXMOX_PVE01_TOKEN_ID",     True,  "Terraform API token ID (user@pam!name)"),
        "token_secret": ("PROXMOX_PVE01_TOKEN_SECRET", True,  "Terraform API token secret UUID"),
    },

    "proxmox/api/pve-02": {
        "api_url":      ("PROXMOX_PVE02_API_URL",      False, "PVE-02 API URL (burst host)"),
        "node":         ("PROXMOX_PVE02_NODE",         False, "PVE-02 node name"),
        "token_id":     ("PROXMOX_PVE02_TOKEN_ID",     False, "PVE-02 Terraform token ID"),
        "token_secret": ("PROXMOX_PVE02_TOKEN_SECRET", False, "PVE-02 Terraform token secret"),
    },

    "proxmox/api/pve-03": {
        "api_url":      ("PROXMOX_PVE03_API_URL",      False, "PVE-03 API URL (burst host)"),
        "node":         ("PROXMOX_PVE03_NODE",         False, "PVE-03 node name (pve-03)"),
        "token_id":     ("PROXMOX_PVE03_TOKEN_ID",     False, "PVE-03 Terraform token ID"),
        "token_secret": ("PROXMOX_PVE03_TOKEN_SECRET", False, "PVE-03 Terraform token secret"),
    },

    "homelab/proxmox/pve-02": {
        "wol_mac":  ("PVE02_WOL_MAC",  True,  "Wake-on-LAN MAC for pve-02"),
        "wol_nic":  ("PVE02_WOL_NIC",  True,  "NIC name for WoL (e.g. eno1)"),
        "mgmt_ip":  ("PVE02_MGMT_IP",  True,  "Management IP of pve-02"),
        "hostname": ("PVE02_HOSTNAME", True,  "pve-02 hostname"),
    },

    "homelab/proxmox/pve-03": {
        "wol_mac":  ("PVE03_WOL_MAC",  True,  "Wake-on-LAN MAC for pve-03"),
        "wol_nic":  ("PVE03_WOL_NIC",  True,  "NIC name for WoL"),
        "mgmt_ip":  ("PVE03_MGMT_IP",  True,  "Management IP of pve-03"),
        "hostname": ("PVE03_HOSTNAME", True,  "pve-03 hostname"),
    },

    # ── Network ─────────────────────────────────────────────────────────────
    "homelab/opnsense": {
        "api_key":       ("OPNSENSE_API_KEY",       True,  "OPNSense API key"),
        "api_secret":    ("OPNSENSE_API_SECRET",    True,  "OPNSense API secret"),
        "root_password": ("OPNSENSE_ROOT_PASSWORD", True,  "OPNSense root console password"),
    },

    "vpn/wireguard": {
        "private_key": ("WG_SERVER_PRIVATE_KEY", True, "WireGuard server private key (base64)"),
    },

    "vpn/wg-easy": {
        "password_hash": ("WG_EASY_PASSWORD_HASH", True, "bcrypt hash of wg-easy admin password"),
        "jwt_secret":    ("WG_EASY_JWT_SECRET",    True, "wg-easy JWT signing secret (hex)"),
    },

    # ── Traefik ─────────────────────────────────────────────────────────────
    "traefik/certs": {
        "wildcard_crt": ("TRAEFIK_WILDCARD_CRT|TRAEFIK_WILDCARD_CRT_FILE", True,
                         "Wildcard TLS certificate (fullchain PEM)"),
        "wildcard_key": ("TRAEFIK_WILDCARD_KEY|TRAEFIK_WILDCARD_KEY_FILE", True,
                         "Wildcard TLS private key (PEM)"),
    },

    "traefik/acme": {
        "ionos_api_key": ("TRAEFIK_ACME_IONOS_API_KEY", True,
                          "IONOS DNS-01 API key (<public>.<secret>)"),
    },

    "traefik/dashboard": {
        "username": ("TRAEFIK_DASHBOARD_USER",     True,  "Traefik dashboard username"),
        "password": ("TRAEFIK_DASHBOARD_PASSWORD", True,  "Traefik dashboard password"),
    },

    # ── PostgreSQL ───────────────────────────────────────────────────────────
    "postgres/pg-01": {
        "postgres_password":        ("PG_POSTGRES_PASSWORD",   True,  "postgres superuser password"),
        "replication_user_password": ("PG_REPLICATION_PASSWORD", True,  "Replication user password"),
        "backup_s3_key":            ("PG_BACKUP_S3_KEY",       False, "pgbackrest S3/MinIO access key"),
        "backup_s3_secret":         ("PG_BACKUP_S3_SECRET",    False, "pgbackrest S3/MinIO secret key"),
        "app_user":                 ("PG_APP_USER",            False, "App-level DB username"),
        "app_password":             ("PG_APP_PASSWORD",        False, "App-level DB password"),
    },

    # ── IaC API ──────────────────────────────────────────────────────────────
    "iac/api_token": {
        "token":       ("IAC_API_TOKEN",       True,  "IaC API server bearer token"),
        "endpoint":    ("IAC_API_ENDPOINT",    True,  "IaC API URL (http://....:8000)"),
        "endpoint_ip": ("IAC_API_ENDPOINT_IP", True,  "IaC VM management IP"),
    },

    # ── n8n ──────────────────────────────────────────────────────────────────
    "docker/web-01": {
        "n8n_admin_email":         ("N8N_ADMIN_EMAIL",         False, "n8n admin UI login email — needed for /rest/login API auth, not just the UI"),
        "n8n_admin_password":      ("N8N_ADMIN_PASSWORD",      True,  "n8n admin UI password"),
        "n8n_jwt_secret":          ("N8N_JWT_SECRET",          True,  "n8n JWT signing secret"),
        "n8n_db_password":         ("N8N_DB_PASSWORD",         True,  "n8n PostgreSQL password"),
        "docker_registry_password": ("DOCKER_REGISTRY_PASSWORD", False, "Docker registry pull token"),
    },

    "n8n/webhook-tokens/jellyfin": {
        "token": ("N8N_JELLYFIN_WEBHOOK_TOKEN", True, "Jellyfin workflow webhook auth token"),
    },

    # ── Claude API (cloud reasoning fallback) ────────────────────────────────
    "claude/api": {
        "api_key": ("ANTHROPIC_API_KEY", True,
                    "Anthropic API key (https://console.anthropic.com/keys)"),
        "model":   ("CLAUDE_ROUTER_MODEL", False,
                    "Default model for ai_query_router (default: claude-sonnet-4-5)"),
    },

    # ── GitLab ───────────────────────────────────────────────────────────────
    "gitlab/root": {
        "password": ("GITLAB_ROOT_PASSWORD", True, "GitLab initial root password"),
    },

    "gitlab/smtp": {
        "password": ("GITLAB_SMTP_PASSWORD", False, "SMTP relay password for GitLab"),
    },

    "gitlab/oidc": {
        "secret": ("GITLAB_OIDC_SECRET", False, "Authelia OIDC client secret for GitLab"),
    },

    "gitlab/runner": {
        "registration_token": ("GITLAB_RUNNER_TOKEN", False,
                                "GitLab Runner registration token (fill after GitLab boots)"),
    },

    # ── Outline ──────────────────────────────────────────────────────────────
    "outline/app": {
        "secret_key":    ("OUTLINE_SECRET_KEY",    True,  "Outline app secret key (32-byte hex)"),
        "utils_secret":  ("OUTLINE_UTILS_SECRET",  True,  "Outline utils secret (32-byte hex)"),
    },

    "outline/db": {
        "db_password": ("OUTLINE_DB_PASSWORD", True, "Outline PostgreSQL user password"),
    },

    "outline/oidc": {
        "client_id":     ("OUTLINE_OIDC_CLIENT_ID",     True,  "Authelia OIDC client_id for Outline"),
        "client_secret": ("OUTLINE_OIDC_CLIENT_SECRET", True,  "OIDC client secret for Outline"),
    },

    "outline/smtp": {
        "smtp_host":      ("OUTLINE_SMTP_HOST",      False, "SMTP host (optional)"),
        "smtp_port":      ("OUTLINE_SMTP_PORT",      False, "SMTP port (optional)"),
        "smtp_username":  ("OUTLINE_SMTP_USERNAME",  False, "SMTP username (optional)"),
        "smtp_password":  ("OUTLINE_SMTP_PASSWORD",  False, "SMTP password (optional)"),
        "smtp_from_email": ("OUTLINE_SMTP_FROM",     False, "SMTP from address (optional)"),
    },

    # ── Atlassian ─────────────────────────────────────────────────────────────
    "atlassian/jira/db": {
        "password": ("JIRA_DB_PASSWORD", True, "Jira PostgreSQL user password"),
    },

    "atlassian/confluence/db": {
        "password": ("CONFLUENCE_DB_PASSWORD", True, "Confluence PostgreSQL user password"),
    },

    # ── NetBox ────────────────────────────────────────────────────────────────
    "netbox/api": {
        "url":                ("NETBOX_URL",                True,  "NetBox API URL (http://10.10.30.20:8001)"),
        "token":              ("NETBOX_API_TOKEN",          True,  "NetBox API token (created during NetBox setup)"),
        "superuser_password": ("NETBOX_SUPERUSER_PASSWORD", True,  "NetBox admin UI password"),
        "superuser_email":    ("NETBOX_SUPERUSER_EMAIL",    False, "NetBox admin email"),
    },

    # ── Homepage widget secrets ───────────────────────────────────────────────
    # Values here are used by the Ansible homepage role for live stats widgets.
    # All optional — missing = widget disabled, card still shown.
    "homepage/widgets": {
        "pihole_api_key":   ("HOMEPAGE_PIHOLE_API_KEY",   False, "Pi-hole API key for stats widget"),
        "grafana_password": ("HOMEPAGE_GRAFANA_PASSWORD", False, "Grafana admin password for widget"),
        "jellyfin_api_key": ("HOMEPAGE_JELLYFIN_API_KEY", False, "Jellyfin API key for media widget"),
    },

    # ── Authelia SSO gateway ──────────────────────────────────────────────────
    "authelia": {
        "jwt_secret":              ("AUTHELIA_JWT_SECRET",              True,
                                    "HMAC secret for Authelia JWTs (min 20 chars, ideally 64 hex)"),
        "session_secret":          ("AUTHELIA_SESSION_SECRET",          True,
                                    "Session cookie signing key (min 20 chars)"),
        "storage_encryption_key":  ("AUTHELIA_STORAGE_ENCRYPTION_KEY",  True,
                                    "AES-256 encryption key for Vault storage (min 20 chars)"),
        "pg_password":             ("AUTHELIA_PG_PASSWORD",             True,
                                    "Authelia PostgreSQL user password"),
    },

    "authelia/oidc": {
        # RSA-4096 private key (PKCS8 PEM) used as the OIDC issuer signing key.
        # Generate: openssl genrsa -out oidc.key 4096 && openssl pkcs8 -topk8 -nocrypt -in oidc.key -out oidc_private.key
        "private_key":          ("AUTHELIA_OIDC_PRIVATE_KEY|AUTHELIA_OIDC_PRIVATE_KEY_FILE", True,
                                  "RSA-4096 PKCS8 PEM private key for OIDC token signing"),
        # Per-client secrets — plaintext, set identically in each application's OIDC config.
        # Authelia stores a bcrypt hash, but plaintext is supported for initial setup.
        "gitlab_secret":        ("AUTHELIA_OIDC_GITLAB_SECRET",       True,  "GitLab OIDC client secret"),
        "outline_secret":       ("AUTHELIA_OIDC_OUTLINE_SECRET",      True,  "Outline OIDC client secret"),
        "netbox_secret":        ("AUTHELIA_OIDC_NETBOX_SECRET",       True,  "NetBox OIDC client secret"),
        "n8n_secret":           ("AUTHELIA_OIDC_N8N_SECRET",          True,  "n8n OIDC client secret"),
        "grafana_secret":       ("AUTHELIA_OIDC_GRAFANA_SECRET",      True,  "Grafana OIDC client secret"),
        "immich_secret":        ("AUTHELIA_OIDC_IMMICH_SECRET",       True,  "Immich OIDC client secret"),
        "jellyfin_secret":      ("AUTHELIA_OIDC_JELLYFIN_SECRET",     False, "Jellyfin OIDC client secret"),
        "jira_secret":          ("AUTHELIA_OIDC_JIRA_SECRET",         False, "Jira OIDC client secret"),
        "confluence_secret":    ("AUTHELIA_OIDC_CONFLUENCE_SECRET",   False, "Confluence OIDC client secret"),
        "paperless_secret":     ("AUTHELIA_OIDC_PAPERLESS_SECRET",    True,  "Paperless-ngx OIDC client secret"),
        "wger_secret":          ("AUTHELIA_OIDC_WGER_SECRET",         True,  "wger OIDC client secret"),
    },

    "authelia/users": {
        # Initial admin user seeded into users_database.yml.
        # Password hash: docker run --rm authelia/authelia:latest authelia crypto hash generate argon2 --password 'YourPassword'
        "admin_username":       ("AUTHELIA_ADMIN_USERNAME",            True,  "Admin login username"),
        "admin_display_name":   ("AUTHELIA_ADMIN_DISPLAY_NAME",        False, "Admin display name"),
        "admin_email":          ("AUTHELIA_ADMIN_EMAIL",               True,  "Admin email address"),
        "admin_password_hash":  ("AUTHELIA_ADMIN_PASSWORD_HASH",       True,
                                  "argon2id hash of admin password (see comment above)"),
    },

    "authelia/smtp": {
        "host":     ("AUTHELIA_SMTP_HOST",     False, "SMTP relay hostname"),
        "port":     ("AUTHELIA_SMTP_PORT",     False, "SMTP port (default 587)"),
        "username": ("AUTHELIA_SMTP_USERNAME", False, "SMTP auth username"),
        "password": ("AUTHELIA_SMTP_PASSWORD", False, "SMTP auth password"),
        "sender":   ("AUTHELIA_SMTP_SENDER",   False, "From address for Authelia notifications"),
    },

    # ── Paperless-ngx ─────────────────────────────────────────────────────────
    "paperless/app": {
        "secret_key": ("PAPERLESS_SECRET_KEY", True,
                       "Django secret key for Paperless-ngx (50-char random)"),
    },

    "paperless/db": {
        "db_user":     ("PAPERLESS_DB_USER",     True,  "PostgreSQL user for paperless"),
        "db_password": ("PAPERLESS_DB_PASSWORD", True,  "PostgreSQL password for paperless user"),
    },

    "paperless/oidc": {
        "client_id":     ("PAPERLESS_OIDC_CLIENT_ID",     True,
                          "Authelia OIDC client_id for Paperless-ngx (matches authelia config)"),
        "client_secret": ("PAPERLESS_OIDC_CLIENT_SECRET", True,
                          "OIDC client secret — same value as AUTHELIA_OIDC_PAPERLESS_SECRET"),
    },

    # ── wger Workout Manager ──────────────────────────────────────────────────
    "wger/django": {
        "secret_key":   ("WGER_DJANGO_SECRET_KEY",   True,
                         "Django SECRET_KEY (50-char random, python3 -c \"import secrets; print(secrets.token_urlsafe(50))\")"),
        "signing_key":  ("WGER_DJANGO_SIGNING_KEY",  True,
                         "JWT signing key — different from secret_key, same generation method"),
    },

    "wger/db": {
        "user":     ("WGER_DB_USER",     True,  "PostgreSQL user for wger database"),
        "password": ("WGER_DB_PASSWORD", True,  "PostgreSQL password for wger user"),
    },

    "wger/celery": {
        "flower_password": ("WGER_CELERY_FLOWER_PASSWORD", True,
                            "Password for Celery Flower monitoring UI"),
    },

    "wger/email": {
        "host_password": ("WGER_EMAIL_HOST_PASSWORD", False,
                          "SMTP relay password for wger outbound email"),
    },

    "wger/recaptcha": {
        "public_key":  ("WGER_RECAPTCHA_PUBLIC_KEY",  False, "Google reCAPTCHA v3 public key (optional)"),
        "private_key": ("WGER_RECAPTCHA_PRIVATE_KEY", False, "Google reCAPTCHA v3 private key (optional)"),
    },

    # Per-profile wger REST API tokens — NOT auto-generated by anything.
    # Log into wger.home.example.org as that profile's user -> Settings -> API ->
    # generate a new token, paste it here. Consumed by the assistant_stack
    # role, which writes it into profile-config's container env as
    # WGER_<PROFILE>_TOKEN; profile_config.py's build_context() resolves it
    # back out per-request as the "wger_token" field. Add one field here per
    # profile that gets wger_token_env set in its integration/profiles/*.yaml.
    "wger/tokens": {
        "papa_token": ("WGER_PAPA_TOKEN", False,
                       "wger REST API token for the 'papa' profile (wger.home.example.org -> Settings -> API)"),
        "mama_token": ("WGER_MAMA_TOKEN", False,
                       "wger REST API token for the 'mama' profile"),
    },

    # mama's wger login credentials — her Django user account was created
    # 2026-08-19 via manage.py shell (no self-signup flow was used) since no
    # wger account existed for her yet. She should log in and change this
    # password from wger's own account settings whenever she wants to.
    "wger/accounts": {
        "mama_username": ("WGER_MAMA_USERNAME", False, "wger login username for mama (currently 'mama')"),
        "mama_password": ("WGER_MAMA_PASSWORD", False, "wger login password for mama — generated, not user-chosen; she should rotate it"),
    },

    # ── apple-health-bridge (Apple Health -> wger/health-fusion sync) ──────────
    # See IaC/docs/APPLE_HEALTH_WGER_SYNC_ARCHITECTURE.md for the full design.
    "apple-health-bridge/webhook": {
        "secret": ("APPLE_HEALTH_BRIDGE_SECRET", True,
                   "Shared bearer token Health Auto Export's automations authenticate with — "
                   "openssl rand -hex 32. Same value goes into both automations' custom "
                   "'Authorization: Bearer <value>' header in the Health Auto Export app."),
    },

    # ── MinIO (S3-compatible object storage on nas-01) ─────────────────────────
    # MinIO runs as a Docker container on nas-01 (OMV NAS, 10.10.40.20).
    # Serves as the Restic backup backend and pgBackRest WAL archive destination.
    #
    # Bootstrap:
    #   vault kv put secret/minio/root \
    #     username=minioadmin \
    #     password=$(openssl rand -hex 32)
    "minio/root": {
        "username": ("MINIO_ROOT_USER",     True, "MinIO root/admin username (never use in client apps)"),
        "password": ("MINIO_ROOT_PASSWORD", True, "MinIO root password — openssl rand -hex 32"),
    },

    # Service account: used by Restic (all VMs → s3:http://nas-01.internal:9000/restic/)
    #   vault kv put secret/minio/service-accounts/restic \
    #     access_key=$(openssl rand -hex 16) \
    #     secret_key=$(openssl rand -hex 32)
    "minio/service-accounts/restic": {
        "access_key": ("MINIO_RESTIC_ACCESS_KEY", True, "Restic MinIO service account access key"),
        "secret_key": ("MINIO_RESTIC_SECRET_KEY", True, "Restic MinIO service account secret key"),
    },

    # Service account: used by pgBackRest on pg-01 for WAL archiving
    #   vault kv put secret/minio/service-accounts/pgbackrest \
    #     access_key=$(openssl rand -hex 16) \
    #     secret_key=$(openssl rand -hex 32)
    "minio/service-accounts/pgbackrest": {
        "access_key": ("MINIO_PGBACKREST_ACCESS_KEY", True, "pgBackRest MinIO service account access key"),
        "secret_key": ("MINIO_PGBACKREST_SECRET_KEY", True, "pgBackRest MinIO service account secret key"),
    },

    # ── Restic backup client (all VMs) ─────────────────────────────────────────
    # The Restic Ansible role (ansible/Roles/restic/) reads from:
    #   secret/restic/primary  — MinIO S3 service account + restic repo password
    #   secret/restic/offsite  — Optional: IONOS/Backblaze S3 for offsite copy
    #
    # repo password: one strong password for ALL restic repos across all VMs
    # (same password makes cross-VM restores simpler; per-VM is more secure)
    #
    # Bootstrap:
    #   vault kv put secret/restic/primary \
    #     access_key=<MINIO_RESTIC_ACCESS_KEY from above> \
    #     secret_key=<MINIO_RESTIC_SECRET_KEY from above> \
    #     password=$(openssl rand -base64 32) \
    #     pg_password=$(vault kv get -field=password secret/postgres/pg-01)
    "restic/primary": {
        "access_key": ("RESTIC_PRIMARY_ACCESS_KEY", True,
                        "MinIO Restic service account access key (same as minio/service-accounts/restic)"),
        "secret_key": ("RESTIC_PRIMARY_SECRET_KEY", True,
                        "MinIO Restic service account secret key"),
        "password":   ("RESTIC_PRIMARY_PASSWORD",   True,
                        "Restic repository encryption password — openssl rand -base64 32. STORE SAFELY."),
        "pg_password": ("RESTIC_PG_PASSWORD",       False,
                         "PostgreSQL superuser password for pg_dump during Restic postgres-type jobs"),
    },

    # Optional: offsite Restic repository (IONOS S3, Backblaze B2, Wasabi, etc.)
    # Set restic_offsite_enabled: true in host_vars to activate offsite copy.
    "restic/offsite": {
        "access_key": ("RESTIC_OFFSITE_ACCESS_KEY", False, "Offsite S3 provider access key"),
        "secret_key": ("RESTIC_OFFSITE_SECRET_KEY", False, "Offsite S3 provider secret key"),
    },

    # ── Immich — photo/video management ───────────────────────────────────────
    # Bootstrap:
    #   vault kv put secret/immich/app \
    #     jwt_secret=$(openssl rand -hex 32)
    #
    #   vault kv put secret/immich/db \
    #     user=immich \
    #     password=$(openssl rand -hex 24)
    "immich/app": {
        "jwt_secret": ("IMMICH_JWT_SECRET", True,
                       "JWT signing secret — openssl rand -hex 32"),
    },

    "immich/db": {
        "user":     ("IMMICH_DB_USER",     True,  "PostgreSQL user for Immich (default: immich)"),
        "password": ("IMMICH_DB_PASSWORD", True,  "PostgreSQL password — openssl rand -hex 24"),
    },

    "immich/redis": {
        "password": ("IMMICH_REDIS_PASSWORD", False,
                     "Redis password (optional — Redis is not exposed externally)"),
    },

    # Operator API key, generated from Immich's own UI (Account Settings ->
    # API Keys) — NOT deploy-time, used for scripted library/admin calls
    # (e.g. setting External Library import paths, since this Immich
    # version's own UI path-entry field silently rejects the value —
    # found 2026-08-15; see project_immich_deployment memory). Seeded
    # 2026-08-15 from the user's own /opt/vault/.env IMMICH_API entry.
    "immich/api": {
        "api_key": ("IMMICH_API", False,
                    "Operator API key from Immich Account Settings -> API Keys"),
    },

    # ── NordLynx (NordVPN WireGuard client on OPNSense) ──────────────────────
    # The private key and client address are the ONLY secrets.
    # The NordVPN server public key and endpoint IP are non-secret and go in
    # defaults/main.yml as opnsense_nordlynx_server_pubkey / _server_endpoint.
    #
    # How to obtain your NordVPN WireGuard private key:
    #   Method A — NordVPN CLI (easiest):
    #     sudo apt install nordvpn
    #     nordvpn login
    #     nordvpn set technology nordlynx
    #     nordvpn connect germany   # connects once to generate keypair
    #     sudo cat /var/lib/nordvpn/data/configs/user/private.key
    #     nordvpn disconnect
    #
    #   Method B — NordVPN Access Token (no client install):
    #     1. https://my.nordaccount.com → Services → NordVPN → Set up NordVPN manually
    #     2. Generate access token
    #     3. curl -s -u "token:<ACCESS_TOKEN>" https://api.nordvpn.com/v1/users/services/credentials
    #        → returns "nordlynx_private_key" and "username" (the base64 client pubkey)
    #
    # Find best Germany WireGuard server + its public key:
    #   curl -s "https://api.nordvpn.com/v1/servers/recommendations?filters[country_id]=81&filters[servers_technologies][identifier]=wireguard_udp&limit=1" \
    #     | python3 -m json.tool | grep -E '"station"|"public_key"'
    #   → "station" = server IP, "public_key" = server pubkey for defaults/main.yml
    #
    # Bootstrap:
    #   vault kv put secret/nordlynx \
    #     private_key="<your-nordvpn-wireguard-private-key>" \
    #     client_address="10.5.0.2"   # ← NordVPN assigns this; see API credentials response
    "nordlynx": {
        "private_key":     ("NORDLYNX_PRIVATE_KEY",    True,
                            "NordVPN WireGuard private key (see comments above for how to get it)"),
        "client_address":  ("NORDLYNX_CLIENT_ADDRESS", True,
                            "Client tunnel IP assigned by NordVPN, e.g. 10.5.0.2"),
    },
}

# Grouping for --section filter
SECTION_MAP = {
    "infra":      ["homelab/ssh", "proxmox/api/pve-01", "proxmox/api/pve-02",
                   "proxmox/api/pve-03", "homelab/proxmox/pve-02", "homelab/proxmox/pve-03"],
    "network":    ["homelab/opnsense", "vpn/wireguard", "vpn/wg-easy", "nordlynx"],
    "traefik":    ["traefik/certs", "traefik/acme", "traefik/dashboard"],
    "proxmox":    ["proxmox/api/pve-01", "proxmox/api/pve-02", "proxmox/api/pve-03",
                   "homelab/proxmox/pve-02", "homelab/proxmox/pve-03"],
    "database":   ["postgres/pg-01"],
    "iac":        ["iac/api_token"],
    "n8n":        ["docker/web-01", "n8n/webhook-tokens/jellyfin"],
    "gitlab":     ["gitlab/root", "gitlab/smtp", "gitlab/oidc", "gitlab/runner"],
    "outline":    ["outline/app", "outline/db", "outline/oidc", "outline/smtp"],
    "atlassian":  ["atlassian/jira/db", "atlassian/confluence/db"],
    "netbox":     ["netbox/api"],
    "homepage":   ["homepage/widgets"],
    "authelia":   ["authelia", "authelia/oidc", "authelia/users", "authelia/smtp"],
    "paperless":  ["paperless/app", "paperless/db", "paperless/oidc"],
    "wger":       ["wger/django", "wger/db", "wger/celery", "wger/email", "wger/recaptcha", "wger/tokens"],
    "apple-health": ["wger/tokens", "wger/accounts", "apple-health-bridge/webhook"],
    "minio":      ["minio/root", "minio/service-accounts/restic",
                   "minio/service-accounts/pgbackrest"],
    "restic":     ["restic/primary", "restic/offsite"],
    "backup":     ["minio/root", "minio/service-accounts/restic",
                   "minio/service-accounts/pgbackrest",
                   "restic/primary", "restic/offsite"],
    "immich":     ["immich/app", "immich/db", "immich/redis"],
    "nordlynx":   ["nordlynx"],
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_value(env_spec: str, env: dict[str, str]) -> str | None:
    """
    Resolve an env_spec that may be:
      "KEY"           — direct env var
      "KEY_FILE"      — read file pointed to by that env var
      "A|B"           — try A first, then B (supports _FILE fallback)
    """
    specs = env_spec.split("|")
    for spec in specs:
        if spec.endswith("_FILE"):
            path_val = env.get(spec)
            if path_val:
                p = Path(path_val.strip())
                try:
                    if p.is_file():
                        return p.read_text().strip()
                except PermissionError:
                    pass
        else:
            val = env.get(spec)
            if val and val.strip():
                return val.strip()
    return None


def _load_env(env_file: Path) -> dict[str, str]:
    if not env_file.is_file():
        console.print(f"[red]ERROR:[/red] env file not found: {env_file}")
        sys.exit(1)
    raw = dotenv_values(env_file)
    # Strip inline comments (dotenv_values handles most; this catches edge cases)
    return {k: v.split("#")[0].strip() if v else "" for k, v in raw.items()}


def _vault_client() -> hvac.Client:
    addr = os.environ.get("VAULT_ADDR")
    role_id = os.environ.get("VAULT_ROLE_ID")
    secret_id = os.environ.get("VAULT_SECRET_ID")

    if not addr:
        console.print("[red]ERROR:[/red] VAULT_ADDR environment variable not set.")
        sys.exit(1)
    if not role_id or not secret_id:
        console.print("[red]ERROR:[/red] VAULT_ROLE_ID and VAULT_SECRET_ID must be set.")
        console.print("These are the AppRole credentials — do NOT put them in vault_seed.env.")
        sys.exit(1)

    client = hvac.Client(url=addr)
    try:
        result = client.auth.approle.login(role_id=role_id, secret_id=secret_id)
        client.token = result["auth"]["client_token"]
        console.print(f"[green]✓[/green] Authenticated to Vault at {addr} "
                      f"(TTL: {result['auth']['lease_duration']}s)")
    except Exception as e:
        console.print(f"[red]ERROR:[/red] Vault authentication failed: {e}")
        sys.exit(1)
    return client


def _write_secret(client: hvac.Client, path: str, data: dict[str, Any],
                  dry_run: bool) -> bool:
    if dry_run:
        console.print(f"  [dim][DRY-RUN] Would write {len(data)} field(s) → secret/{path}[/dim]")
        return True
    try:
        client.secrets.kv.v2.create_or_update_secret(
            path=path, secret=data, mount_point="secret"
        )
        return True
    except Exception as e:
        console.print(f"  [red]WRITE FAILED[/red] secret/{path}: {e}")
        return False


def _verify_secret(client: hvac.Client, path: str,
                   expected_fields: list[str]) -> tuple[bool, list[str]]:
    try:
        resp = client.secrets.kv.v2.read_secret_version(
            path=path, mount_point="secret"
        )
        data = resp["data"]["data"]
        missing = [f for f in expected_fields if f not in data or not data[f]]
        return len(missing) == 0, missing
    except Exception as e:
        return False, [f"<read error: {e}>"]


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> int:
    env_file = Path(args.env_file)
    dry_run = args.dry_run
    check_only = args.check_only

    # Load env values
    if not check_only:
        env = _load_env(env_file)
    else:
        env = {}

    # Filter by section
    if args.section:
        section_key = args.section.lower()
        if section_key not in SECTION_MAP:
            console.print(f"[red]Unknown section:[/red] {args.section}")
            console.print("Available: " + ", ".join(SECTION_MAP.keys()))
            return 1
        paths_to_process = {k: v for k, v in SECRET_MAP.items()
                            if k in SECTION_MAP[section_key]}
    else:
        paths_to_process = SECRET_MAP

    # Connect to Vault (skip in dry-run)
    client = None
    if not dry_run:
        client = _vault_client()

    # ── Validate: collect all values before touching Vault ──────────────────
    if not check_only:
        console.print("\n[bold]Phase 1: Validating env file...[/bold]")
        errors: list[str] = []
        warnings: list[str] = []

        for vault_path, fields in paths_to_process.items():
            for vault_field, (env_spec, required, desc) in fields.items():
                val = _resolve_value(env_spec, env)
                if not val:
                    if required:
                        errors.append(
                            f"  REQUIRED  secret/{vault_path}.{vault_field}  "
                            f"({env_spec.split('|')[0]})"
                        )
                    else:
                        warnings.append(
                            f"  optional  secret/{vault_path}.{vault_field}  "
                            f"({env_spec.split('|')[0]})"
                        )

        if warnings:
            console.print(f"[yellow]⚠ {len(warnings)} optional field(s) not set "
                          f"(will be skipped):[/yellow]")
            for w in warnings:
                console.print(f"[dim]{w}[/dim]")

        if errors:
            console.print(f"\n[red]✗ {len(errors)} required field(s) missing:[/red]")
            for e in errors:
                console.print(f"[red]{e}[/red]")
            console.print(
                "\n[yellow]Fill these in vault_seed.env and re-run.[/yellow]"
            )
            return 1

        console.print(f"[green]✓ Validation passed[/green]")

    # ── Write phase ──────────────────────────────────────────────────────────
    if not check_only:
        console.print("\n[bold]Phase 2: Writing secrets to Vault...[/bold]")
        write_results: dict[str, bool] = {}

        for vault_path, fields in paths_to_process.items():
            secret_data: dict[str, Any] = {}
            for vault_field, (env_spec, required, _) in fields.items():
                val = _resolve_value(env_spec, env)
                if val:
                    secret_data[vault_field] = val

            if not secret_data:
                console.print(f"  [dim]skip[/dim]  secret/{vault_path} (no values set)")
                continue

            ok = _write_secret(client, vault_path, secret_data, dry_run)
            write_results[vault_path] = ok
            status = "[green]✓[/green]" if ok else "[red]✗[/red]"
            console.print(f"  {status}  secret/{vault_path}  "
                          f"({len(secret_data)} field(s))")

    # ── Verify phase ─────────────────────────────────────────────────────────
    if not dry_run:
        console.print("\n[bold]Phase 3: Verifying Vault state...[/bold]")
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Vault Path", style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Missing Fields")

        all_ok = True
        for vault_path, fields in paths_to_process.items():
            required_fields = [f for f, (_, req, _) in fields.items() if req]
            if not required_fields:
                continue
            ok, missing = _verify_secret(client, vault_path, required_fields)
            status = "[green]✓ OK[/green]" if ok else "[red]✗ MISSING[/red]"
            missing_str = ", ".join(missing) if missing else ""
            table.add_row(f"secret/{vault_path}", status, missing_str)
            if not ok:
                all_ok = False

        console.print(table)

        if all_ok:
            console.print(
                "\n[bold green]✓ All required secrets are present in Vault.[/bold green]"
            )
        else:
            console.print(
                "\n[bold red]✗ Some secrets are missing. "
                "Check the table above and re-run.[/bold red]"
            )
            return 1

    # ── Shred phase ──────────────────────────────────────────────────────────
    if args.shred and not dry_run and not check_only:
        console.print(f"\n[bold yellow]Shredding {env_file}...[/bold yellow]")
        _secure_delete(env_file)
        console.print("[green]✓ Shredded.[/green]")
        console.print(
            "[yellow]Run the following to clear shell history:[/yellow]\n"
            "  bash: history -c && history -w\n"
            "  zsh:  cat /dev/null > ~/.zsh_history && fc -R\n"
            "Or: bash scripts/secure_cleanup.sh"
        )

    # ── --wire: post-seed service sync ───────────────────────────────────────
    if args.wire and not dry_run and not check_only:
        _run_wire_step(client)

    # ── --check-api: verify live services ────────────────────────────────────
    if args.check_api:
        _check_live_services(client)

    return 0


def _check_live_services(client: "hvac.Client | None") -> None:
    """Ping IaC API, NetBox, and Homepage to verify they're up."""
    import time

    services_to_check: list[tuple[str, str, str | None]] = []

    # Read endpoints from Vault if client available
    if client is not None:
        try:
            iac = client.secrets.kv.v2.read_secret_version(
                path="iac/api_token", mount_point="secret"
            )["data"]["data"]
            services_to_check.append(("IaC API", iac.get("endpoint", "") + "/health",
                                       f"Bearer {iac.get('token','')}"))
        except Exception:
            pass
        try:
            nb = client.secrets.kv.v2.read_secret_version(
                path="netbox/api", mount_point="secret"
            )["data"]["data"]
            services_to_check.append(("NetBox", nb.get("url", "") + "/api/status/", None))
        except Exception:
            pass

    # Fall back to env vars
    if not services_to_check:
        iac_url = os.environ.get("IAC_API_URL", "http://iac.internal:8000")
        nb_url  = os.environ.get("NETBOX_URL",  "http://10.10.30.20:8001")
        hp_url  = os.environ.get("HOMEPAGE_URL", "http://10.10.30.20:3000")
        services_to_check = [
            ("IaC API",  f"{iac_url}/health", None),
            ("NetBox",   f"{nb_url}/api/status/", None),
            ("Homepage", f"{hp_url}/api/", None),
        ]

    import urllib.request, urllib.error
    console.print("\n[bold]Service health checks:[/bold]")
    for name, url, auth_header in services_to_check:
        if not url or url.startswith("/"):
            continue
        try:
            req = urllib.request.Request(url)
            if auth_header:
                req.add_header("Authorization", auth_header)
            urllib.request.urlopen(req, timeout=5)
            console.print(f"  [green]✓[/green]  {name:<12}  {url}")
        except Exception as e:
            code = getattr(e, 'code', None)
            if code and code < 500:
                console.print(f"  [green]✓[/green]  {name:<12}  {url}  (HTTP {code})")
            else:
                console.print(f"  [red]✗[/red]  {name:<12}  {url}  ({e})")


def _run_wire_step(client: "hvac.Client | None") -> None:
    """After seeding, run sync_homepage and sync_netbox."""
    import subprocess as sp
    scripts_dir = Path(__file__).parent / "scripts"

    for script, extra_args, label in [
        ("sync_homepage.py", ["--restart"], "Homepage config sync"),
        ("sync_netbox.py",   ["--all"],      "NetBox VM sync"),
    ]:
        script_path = scripts_dir / script
        if not script_path.is_file():
            console.print(f"[yellow]⚠ Not found: {script_path}[/yellow]")
            continue
        console.print(f"\n[bold]{label}...[/bold]")
        result = sp.run(
            [sys.executable, str(script_path)] + extra_args,
            capture_output=False,
        )
        if result.returncode != 0:
            console.print(f"[yellow]⚠ {label} exited {result.returncode} — check output above[/yellow]")


def _secure_delete(path: Path) -> None:
    """
    Overwrite file with zeros three times then unlink.
    Falls back to simple unlink if shred/sdelete is unavailable.
    """
    # Try platform shred tools first
    for cmd in [["shred", "-vzu", "--iterations=3", str(path)],
                ["sdelete", "-p", "3", str(path)]]:
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue

    # Manual overwrite fallback
    size = path.stat().st_size
    with open(path, "rb+") as f:
        for _ in range(3):
            f.seek(0)
            f.write(b"\x00" * size)
            f.flush()
            os.fsync(f.fileno())
        f.seek(0)
        f.write(os.urandom(size))
        f.flush()
        os.fsync(f.fileno())
    path.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    default_env = Path(__file__).parent / "secrets" / "vault_seed.env"

    parser = argparse.ArgumentParser(
        description="Homelab Vault secret provisioner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--env-file", default=str(default_env),
        help=f"Path to vault_seed.env (default: {default_env})"
    )
    parser.add_argument(
        "--check-only", action="store_true",
        help="Verify existing Vault state without writing anything"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be written without touching Vault"
    )
    parser.add_argument(
        "--shred", action="store_true",
        help="Securely delete env-file after successful write+verify"
    )
    parser.add_argument(
        "--section", metavar="NAME",
        help="Only process one section: " + ", ".join(SECTION_MAP.keys())
    )
    parser.add_argument(
        "--list-paths", action="store_true",
        help="Print all Vault paths this script manages and exit"
    )
    parser.add_argument(
        "--check-api", action="store_true",
        help="Ping IaC API, NetBox, and Homepage to verify they're up"
    )
    parser.add_argument(
        "--wire", action="store_true",
        help="After seeding, run sync_homepage.py + sync_netbox.py automatically"
    )

    args = parser.parse_args()

    if args.list_paths:
        table = Table(show_header=True, header_style="bold cyan", title="Managed Vault Paths")
        table.add_column("Vault Path", style="cyan")
        table.add_column("Fields")
        table.add_column("Required?")
        for path, fields in SECRET_MAP.items():
            req = [f for f, (_, r, _) in fields.items() if r]
            opt = [f for f, (_, r, _) in fields.items() if not r]
            table.add_row(
                f"secret/{path}",
                ", ".join(req + [f"[dim]{f}[/dim]" for f in opt]),
                "✓" if req else "optional",
            )
        console.print(table)
        sys.exit(0)

    sys.exit(run(args))


if __name__ == "__main__":
    main()
