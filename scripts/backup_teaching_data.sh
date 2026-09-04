#!/usr/bin/env bash
set -euo pipefail

ROOT=/opt/mathweaver
CURRENT=$ROOT/current-teaching
ENV_FILE=$ROOT/.env.teaching
BACKUP_ROOT=${MATHWEAVER_BACKUP_ROOT:-$ROOT/backups}
STAMP=$(date +%Y%m%d-%H%M%S)
FINAL_DIR=$BACKUP_ROOT/teaching-$STAMP
PARTIAL_DIR=$BACKUP_ROOT/.teaching-$STAMP.partial
LOCK_FILE=/run/mathweaver-teaching-backup.lock
MYSQL_DEFAULTS=

cleanup() {
  if [ -n "$MYSQL_DEFAULTS" ]; then
    rm -f -- "$MYSQL_DEFAULTS"
  fi
  case "$PARTIAL_DIR" in
    "$BACKUP_ROOT"/.teaching-*.partial) rm -rf -- "$PARTIAL_DIR" ;;
  esac
}
trap cleanup EXIT

if [ "$(id -u)" -ne 0 ]; then
  echo "backup requires root" >&2
  exit 77
fi
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE" >&2; exit 66; }
[ -x "$CURRENT/.venv/bin/python" ] || { echo "release Python is unavailable" >&2; exit 67; }
command -v flock >/dev/null
command -v mysqldump >/dev/null
command -v gzip >/dev/null
command -v tar >/dev/null
command -v sha256sum >/dev/null

exec 9>"$LOCK_FILE"
flock -n 9 || { echo "another teaching backup is running" >&2; exit 75; }
umask 077
install -d -m 0700 "$BACKUP_ROOT"
install -d -m 0700 "$PARTIAL_DIR"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
: "${MATHWEAVER_DATABASE_URL:?MATHWEAVER_DATABASE_URL is required}"
: "${MATHWEAVER_DATABASE_NAME:?MATHWEAVER_DATABASE_NAME is required}"
: "${MATHGRAPH_DATA_DIR:?MATHGRAPH_DATA_DIR is required}"
[ "$MATHWEAVER_DATABASE_NAME" = mathweaver ] || { echo "unexpected database name" >&2; exit 68; }
[ -d "$MATHGRAPH_DATA_DIR" ] || { echo "persistent file directory is missing" >&2; exit 69; }

MYSQL_DEFAULTS=$(mktemp "$BACKUP_ROOT/.mysql-client.XXXXXX")
DATABASE_NAME=$(
  "$CURRENT/.venv/bin/python" - "$MYSQL_DEFAULTS" <<'PY'
import os
import sys
from pathlib import Path
from sqlalchemy.engine import make_url

url = make_url(os.environ["MATHWEAVER_DATABASE_URL"])
if url.get_backend_name() != "mysql" or url.database != "mathweaver":
    raise SystemExit("refusing to back up an unexpected database target")

def quoted(value: object) -> str:
    return '"' + str(value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'

Path(sys.argv[1]).write_text(
    "[client]\n"
    f"host={quoted(url.host)}\n"
    f"port={int(url.port or 3306)}\n"
    f"user={quoted(url.username)}\n"
    f"password={quoted(url.password)}\n"
    "default-character-set=utf8mb4\n",
    encoding="utf-8",
)
print(url.database)
PY
)
chmod 600 "$MYSQL_DEFAULTS"

mysqldump \
  --defaults-extra-file="$MYSQL_DEFAULTS" \
  --single-transaction --quick --routines --triggers --events --hex-blob \
  --no-tablespaces --set-gtid-purged=OFF \
  "$DATABASE_NAME" | gzip -c >"$PARTIAL_DIR/mysql.sql.gz"

(
  cd "$CURRENT/backend"
  ../.venv/bin/python scripts/export_graph_backup.py \
    --output "$PARTIAL_DIR/neo4j-graphs.json.gz"
)

DATA_PARENT=$(dirname "$MATHGRAPH_DATA_DIR")
DATA_NAME=$(basename "$MATHGRAPH_DATA_DIR")
tar -czf "$PARTIAL_DIR/data-teaching.tar.gz" -C "$DATA_PARENT" "$DATA_NAME"

{
  echo "created_at=$(date --iso-8601=seconds)"
  echo "release=$(readlink -f "$CURRENT")"
  echo "database=$DATABASE_NAME"
  echo "data_directory=$MATHGRAPH_DATA_DIR"
} >"$PARTIAL_DIR/METADATA"

(
  cd "$PARTIAL_DIR"
  sha256sum mysql.sql.gz neo4j-graphs.json.gz data-teaching.tar.gz METADATA >SHA256SUMS
  sha256sum -c SHA256SUMS
  gzip -t mysql.sql.gz
  tar -tzf data-teaching.tar.gz >/dev/null
  "$CURRENT/.venv/bin/python" "$CURRENT/backend/scripts/export_graph_backup.py" \
    --restore neo4j-graphs.json.gz
)

mv "$PARTIAL_DIR" "$FINAL_DIR"
ln -sfn "$FINAL_DIR" "$BACKUP_ROOT/latest-teaching"
PARTIAL_DIR=
echo "teaching backup completed: $FINAL_DIR"
