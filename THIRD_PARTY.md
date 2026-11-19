# Third-party components

This repository is mostly configuration and glue code. It runs other people's programs as separate processes and
containers, and it imports a few libraries. This page lists what it uses, how, and under which license, so a reader can
check the chain. It is not legal advice. Entries marked **checked** were read from the package registry on 2026-11-19. Entries
marked **recalled** come from general knowledge and were not re-checked; verify them before relying on them.

This project is AGPL-3.0 (see [LICENSE](LICENSE)). The copyright holder may also offer it under commercial terms
(see [NOTICE.md](NOTICE.md)), which is why the "dual-license note" column matters.

## Files shipped in this repository

| Component | License | Where | Notes |
|---|---|---|---|
| wavesurfer.js 8.0.2 and its regions plugin | BSD-3-Clause, **checked** (npm) | `integration/music_studio_ui/static/` | Byte-identical to the jsDelivr build of the same version. The license text is in `wavesurfer.js-LICENSE.txt` next to the files |
| ChordSheetJS 18.0.0 | GPL-2.0-only, **checked** (npm) | not shipped | Cannot be combined with AGPL-3.0. Downloaded by `scripts/fetch_vendor.sh` with a checksum, never committed |

## Python libraries imported by the code

All are installed by the user, not shipped here. Licenses **checked** on PyPI.

| Library | License | Dual-license note |
|---|---|---|
| fastapi, pydantic, pytest, rich, PyYAML | MIT | none |
| httpx, uvicorn, voluptuous, python-dotenv | BSD-3-Clause | none |
| aiohttp, asyncpg, hvac, requests, homeassistant | Apache-2.0 (aiohttp: Apache-2.0 and MIT) | Apache-2.0 is compatible with GPL-3.0 and AGPL-3.0 |
| numpy | BSD-3-Clause and others, all permissive | none |
| psycopg2 | LGPL with exceptions | used as a library; no change |
| **piper-tts** | **GPL-3.0-or-later** | Imported lazily in `integration/code_partner.py`. GPL-3.0 and AGPL-3.0 may be combined, so the open license is fine. A commercial license for that one file would need either the piper-tts authors' consent or a rewrite that calls piper as a separate program |

## Programs run as separate processes

Containers and services that the configuration starts. Running an unmodified program does not put its license on this
repository. Licenses below are **recalled**; confirm against each project before redistributing images or modified copies.

- Permissive: PostgreSQL, Traefik (MIT), Authelia (Apache-2.0).
- Copyleft with a different scope: Pi-hole (EUPL-1.2).
- Network copyleft (AGPL-3.0): Grafana, Nextcloud, Immich, wg-easy and others. Obligations apply to whoever modifies and offers them as a service.
- Source-available, not open source: n8n (Sustainable Use License), Redis from version 8 (a choice of licenses including AGPL), Terraform and Vault (BUSL-1.1).

This repository does not include or redistribute any of them.

## Ansible collections and Terraform providers

Installed by the user from `ansible/requirements.yml` and from the providers named in `terraform/`. The community Ansible
collections are mostly GPL-3.0-or-later and the Terraform providers used here are mostly MPL-2.0 (**recalled**). They are used
as tools and are not part of this repository.

## Name

The name "Elementary Particles" is not licensed under the AGPL (see the README).

## Keeping this current

Add a row when a vendored file, a new Python import or a new copyleft dependency appears. `scripts/fetch_vendor.sh` is the
place for any browser library whose license cannot be combined with AGPL-3.0.
