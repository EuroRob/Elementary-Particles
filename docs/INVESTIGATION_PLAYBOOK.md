# Investigation playbook: from "it feels slow" to a root cause

How the 2026-11-06 evening went from "pve-01 swap is almost full" to the actual cause (host freezes
from memory fragmentation) and the fixes. It also found Pulse's 135,000 stored alerts for a host
that is off by design. Written so the next investigation, by a person or an agent, can repeat the
method.

Nothing here is new tooling. It's existing commands used in a disciplined order. The full record
of that evening is in memory note `incident_pve-01_swap_full_2026-11-06`.

## The method in six rules

1. **Read what we already know first.** Look in `MEMORY.md` and the incident notes it points to,
   plus the `CLAUDE.md` files.
   - Twice that evening, history changed the plan:
     - The Alertmanager incident from 2026-10-19 predicted that Prometheus wouldn't restart after
       a reboot. The watchdog built after it brought Prometheus back.
     - `Terraform/CLAUDE.md` showed that ballooning had squeezed jellyfin-01 below its floor. That
       led to the "floor = size" design instead of plain ballooning.
2. **Measure before you change anything, and from more than one side.** Look at the host, the
   guest and the app.
   - "Swap 97% full" looked like an emergency. The other two views said otherwise: MemAvailable
     was 14 GB, memory pressure was 0, and nothing was swapping out.
3. **Find the moment, then look at every layer at that minute.**
   - The question that cracked it: *when* did swap jump? The answer was 13:04 and 19:04 UTC. At
     19:04 the pve-01 journal showed pvestatd taking 61 s, and every VM lost memory at once.
   - All VMs at once means the host reclaimed memory. One VM growing would mean a guest problem.
4. **Count, don't skim.** Turn logs and tables into counts per message type, per day and per
   resource. The outlier stands out:
   - 135,059 of 149,473 alert rows belonged to one host, pve-02.
   - 42,000 refused API calls a day.
5. **Prove a hypothesis before acting on it, and say which ones are still unproven.**
   - Plex buffering: the files were read fast, so pve-03 wasn't the cause. The route through fw-01
     was *plausible*, and it was reported as unproven, not as the answer.
6. **Change one thing at a time, with a check before and after.**
   - A Terraform plan that shows "No changes" before editing anything.
   - Then the plan with the edit, then one VM restart at a time, each followed by a health check.

## Toolkit: what answered which question

### Host memory (Proxmox)

| Question | Command / source |
|---|---|
| Is there real pressure, or is it just cold swap? | `cat /proc/pressure/memory`; `vmstat 1 5` (si/so columns); `free -m` (available, not free) |
| Is it fragmentation? | `cat /proc/buddyinfo` (zeros in the right-hand columns mean no 2 MB blocks); `grep -E 'compact_stall\|compact_fail\|allocstall_normal' /proc/vmstat` |
| Which VM holds what? | For each `/var/run/qemu-server/<id>.pid`: `VmRSS`/`VmSwap` from `/proc/<pid>/status`, compared with `memory:`/`balloon:` in `/etc/pve/qemu-server/<id>.conf` |
| Who is pulling pages back from swap now? | Major faults per qemu process over 30 s (`/proc/<pid>/stat`, field 12) |
| ZFS cache | `/proc/spl/kstat/zfs/arcstats` (`size`, `c_max`) |
| Did the host freeze? | `journalctl -u pvestatd \| grep 'status update time'` (over 15 s means the host stalled) |
| History, without Prometheus | `pvesh get /nodes/<node>/rrddata --timeframe day` (host swap/memory per minute) and `/nodes/<node>/qemu/<id>/rrddata --timeframe hour` (per VM). Both are JSON, so sort them in a short Python script |

### Inside guests, without SSH

- `qm guest exec <vmid> -- sh -c "free -m; cat /proc/pressure/memory"` from the Proxmox host. It
  needs only the guest agent and gives the guest's own view (used vs cache). That decided which
  VMs could be cut and which couldn't (gpu-01 and home-assistant).
- Before cutting a VM, read what it is tuned for. dmz-pg-01's Postgres had
  `shared_buffers = 512MB` plus 50 × 16 MB `work_mem`, so it was dropped from the cuts.

### Application logs

- **JSON logs** (Pulse, many Go and Python services): `journalctl --user -u <unit> -o cat`, parse
  each line as JSON, and count `(level, normalized message)` with digits replaced by `N`. The top 10
  is usually the whole story.
- **Rates per day:** the same counting with `-o short-iso` and the first 10 characters as the key.
  This shows whether a problem is new or has been there all along.
- **Plex:** `Logs/Plex Media Server*.log` inside the container. To tell real buffering from normal
  buffering:
  - Read the `reporting timeline state buffering` lines together with the file requests around
    them. *Direct play* plus fast reads means the server isn't the bottleneck.
  - Jumping positions are seeks.
  - A position at the end of the file is just the video ending.

### Databases an app keeps to itself (SQLite)

Open them **read-only**: `sqlite3.connect("file:x.db?mode=ro", uri=True)`, ideally with the app
stopped. Four queries found the bloat:
```sql
pragma page_count; pragma page_size; pragma freelist_count;    -- size and free space
select tier, count(*), min(timestamp) from metrics group by tier; -- what retention really holds
select sum(length(<col>)) from <table>;                          -- per column: which one is fat
select resource_name, count(*) from <t> group by 1 order by 2 desc limit 10; -- who
```
Before editing: copy the files, run `pragma integrity_check`, delete with a narrow `WHERE`, then
`vacuum` and run the integrity check again.

### Vendor claims ("will an update fix it?")

Read the release notes and docs for the versions in question, and quote them, don't guess. On
2026-11-06 that showed:
- Pulse 6.7.0 fixes none of what we saw.
- Proxmox 9 needs `VM.GuestAgent.FileRead` for guest memory reads, which we chose not to grant.

## Safety habits that kept it safe

- **Secrets:**
  - Parse `/opt/vault/.env` raw in Python, never with `source`.
  - Credentials pass from Vault to the request without being printed.
  - New automation gets a **scoped** token, for example Pulse `settings:read/write`, not the admin
    login. It goes straight into Vault and a local mode-600 file.
- **Terraform:**
  - Run it through the env wrapper. Without the Vault variables, `plan` **hangs silently**.
  - A plan with no edits must show "No changes" before you edit anything. On 2026-11-06 a stale
    local `terraform.tfvars` would have moved web-01's disk.
- **Ansible on hosts with history:**
  - Check mode first, with tags that select only the new tasks.
  - `include_role` with `apply: tags` tags **every** task in the file; don't use it to narrow a
    run.
- **Line endings:** CRLF files are edited in binary mode, otherwise the whole file shows up in
  the diff.

## Mistakes from 2026-11-06, so they aren't repeated

| Mistake | Lesson |
|---|---|
| `pkill -f '<pattern>'` killed its own shell | Kill by PID after `pgrep`, or use a pattern that can't match the command line running it |
| Ran `terraform plan` without the Vault env; it hung for 5 min | Always use the env wrapper (`tf.py`) |
| Capped Pulse at 768 MB, then 1.2 GB, then 1.8 GB; each throttled it into a dead UI | A systemd memory cap also counts the service's **file cache**. Measure `anon` vs `file` in `memory.stat` before capping |
| Restarted Pulse to "free memory" and it took 10+ min to come back | Find out what a restart costs (database size, startup work) before restarting a long-running service |
| Assumed pve-01's IP and SSH user | Look them up in memory or `~/.ssh/config` first |
| Proposed 6–8 GB of VM cuts before measuring inside the guests | Measure first; the real safe cut was 2.3 GB |

## What we can build to make this faster

- **An alert on memory pressure, not swap percentage.** Alert when MemAvailable is under 2 GB or
  memory pressure stays above a few percent. Swap % misled us at first.
- **Keep the investigation scripts.** The per-VM memory table, the JSON log counter and the SQLite
  size report were each written ad hoc that evening. A `scripts/investigate/` folder would make
  them one command next time.
