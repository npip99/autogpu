#!/bin/bash
# Run OpenLane synthesis on chip_top (preprocessed by sv2v).
#
# Usage:
#   ./run.sh           # fresh run
#   ./run.sh resume    # resume from the latest completed step of the most
#                      # recent run (skips already-done stages)
#
# Requires `make sv2v` to have been run first (or invoke via `make synth`).

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [[ ! -f "$REPO_ROOT/build/sv2v/chip_top.v" ]]; then
    echo "ERROR: build/sv2v/chip_top.v missing. Run 'make sv2v' first."
    exit 1
fi

cd "$SCRIPT_DIR"
source "$REPO_ROOT/.venv/bin/activate"

RESUME_FLAG=()
if [[ "$1" == "resume" ]]; then
    LATEST_RUN=$(ls -td runs/RUN_* 2>/dev/null | head -1 || true)
    if [[ -z "$LATEST_RUN" ]]; then
        echo "No prior run found; doing a fresh run."
    else
        LAST_STATE=$(ls -t "$LATEST_RUN"/*/state_out.json 2>/dev/null | head -1 || true)
        if [[ -z "$LAST_STATE" ]]; then
            echo "Prior run has no completed step; doing a fresh run."
        else
            echo "Resuming from $LAST_STATE"
            RESUME_FLAG=(--with-initial-state "$LAST_STATE")
        fi
    fi
fi

exec python -m openlane --docker-no-tty --dockerized --pdk sky130A \
    "${RESUME_FLAG[@]}" config.yaml
