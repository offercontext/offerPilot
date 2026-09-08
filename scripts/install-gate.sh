#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP_ROOT="$(mktemp -d)"
cleanup() {
  rm -rf "$TEMP_ROOT"
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

UV_TOOL_DIR="$INSTALL_TOOL_DIR" scripts/install.sh --source "$ROOT" --install-dir "$INSTALL_BIN" --name oc-install-gate
"$INSTALL_BIN/oc-install-gate" --help >/dev/null

echo "Install gate passed"
