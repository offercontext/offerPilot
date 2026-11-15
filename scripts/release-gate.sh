#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="18765"
RUN_REAL_AI=0
RUN_DOCKER=0
RUN_INSTALL=0
EVAL_SUITE=""
EVAL_POLICY=""
EVAL_VARIANTS=""
EVAL_OUTPUT=""
EVAL_BASELINE_ID=""
EVAL_BASELINE_STORE=""
EVAL_REVIEW=""
EVAL_EXPERIMENT=""
EVAL_PROVIDER=""
EVAL_GRANT=""
EVAL_REUSE=()
EVAL_TIMEOUT=""
ISOLATE_BACKEND_TESTS=0
BACKEND_OUTPUT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --isolate-backend-tests) ISOLATE_BACKEND_TESTS=1; shift ;;
    --backend-output) BACKEND_OUTPUT="${2:?Missing backend output}"; shift 2 ;;
    --eval-suite) EVAL_SUITE="${2:?Missing eval suite}"; shift 2 ;;
    --eval-policy) EVAL_POLICY="${2:?Missing eval policy}"; shift 2 ;;
    --eval-variants) EVAL_VARIANTS="${2:?Missing eval variants}"; shift 2 ;;
    --eval-output) EVAL_OUTPUT="${2:?Missing eval output}"; shift 2 ;;
    --eval-baseline-id) EVAL_BASELINE_ID="${2:?Missing eval baseline ID}"; shift 2 ;;
    --eval-baseline-store) EVAL_BASELINE_STORE="${2:?Missing eval baseline store}"; shift 2 ;;
    --eval-review) EVAL_REVIEW="${2:?Missing eval review}"; shift 2 ;;
    --eval-experiment) EVAL_EXPERIMENT="${2:?Missing eval experiment}"; shift 2 ;;
    --eval-provider-profile) EVAL_PROVIDER="${2:?Missing eval provider profile}"; shift 2 ;;
    --eval-grant) EVAL_GRANT="${2:?Missing eval grant}"; shift 2 ;;
    --eval-reuse) EVAL_REUSE+=("--reuse" "${2:?Missing reused run}"); shift 2 ;;
    --eval-timeout-seconds) EVAL_TIMEOUT="${2:?Missing eval timeout}"; shift 2 ;;
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
if [[ "$ISOLATE_BACKEND_TESTS" -eq 1 && -z "$BACKEND_OUTPUT" ]]; then
  echo '--backend-output is required with --isolate-backend-tests' >&2; exit 2
fi
if [[ "$ISOLATE_BACKEND_TESTS" -eq 0 && -n "$BACKEND_OUTPUT" ]]; then
  echo '--backend-output requires --isolate-backend-tests' >&2; exit 2
fi
if [[ -n "$EVAL_SUITE$EVAL_POLICY$EVAL_VARIANTS$EVAL_OUTPUT$EVAL_BASELINE_ID$EVAL_BASELINE_STORE$EVAL_REVIEW$EVAL_EXPERIMENT$EVAL_PROVIDER$EVAL_GRANT$EVAL_TIMEOUT" || ${#EVAL_REUSE[@]} -gt 0 ]]; then
  if [[ -n "$EVAL_TIMEOUT" ]]; then
    [[ "$EVAL_TIMEOUT" =~ ^[0-9]+(\.[0-9]+)?$ && "$EVAL_TIMEOUT" =~ [1-9] ]] || { echo "Positive eval timeout required" >&2; exit 2; }
  fi
  for value in "$EVAL_SUITE" "$EVAL_POLICY" "$EVAL_VARIANTS" "$EVAL_OUTPUT" "$EVAL_BASELINE_ID" "$EVAL_BASELINE_STORE" "$EVAL_REVIEW" "$EVAL_EXPERIMENT"; do
    [[ -n "$value" ]] || { echo "Complete evaluation parameters are required before release checks" >&2; exit 2; }
  done
  EVAL_ARGS=(--suite "$EVAL_SUITE" --policy "$EVAL_POLICY" --variants "$EVAL_VARIANTS" --output "$EVAL_OUTPUT" --baseline-id "$EVAL_BASELINE_ID" --baseline-store "$EVAL_BASELINE_STORE" --review "$EVAL_REVIEW" --experiment "$EVAL_EXPERIMENT")
  [[ -z "$EVAL_PROVIDER" ]] || EVAL_ARGS+=(--provider-profile "$EVAL_PROVIDER")
  [[ -z "$EVAL_GRANT" ]] || EVAL_ARGS+=(--grant "$EVAL_GRANT")
  uv run oc eval release-preflight "${EVAL_ARGS[@]}" "${EVAL_REUSE[@]}"
  [[ -s "$EVAL_OUTPUT/release-preflight.json" ]] || { echo "Evaluation preflight artifact missing" >&2; exit 2; }
fi
if [[ "$ISOLATE_BACKEND_TESTS" -eq 1 ]]; then
  uv run python -m scripts.test_selection.backend_gate --output "$BACKEND_OUTPUT"
  backend_receipt="$(uv run python -m scripts.test_selection.backend_gate --output "$BACKEND_OUTPUT" --verify)"
  [[ "$backend_receipt" =~ ^BACKEND_FULL_VERIFIED\ [a-f0-9]{64}$ ]] || { echo 'Full backend execution receipt missing or invalid' >&2; exit 2; }
else
  uv run pytest -q
fi
uv run ruff check .
uv run mypy src

cd "$ROOT/web"
npm test
npm run build

cd "$ROOT"
scripts/local-smoke.sh "$PORT" --skip-build
uv run oc verify --profile local --static-dir web/dist

if [[ "$RUN_REAL_AI" -eq 1 ]]; then
  uv run oc verify --profile real-ai --static-dir web/dist
fi

if [[ "$RUN_INSTALL" -eq 1 ]]; then
  scripts/install-gate.sh --keep-artifacts
fi

if [[ "$RUN_DOCKER" -eq 1 ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker was requested but the docker command is not available." >&2
    exit 1
  fi
  scripts/docker-smoke.sh
fi

if [[ -n "$EVAL_SUITE" ]]; then
  EXECUTE_ARGS=(--preflight "$EVAL_OUTPUT/release-preflight.json")
  [[ -z "$EVAL_TIMEOUT" ]] || EXECUTE_ARGS+=(--timeout-seconds "$EVAL_TIMEOUT")
  uv run oc eval release-execute "${EXECUTE_ARGS[@]}"
  [[ -s "$EVAL_OUTPUT/release-evaluation.json" ]] || { echo "Evaluation receipt missing" >&2; exit 2; }
  receipt="$(uv run oc eval release-receipt --preflight "$EVAL_OUTPUT/release-preflight.json")"
  [[ "$receipt" =~ ^EVAL_ENFORCE_VERIFIED\ [a-f0-9]{64}$ ]] || { echo "Evaluation enforce verification missing" >&2; exit 2; }
fi
echo "Release gate passed"
