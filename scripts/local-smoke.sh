#!/usr/bin/env bash
set -euo pipefail

PORT=""
SKIP_BUILD=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build)
      SKIP_BUILD=1
      ;;
    *)
      if [[ -z "$PORT" && "$1" =~ ^[0-9]{1,5}$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 )); then
        PORT="$1"
      else
        echo "Usage: local-smoke.sh [port] [--skip-build]" >&2
        exit 2
      fi
      ;;
  esac
  shift
done
PORT="${PORT:-18765}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

cd "$ROOT/web"
if [[ "$SKIP_BUILD" -eq 0 ]]; then
  npm run build
fi
if [[ ! -f "$ROOT/web/dist/index.html" ]]; then
  echo "Missing web/dist/index.html. Run a successful frontend build before using --skip-build." >&2
  exit 1
fi

cd "$ROOT"
DATA_DIR="${OFFERPILOT_SMOKE_DATA:-$(mktemp -d)}"
OFFERPILOT_DATA="$DATA_DIR" uv run oc start --port "$PORT" &
SERVER_PID="$!"

for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null; then
    break
  fi
  sleep 0.5
done

curl -fsS "http://127.0.0.1:$PORT/api/health" | grep -q '"status":"ok"'
curl -fsS "http://127.0.0.1:$PORT/applications/smoke" | grep -q 'root'
OFFERPILOT_DATA="$DATA_DIR" uv run oc smoke --static-dir web/dist

echo "Local smoke passed at http://127.0.0.1:$PORT"
