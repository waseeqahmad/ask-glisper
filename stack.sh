#!/usr/bin/env bash
# Bring the hackathon workflow up or down: Zetaris (Docker) + Nightshift (app).
#   ./stack.sh up | down | status     (no argument = toggle)
# Docker Desktop itself is left running on "down": other projects use it.
set -uo pipefail
cd "$(dirname "$0")/zetaris-platform"

NS_APP="Nightshift M1-M4"
UI="http://localhost:$(sed -n 's/^UI_PORT=//p' .env | tail -1)"
NS="http://127.0.0.1:8088/v1/models"

wait_for() {  # wait_for <label> <seconds> <command...>
  local label=$1 secs=$2; shift 2
  printf '  waiting for %s' "$label"
  for _ in $(seq "$secs"); do "$@" >/dev/null 2>&1 && { echo " ok"; return 0; }; printf .; sleep 1; done
  echo " TIMED OUT"; return 1
}
http_up() { curl -s -m 2 -o /dev/null "$1"; }
running() { [ -n "$(docker compose ps -q --status running driver 2>/dev/null)" ]; }

up() {
  echo "Starting..."
  if ! docker info >/dev/null 2>&1; then
    open -a Docker && wait_for "Docker Desktop" 120 docker info || exit 1
  fi
  docker compose up -d || exit 1
  wait_for "Zetaris UI at $UI" 600 curl -sf -m 2 -o /dev/null "$UI/api/config" || exit 1
  open -a "$NS_APP" && wait_for "Nightshift API" 60 http_up "$NS" || exit 1
  echo "Up. Zetaris: $UI   Nightshift: http://127.0.0.1:8088/v1   Test: python3 demo.py"
}

down() {
  echo "Stopping..."
  docker compose stop   # keeps containers and data; "up" restarts them fast
  osascript -e "quit app \"$NS_APP\"" 2>/dev/null
  # ponytail: hard kill only if the app ignores quit; its runtime process holds port 8088
  sleep 3; pkill -f "$NS_APP.app/Contents/MacOS/" 2>/dev/null
  echo "Down. (Docker Desktop left running for other projects.)"
}

status() {
  running && echo "Zetaris:    up ($UI)" || echo "Zetaris:    down"
  http_up "$NS" && echo "Nightshift: up" || echo "Nightshift: down"
}

case "${1:-toggle}" in
  up) up ;;
  down) down ;;
  status) status ;;
  toggle) if running; then down; else up; fi ;;
  *) echo "usage: $0 [up|down|status]"; exit 2 ;;
esac
