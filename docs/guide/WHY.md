# Why this exists

Why this repository exists, what it runs, and where it is heading. Back to the [README](../../README.md).

Photos, music, the family calendars, our health data, our documents: I want them on hardware I own, under rules I set. That is the r/selfhosted mantra. No one else can change the price on it, pilfer and mine it, discontinue it or lose it in a breach.

Owning your data also means guarding it. When you self-host, nobody else is on call, so security has to be built into how the system runs, not added at the end. Also, AI keeps getting better at finding vulnerabilities, making a neglected service a bigger risk every month. So everything here is designed around a few habits:

- **Patching is routine, not heroic.** Updates run on a schedule, with snapshots first, health checks after and an automatic rollback.
- **Nothing is exposed that doesn't need to be.** Services sit behind single sign-on, networks are split into VLANs, and the firewall blocks traffic between them by default.
- **Secrets live in a vault, not in config files.** Access to those secrets are gated.
- **Backups are tested.** A backup you've never restored is a hope, not a backup.
- **Trouble shows up before it's an outage.** Monitoring warns me about a filling disk or a database that stops answering before my family notices.

I have a full-time job, a family and other hobbies, so I can't watch it all the time. It has to keep working when I am not looking. That turns a homelab into what r/homelab half-jokingly calls "homeprod": the moment the family depends on it, it is production.

And that's the real reason I keep going late at night. A feature which makes my wife's day easier and less stressful beats any clever dashboard. It also gets my oldest ready for school: the time, the forecast, and whether he needs a jacket, even when he insists he doesn't.

The other half is the joy of building. Wiring a dozen open source projects into one system, writing the missing pieces myself, breaking things and learning why: that's the r/homelab spirit, and AI has made it more fun than ever. Local models take care of the household AI work, so my family's data stays at home and I can now build in an evening what used to take a month of weekends.

Where it's heading is a full-blown private AI concierge, with a separate agent for each person in the household. It curates my photos and music recordings, automates the house, watches for bad weather, helps with workouts and recipes, and warns me when it predicts a problem. It boils the news down to what's useful, keeps me company while creating music, and helps plan trips. It even scouts for job opportunities and the latest tech trends. It grows one small piece at a time, and there's always more in the pipeline.

I build this with Claude Code and a local code partner. I decide what gets built, and I review and merge every change. The sections below show how the agents are kept in check.

## What it runs

For the family: files, calendars and contacts (Nextcloud), photos (Immich), movies and music
(Jellyfin), scanned documents (Paperless-ngx), a wiki (Outline), recipes (Tandoor), household
finances (Firefly III), workouts (WGER) and the smart home (Home Assistant with Frigate cameras and Alarmo).
Behind them sit the AI concierge, a music studio toolset and the news and travel helpers as well as a journal app.

Under the hood: Proxmox, Terraform, Ansible, Vault, Traefik with Authelia single sign-on,
OPNsense with separate VLANs, Wireguard, PostgreSQL, Prometheus and Grafana, Pulse, NetBox and n8n for automation. The
full list is in the [solution overview](../SOLUTION_OVERVIEW.md).
