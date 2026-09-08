#!/usr/bin/env sh
# OfferPilot one-line installer.
#
#   curl -sSL https://get.offerpilot.dev | sh
#   curl -sSL https://get.offerpilot.dev | sh -s -- --install-dir ~/.local/bin
#
# Installs the Python `oc` CLI using uv. This requires Python 3.10+ and uv.

set -eu

REPO="offercontext/offerpilot"
INSTALL_DIR="${INSTALL_DIR:-${HOME}/.local/bin}"
INSTALL_NAME="${INSTALL_NAME:-oc}"
SOURCE="${SOURCE:-https://github.com/${REPO}.git}"

err() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; }
info() { printf '\033[36m=>\033[0m %s\n' "$*"; }

require_tools() {
  if ! command -v uv >/dev/null 2>&1; then
    err "uv is required. Install it first: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
  fi
}

install_source() {
  mkdir -p "$INSTALL_DIR"
  export UV_TOOL_BIN_DIR="$INSTALL_DIR"
  info "installing OfferPilot from ${SOURCE}"
  uv tool install --python 3.12 --force "$SOURCE"
  if [ "$INSTALL_NAME" != "oc" ]; then
    if [ -f "${INSTALL_DIR}/oc" ]; then
      mv "${INSTALL_DIR}/oc" "${INSTALL_DIR}/${INSTALL_NAME}"
    else
      err "expected uv to install ${INSTALL_DIR}/oc"
      exit 1
    fi
  fi
  info "installed ${INSTALL_NAME} to ${INSTALL_DIR}/${INSTALL_NAME}"
}

main() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --install-dir) INSTALL_DIR="$2"; shift 2 ;;
      --name) INSTALL_NAME="$2"; shift 2 ;;
      --source) SOURCE="$2"; shift 2 ;;
      -h|--help)
        sed -n '2,12p' "$0"
        exit 0 ;;
      *) err "unknown flag: $1"; exit 1 ;;
    esac
  done
  require_tools
  install_source
  info "next: run '${INSTALL_NAME} config --api-key sk-xxx' then '${INSTALL_NAME} start'"
  info "ensure ${INSTALL_DIR} is on your PATH"
}

main "$@"
