#!/bin/bash
# Run one submodule synthesis. Usage: ./run.sh <module_name>
#   e.g. ./run.sh fp32_fma
#
# Requires `make sv2v` at the parent tech/sky130/ level to have produced
# build/sv2v/chip_top.v (with USE_SKY130_MACRO + current parameters baked in).

set -e
MODULE="${1:?usage: $0 <module_name>}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SV2V_FILE="$REPO_ROOT/build/sv2v/chip_top.v"

if [[ ! -f "$SV2V_FILE" ]]; then
    echo "ERROR: $SV2V_FILE missing. Run 'make -C $REPO_ROOT/tech/sky130 sv2v' first."
    exit 1
fi

CONFIG_DIR="$SCRIPT_DIR/$MODULE"
if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
    echo "ERROR: $CONFIG_DIR/config.yaml missing."
    exit 1
fi

cd "$CONFIG_DIR"
source "$REPO_ROOT/.venv/bin/activate"
exec python -m openlane --docker-no-tty --dockerized --pdk sky130A config.yaml
