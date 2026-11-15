#!/bin/bash
# pgbackrest_textfile.sh -- pgBackRest + WAL-archiver health for node_exporter's
# textfile collector (added 2026-10-23). Run every 5 min by
# pgbackrest-textfile.timer; feeds the PgBackRest*/PostgresWal* rules in
# Roles/monitoring/templates/rules/pg01_backup.yml.j2.
#
# Why: archiving silently failed for two months (0 segments archived) until
# WAL filled the disk on 2026-09-02. Every one of these numbers would have
# shown it.
set -u
STANZA=${1:?stanza}
OUT=/var/lib/prometheus/node-exporter/pgbackrest.prom
TMP=$(mktemp "$OUT.XXXX")

info=$(sudo -u postgres pgbackrest --stanza="$STANZA" --output=json info 2>/dev/null)
read -r ok full diff incr count < <(echo "$info" | python3 -c '
import json, sys
try:
    s = json.load(sys.stdin)[0]
    last = {"full": 0, "diff": 0, "incr": 0}
    for b in s.get("backup", []):
        last[b["type"]] = max(last[b["type"]], b["timestamp"]["stop"])
    # a full backup also satisfies "latest backup of any type"
    print(1 if s["status"]["code"] in (0, 2) else 0, last["full"], last["diff"], last["incr"], len(s.get("backup", [])))
except Exception:
    print(0, 0, 0, 0, 0)')

arch=$(sudo -u postgres psql -XtA -F' ' -c "select archived_count, failed_count,
  coalesce(extract(epoch from last_archived_time)::bigint, 0),
  coalesce(extract(epoch from last_failed_time)::bigint, 0),
  (current_setting('archive_mode') = 'on')::int from pg_stat_archiver" 2>/dev/null)
read -r archived failed last_ok last_fail mode_on <<< "${arch:-0 0 0 0 0}"

spool=$(du -sb /var/spool/pgbackrest 2>/dev/null | cut -f1)
wal=$(du -sb "$(readlink -f /var/lib/postgresql/15/main/pg_wal)" 2>/dev/null | cut -f1)
latest=$(( full > incr ? (full > diff ? full : diff) : (incr > diff ? incr : diff) ))

cat > "$TMP" <<EOF
# HELP pgbackrest_stanza_ok 1 if pgbackrest info reports the stanza usable
# TYPE pgbackrest_stanza_ok gauge
pgbackrest_stanza_ok{stanza="$STANZA"} $ok
# HELP pgbackrest_backups Number of backups in the repo
# TYPE pgbackrest_backups gauge
pgbackrest_backups{stanza="$STANZA"} $count
# HELP pgbackrest_last_backup_timestamp_seconds Stop time of the newest backup by type (0 = none)
# TYPE pgbackrest_last_backup_timestamp_seconds gauge
pgbackrest_last_backup_timestamp_seconds{stanza="$STANZA",type="full"} $full
pgbackrest_last_backup_timestamp_seconds{stanza="$STANZA",type="diff"} $diff
pgbackrest_last_backup_timestamp_seconds{stanza="$STANZA",type="incr"} $incr
pgbackrest_last_backup_timestamp_seconds{stanza="$STANZA",type="any"} $latest
# HELP pg_archiver_archive_mode_on 1 if archive_mode is on
# TYPE pg_archiver_archive_mode_on gauge
pg_archiver_archive_mode_on $mode_on
# HELP pg_archiver_archived_total WAL segments archived (pg_stat_archiver)
# TYPE pg_archiver_archived_total counter
pg_archiver_archived_total $archived
# HELP pg_archiver_failed_total Failed archive attempts (pg_stat_archiver)
# TYPE pg_archiver_failed_total counter
pg_archiver_failed_total $failed
# HELP pg_archiver_last_archived_timestamp_seconds Last successful archive
# TYPE pg_archiver_last_archived_timestamp_seconds gauge
pg_archiver_last_archived_timestamp_seconds $last_ok
# HELP pg_archiver_last_failed_timestamp_seconds Last failed archive
# TYPE pg_archiver_last_failed_timestamp_seconds gauge
pg_archiver_last_failed_timestamp_seconds $last_fail
# HELP pgbackrest_spool_bytes Async archive spool size (grows when the repo is unreachable)
# TYPE pgbackrest_spool_bytes gauge
pgbackrest_spool_bytes ${spool:-0}
# HELP pg_wal_dir_bytes Size of pg_wal (grows when archiving fails)
# TYPE pg_wal_dir_bytes gauge
pg_wal_dir_bytes ${wal:-0}
# HELP pgbackrest_textfile_timestamp_seconds When this file was written
# TYPE pgbackrest_textfile_timestamp_seconds gauge
pgbackrest_textfile_timestamp_seconds $(date +%s)
EOF
chmod 0644 "$TMP" && mv "$TMP" "$OUT"
