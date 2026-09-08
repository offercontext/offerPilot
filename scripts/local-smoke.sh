#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-18765}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${OFFERPILOT_SMOKE_DATA:-$(mktemp -d)}"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

cd "$ROOT/web"
npm run build

cd "$ROOT"
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
