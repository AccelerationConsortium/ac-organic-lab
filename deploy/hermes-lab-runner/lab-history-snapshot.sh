#!/usr/bin/env bash
# Consistent read-only snapshot of the live dashboard history DB, for the
# boxed hermes agent's lab-history MCP server.
#
# Why a snapshot and not the live file: the live DB is WAL-mode and mode 600.
# A read-only SQLite reader on a WAL database must write the -shm file, which
# would break the HERMES_ACCESS_DESIGN invariant that the hermes principal
# cannot write lab data. The snapshot is journal_mode=DELETE, so readers need
# no sidecar files and no write access at all.
#
# Runs as sdl2 (owner of the source). Atomic: build to .tmp, then rename.
set -euo pipefail

SRC=/data/dashboard/sqlite/lab.db
DIR=/data/dashboard/snapshots
DST=$DIR/lab.db
TMP=$DIR/.lab.db.tmp.$$

trap 'rm -f "$TMP"' EXIT

[ -r "$SRC" ] || { echo "source not readable: $SRC" >&2; exit 1; }
mkdir -p "$DIR"

python3 - "$SRC" "$TMP" <<'PY'
import sqlite3, sys
src_path, tmp_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
dst = sqlite3.connect(tmp_path)
src.backup(dst)                      # WAL-safe, consistent point-in-time copy
dst.execute("pragma journal_mode=DELETE")   # no -shm/-wal needed by readers
dst.execute("pragma query_only=1")
ok = dst.execute("pragma quick_check").fetchone()[0]
dst.close(); src.close()
if ok != "ok":
    raise SystemExit(f"integrity check failed: {ok}")
PY

# readable by hermes, writable by nobody but sdl2
chmod 640 "$TMP"
setfacl -m u:hermes:r "$TMP"
mv -f "$TMP" "$DST"
trap - EXIT

echo "snapshot ok: $(stat -c '%s bytes, %y' "$DST" | cut -d. -f1)"
