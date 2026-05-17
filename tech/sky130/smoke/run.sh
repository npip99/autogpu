#!/bin/bash
# Run the OpenLane sky130 toolchain smoke test.
#
# Prereqs:
#   - Docker installed; user in docker group (or invoke via `sg docker -c ./run.sh`)
#   - `uv sync --extra synth` has been run in repo root
#   - OpenLane image pulled: `docker pull ghcr.io/efabless/openlane2:2.3.10`
#
# Outputs a GDS at runs/<timestamp>/final/gds/smoke_top.gds on success.
#
# Note: --docker-no-tty must come BEFORE --dockerized.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

cd "$SCRIPT_DIR"
source "$REPO_ROOT/.venv/bin/activate"
exec python -m openlane --docker-no-tty --dockerized --pdk sky130A config.yaml
