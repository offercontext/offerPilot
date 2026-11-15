#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/offerpilot-install-gate.XXXXXXXX")"
SUCCEEDED=0
KEEP_ARTIFACTS=0
if [[ "${1:-}" == "--keep-artifacts" ]]; then KEEP_ARTIFACTS=1; shift; fi
if [[ $# -gt 0 ]]; then echo "Unknown install gate argument: $1" >&2; exit 2; fi
cleanup() {
  if [[ "$SUCCEEDED" -eq 1 && "$KEEP_ARTIFACTS" -eq 0 ]]; then
    case "$TEMP_ROOT" in
      "${TMPDIR:-/tmp}"/offerpilot-install-gate.*) rm -rf -- "$TEMP_ROOT" ;;
      *) echo "Refusing unsafe install-gate cleanup" >&2; return 1 ;;
    esac
  else
    echo "Install gate artifacts retained at $TEMP_ROOT"
  fi
}
trap cleanup EXIT

TOOL_DIR="$TEMP_ROOT/uv-tools"
BIN_DIR="$TEMP_ROOT/bin"
INSTALL_TOOL_DIR="$TEMP_ROOT/install-tools"
INSTALL_BIN="$TEMP_ROOT/install-bin"
mkdir -p "$TOOL_DIR" "$BIN_DIR" "$INSTALL_TOOL_DIR" "$INSTALL_BIN"

cd "$ROOT"
uv run oc --help >/dev/null

UV_TOOL_DIR="$TOOL_DIR" UV_TOOL_BIN_DIR="$BIN_DIR" uv tool install --force .
"$BIN_DIR/oc" --help >/dev/null
"$TOOL_DIR/offerpilot/bin/python" -c "import importlib.util; assert importlib.util.find_spec('mcp') is None"
UV_TOOL_DIR="$TOOL_DIR" UV_TOOL_BIN_DIR="$BIN_DIR" uv tool install --force '.[mcp]'
"$BIN_DIR/oc" mcp --help >/dev/null
"$TOOL_DIR/offerpilot/bin/python" scripts/external-agent-acceptance.py prepare --data-dir "$TEMP_ROOT/mcp-acceptance"
"$TOOL_DIR/offerpilot/bin/python" scripts/external-agent-acceptance.py verify --data-dir "$TEMP_ROOT/mcp-acceptance" --expected pending

UV_TOOL_DIR="$INSTALL_TOOL_DIR" scripts/install.sh --source "$ROOT" --install-dir "$INSTALL_BIN" --name oc-install-gate
"$INSTALL_BIN/oc-install-gate" --help >/dev/null

SUCCEEDED=1
echo "Install gate passed (base + MCP extra, real stdio; no automated approval)"
