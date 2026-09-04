#!/usr/bin/env bash
set -eu

ROOT=/opt/mathweaver
RELEASES=/opt/mathweaver/releases
CURRENT=/opt/mathweaver/current-teaching
PREVIOUS=/opt/mathweaver/previous-teaching
ENV_FILE=/opt/mathweaver/.env.teaching
BACKEND_UNIT=mathweaver-teaching-backend.service
AI_BACKEND_UNIT=mathweaver-teaching-ai-backend.service
PIPELINE_BACKEND_UNIT=mathweaver-teaching-pipeline-backend.service
FRONTEND_UNIT=mathweaver-teaching-frontend.service
NEO4J_UNIT=mathweaver-neo4j.service
BACKUP_UNIT=mathweaver-teaching-backup.service
BACKUP_TIMER=mathweaver-teaching-backup.timer
NGINX_CONFIG=/etc/nginx/conf.d/mathweaver-teaching-18080.conf
NEO4J_DATA_ROOT=/opt/mathweaver/neo4j
# 目标服务器的 python3 仍指向 3.6；固定使用已安装的 3.11，避免 pip 静默降级依赖。
PYTHON_BIN=python3.11

usage() {
  echo "usage: $0 {preflight|migrate|start|rollback} [release-directory]" >&2
  exit 64
}

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "deployment requires root" >&2
    exit 77
  fi
}

resolve_release() {
  local requested=${1:-}
  [ -n "$requested" ] || usage
  RELEASE_DIR=$(realpath -m "$requested")
  case "$RELEASE_DIR" in
    "$RELEASES"/*) ;;
    *) echo "release must be under $RELEASES" >&2; exit 65 ;;
  esac
  [ -f "$RELEASE_DIR/backend/requirements.txt" ] || {
    echo "release backend is incomplete" >&2
    exit 66
  }
}

check_sidecar_ports_available() {
  local port unit
  while read -r port unit; do
    if ss -ltnH | awk '{print $4}' | grep -Eq "(^|:)$port$"; then
      if ! systemctl is-active --quiet "$unit"; then
        echo "sidecar port $port is occupied outside $unit" >&2
        exit 69
      fi
    fi
  done <<EOF
5002 $BACKEND_UNIT
5003 $AI_BACKEND_UNIT
5004 $PIPELINE_BACKEND_UNIT
5174 $FRONTEND_UNIT
18080 nginx.service
EOF
}

load_environment() {
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  : "${MATHWEAVER_DATABASE_URL:?MATHWEAVER_DATABASE_URL is required}"
  : "${MATHWEAVER_DATABASE_NAME:?MATHWEAVER_DATABASE_NAME is required}"
  : "${NEO4J_URI:?NEO4J_URI is required}"
  : "${MATHWEAVER_DATA_KEY_FILE:?MATHWEAVER_DATA_KEY_FILE is required}"
  : "${MATHGRAPH_DATA_DIR:?MATHGRAPH_DATA_DIR is required}"
  if [ -z "${NEO4J_PASSWORD:-}" ] && [ -z "${NEO4J_PASSWORD_FILE:-}" ]; then
    echo "NEO4J_PASSWORD or NEO4J_PASSWORD_FILE is required" >&2
    exit 74
  fi
}

prepare_persistent_storage() {
  load_environment
  install -d -o nginx -g nginx -m 0750 \
    "$MATHGRAPH_DATA_DIR" \
    "$MATHGRAPH_DATA_DIR/jobs" \
    "$MATHGRAPH_DATA_DIR/uploads/source_pdfs" \
    "$MATHGRAPH_DATA_DIR/education" \
    "$MATHGRAPH_DATA_DIR/graph-staging"
  install -d -o neo4j -g neo4j -m 0750 \
    "$NEO4J_DATA_ROOT/data" \
    "$NEO4J_DATA_ROOT/logs" \
    "$NEO4J_DATA_ROOT/run"
  local path
  for path in \
    "$MATHGRAPH_DATA_DIR" \
    "$MATHGRAPH_DATA_DIR/jobs" \
    "$MATHGRAPH_DATA_DIR/uploads/source_pdfs" \
    "$MATHGRAPH_DATA_DIR/education" \
    "$MATHGRAPH_DATA_DIR/graph-staging"; do
    runuser -u nginx -- test -r "$path"
    runuser -u nginx -- test -w "$path"
    runuser -u nginx -- test -x "$path"
  done
  for path in "$NEO4J_DATA_ROOT/data" "$NEO4J_DATA_ROOT/logs" "$NEO4J_DATA_ROOT/run"; do
    runuser -u neo4j -- test -r "$path"
    runuser -u neo4j -- test -w "$path"
    runuser -u neo4j -- test -x "$path"
  done
}

wait_for_url() {
  local url=$1 attempt=0
  # systemd 已拉起进程不代表应用完成导入和监听，使用有上限的等待避免误判启动失败。
  while [ "$attempt" -lt 30 ]; do
    if curl --fail --silent "$url" >/dev/null 2>&1; then
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
  echo "service did not become ready: $url" >&2
  return 1
}

activate_link() {
  local target=$1
  local next_link="$ROOT/.current-teaching.next"
  ln -sfn "$target" "$next_link"
  mv -Tf "$next_link" "$CURRENT"
}

preflight() {
  resolve_release "$1"
  [ -f "$ENV_FILE" ] || { echo "missing teaching environment file" >&2; exit 67; }
  case "$(stat -c '%a' "$ENV_FILE")" in
    600|640) ;;
    *) echo "teaching environment file permissions must be 600 or 640" >&2; exit 68 ;;
  esac
  command -v "$PYTHON_BIN" >/dev/null
  command -v node >/dev/null
  command -v npm >/dev/null
  command -v nginx >/dev/null
  command -v curl >/dev/null
  command -v ss >/dev/null
  command -v systemctl >/dev/null
  command -v setfacl >/dev/null
  command -v runuser >/dev/null
  getent passwd nginx >/dev/null || { echo "required nginx service account is missing" >&2; exit 73; }
  getent passwd neo4j >/dev/null || { echo "required neo4j service account is missing" >&2; exit 73; }
  check_sidecar_ports_available
  df -Pk "$ROOT" | awk 'NR==2 { if ($4 < 2097152) exit 1 }'
  echo "preflight ok: $RELEASE_DIR"
}

migrate() {
  resolve_release "$1"
  local node_binary
  node_binary=$(command -v node)
  "$PYTHON_BIN" -m venv "$RELEASE_DIR/.venv"
  "$RELEASE_DIR/.venv/bin/pip" install --disable-pip-version-check -r "$RELEASE_DIR/backend/requirements.txt"
  # Node 来自 root 的 NVM；复制单个运行时到版本目录，避免服务进程穿越 /root。
  install -D -m 0755 "$node_binary" "$RELEASE_DIR/.runtime/node"
  # 服务器只运行 Web 旁路服务，跳过不会被使用且依赖外网下载的 Electron 桌面二进制。
  ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm --prefix "$RELEASE_DIR" ci
  npm --prefix "$RELEASE_DIR" run build
  load_environment
  cd "$RELEASE_DIR"
  "$RELEASE_DIR/.venv/bin/python" backend/scripts/upgrade_database.py
  "$RELEASE_DIR/.venv/bin/python" backend/scripts/migrate_legacy_mysql_storage.py --apply
  echo "migration ok: $RELEASE_DIR"
}

start_release() {
  resolve_release "$1"
  [ -x "$RELEASE_DIR/.venv/bin/gunicorn" ] && [ -x "$RELEASE_DIR/.runtime/node" ] && [ -f "$RELEASE_DIR/node_modules/@react-router/serve/bin.js" ] && [ -f "$RELEASE_DIR/build/server/index.js" ] || {
    echo "release has not completed migrate/build" >&2
    exit 70
  }
  if [ -L "$CURRENT" ]; then
    local old_target previous_next
    old_target=$(readlink -f "$CURRENT")
    previous_next="$ROOT/.previous-teaching.next"
    ln -sfn "$old_target" "$previous_next"
    mv -Tf "$previous_next" "$PREVIOUS"
  fi
  # 根目录与当前发布都保持 750，仅给 nginx 穿越权限，不开放目录枚举或敏感文件读取。
  setfacl -m u:nginx:--x "$ROOT"
  setfacl -m u:nginx:--x "$RELEASE_DIR"
  prepare_persistent_storage
  activate_link "$RELEASE_DIR"
  install -m 0644 "$RELEASE_DIR/deploy/systemd/$NEO4J_UNIT" "/etc/systemd/system/$NEO4J_UNIT"
  install -m 0644 "$RELEASE_DIR/deploy/systemd/$BACKEND_UNIT" "/etc/systemd/system/$BACKEND_UNIT"
  install -m 0644 "$RELEASE_DIR/deploy/systemd/$AI_BACKEND_UNIT" "/etc/systemd/system/$AI_BACKEND_UNIT"
  install -m 0644 "$RELEASE_DIR/deploy/systemd/$PIPELINE_BACKEND_UNIT" "/etc/systemd/system/$PIPELINE_BACKEND_UNIT"
  install -m 0644 "$RELEASE_DIR/deploy/systemd/$FRONTEND_UNIT" "/etc/systemd/system/$FRONTEND_UNIT"
  install -m 0644 "$RELEASE_DIR/deploy/systemd/$BACKUP_UNIT" "/etc/systemd/system/$BACKUP_UNIT"
  install -m 0644 "$RELEASE_DIR/deploy/systemd/$BACKUP_TIMER" "/etc/systemd/system/$BACKUP_TIMER"
  install -m 0644 "$RELEASE_DIR/deploy/nginx/mathweaver-teaching-18080.conf" "$NGINX_CONFIG"
  systemctl daemon-reload
  systemctl enable --now "$NEO4J_UNIT"
  systemctl enable --now "$BACKUP_TIMER"
  systemctl enable "$BACKEND_UNIT" "$AI_BACKEND_UNIT" "$PIPELINE_BACKEND_UNIT" "$FRONTEND_UNIT"
  # enable --now 不会重启已运行进程；切换软链后必须显式重启，确保执行的是新发布目录。
  systemctl restart "$BACKEND_UNIT" "$AI_BACKEND_UNIT" "$PIPELINE_BACKEND_UNIT" "$FRONTEND_UNIT"
  nginx -t
  systemctl reload nginx
  wait_for_url "http://127.0.0.1:5002/health/ready"
  wait_for_url "http://127.0.0.1:5003/health/ready"
  wait_for_url "http://127.0.0.1:5004/health/ready"
  wait_for_url "http://127.0.0.1:5174/"
  echo "sidecar release started: $RELEASE_DIR"
}

rollback() {
  [ -L "$PREVIOUS" ] || { echo "no previous teaching release" >&2; exit 71; }
  local target
  target=$(readlink -f "$PREVIOUS")
  case "$target" in
    "$RELEASES"/*) ;;
    *) echo "previous release target is invalid" >&2; exit 72 ;;
  esac
  prepare_persistent_storage
  activate_link "$target"
  systemctl enable --now "$NEO4J_UNIT"
  systemctl restart "$BACKEND_UNIT" "$AI_BACKEND_UNIT" "$PIPELINE_BACKEND_UNIT" "$FRONTEND_UNIT"
  wait_for_url "http://127.0.0.1:5002/health/ready"
  wait_for_url "http://127.0.0.1:5003/health/ready"
  wait_for_url "http://127.0.0.1:5004/health/ready"
  wait_for_url "http://127.0.0.1:5174/"
  echo "rolled back teaching sidecar to: $target"
}

require_root
ACTION=${1:-}
case "$ACTION" in
  preflight) [ $# -eq 2 ] || usage; preflight "$2" ;;
  migrate) [ $# -eq 2 ] || usage; migrate "$2" ;;
  start) [ $# -eq 2 ] || usage; start_release "$2" ;;
  rollback) [ $# -eq 1 ] || usage; rollback ;;
  *) usage ;;
esac
