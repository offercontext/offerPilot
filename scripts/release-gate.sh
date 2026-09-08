#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="18765"
RUN_REAL_AI=0
RUN_DOCKER=0
RUN_INSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --real-ai)
      RUN_REAL_AI=1
      shift
      ;;
    --docker)
      RUN_DOCKER=1
      shift
      ;;
    --install)
      RUN_INSTALL=1
      shift
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

cd "$ROOT"
uv run pytest -q
uv run ruff check .
uv run mypy src

cd "$ROOT/web"
npm test
npm run build

cd "$ROOT"
scripts/local-smoke.sh "$PORT"
uv run oc verify --profile local --static-dir web/dist

if [[ "$RUN_REAL_AI" -eq 1 ]]; then
  uv run oc verify --profile real-ai --static-dir web/dist
fi

if [[ "$RUN_INSTALL" -eq 1 ]]; then
  scripts/install-gate.sh
fi

if [[ "$RUN_DOCKER" -eq 1 ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker was requested but the docker command is not available." >&2
    exit 1
  fi
  scripts/docker-smoke.sh
fi

echo "Release gate passed"
