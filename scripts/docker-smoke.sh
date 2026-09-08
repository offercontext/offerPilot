#!/usr/bin/env bash
set -euo pipefail

IMAGE="${1:-offerpilot:smoke}"

docker build -t "$IMAGE" .

# Run the same command the image exposes through ENTRYPOINT: oc smoke.
docker run --rm \
  -e OFFERPILOT_DATA=/tmp/offerpilot-smoke \
  "$IMAGE" \
  smoke --static-dir /app/web/dist
