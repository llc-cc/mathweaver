#!/usr/bin/env bash
set -eu

ROOT=/opt/mathweaver
RELEASES=/opt/mathweaver/releases
CURRENT=/opt/mathweaver/current-teaching
PREVIOUS=/opt/mathweaver/previous-teaching
ENV_FILE=/opt/mathweaver/.env.teaching
BACKEND_UNIT=mathweaver-teaching-backend.service
FRONTEND_UNIT=mathweaver-teaching-frontend.service
NGINX_CONFIG=/etc/nginx/conf.d/mathweaver-teaching-18080.conf
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

check_sidecar_ports_free() {
  local port
  for port in 5002 5174 18080; do
    if ss -ltnH | awk '{print $4}' | grep -Eq "(^|:)$port$"; then
      echo "sidecar port $port is already occupied" >&2
      exit 69
    fi
  done
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
  command -v npm >/dev/null
  command -v nginx >/dev/null
  command -v curl >/dev/null
  command -v ss >/dev/null
  getent passwd nginx >/dev/null || { echo "required nginx service account is missing" >&2; exit 73; }
  check_sidecar_ports_free
  df -Pk "$ROOT" | awk 'NR==2 { if ($4 < 2097152) exit 1 }'
  echo "preflight ok: $RELEASE_DIR"
}

migrate() {
  resolve_release "$1"
  "$PYTHON_BIN" -m venv "$RELEASE_DIR/.venv"
  "$RELEASE_DIR/.venv/bin/pip" install --disable-pip-version-check -r "$RELEASE_DIR/backend/requirements.txt"
  # 服务器只运行 Web 旁路服务，跳过不会被使用且依赖外网下载的 Electron 桌面二进制。
  ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm --prefix "$RELEASE_DIR" ci
  npm --prefix "$RELEASE_DIR" run build
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  cd "$RELEASE_DIR"
  "$RELEASE_DIR/.venv/bin/python" -m alembic -c backend/migrations/alembic.ini current
  "$RELEASE_DIR/.venv/bin/python" -m alembic -c backend/migrations/alembic.ini upgrade head
  echo "migration ok: $RELEASE_DIR"
}

start_release() {
  resolve_release "$1"
  [ -x "$RELEASE_DIR/.venv/bin/gunicorn" ] && [ -f "$RELEASE_DIR/build/server/index.js" ] || {
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
  activate_link "$RELEASE_DIR"
  install -m 0644 "$RELEASE_DIR/deploy/systemd/$BACKEND_UNIT" "/etc/systemd/system/$BACKEND_UNIT"
  install -m 0644 "$RELEASE_DIR/deploy/systemd/$FRONTEND_UNIT" "/etc/systemd/system/$FRONTEND_UNIT"
  install -m 0644 "$RELEASE_DIR/deploy/nginx/mathweaver-teaching-18080.conf" "$NGINX_CONFIG"
  systemctl daemon-reload
  systemctl enable --now "$BACKEND_UNIT" "$FRONTEND_UNIT"
  nginx -t
  systemctl reload nginx
  curl --fail --silent --show-error http://127.0.0.1:5002/api/v2/ready >/dev/null
  curl --fail --silent --show-error http://127.0.0.1:5174/ >/dev/null
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
  activate_link "$target"
  systemctl restart "$BACKEND_UNIT" "$FRONTEND_UNIT"
  curl --fail --silent --show-error http://127.0.0.1:5002/api/v2/ready >/dev/null
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
