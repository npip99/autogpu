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
EXTRA_OPENLANE_ARGS="${EXTRA_OPENLANE_ARGS:-}"
python -m openlane --docker-no-tty --dockerized --pdk sky130A $EXTRA_OPENLANE_ARGS config.yaml
SYNTH_RC=$?
[[ $SYNTH_RC -ne 0 ]] && exit $SYNTH_RC

# Auto-render the final GDS to PNG. Skip silently if no final/ dir
# (e.g. flow exited before streamout, --skip caused partial result).
LATEST_RUN="$(ls -td "$CONFIG_DIR/runs/RUN_"* 2>/dev/null | head -1)"
GDS="$LATEST_RUN/final/gds/$MODULE.gds"
PNG_DIR="$REPO_ROOT/build/render"
PNG="$PNG_DIR/$MODULE.png"
if [[ -f "$GDS" ]]; then
    mkdir -p "$PNG_DIR"
    echo "Rendering $MODULE.png …"
    sg docker -c "docker run --rm \
        -v $REPO_ROOT:/work \
        -v $HOME/.volare:/root/.volare \
        -e RES=8192 \
        ghcr.io/efabless/openlane2:2.3.10 \
        klayout -b -r /work/tech/sky130/render_layout.py \
          -rd layout_path=/work/${GDS#$REPO_ROOT/} \
          -rd out_png=/work/${PNG#$REPO_ROOT/}" 2>&1 | tail -1
    echo "new GDS/png available: $PNG"
else
    echo "(no final/ dir — skipping PNG render; was --skip used?)"
fi
