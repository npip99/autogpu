#!/bin/bash
# Run one submodule synthesis through ORFS + asap7 + KLayout-only streamout.
# Usage: ./run.sh <module_name>
#   e.g. ./run.sh mac_tmem_cell
#
# Requires `make -C tech/sky130 sv2v` at the repo root to have produced
# build/sv2v/chip_top.v (the sv2v'd flattened design).
set -e
MODULE="${1:?usage: $0 <module_name>}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SV2V_FILE="$REPO_ROOT/build/sv2v/chip_top.v"

if [[ ! -f "$SV2V_FILE" ]]; then
    echo "ERROR: $SV2V_FILE missing. Run 'make -C $REPO_ROOT/tech/sky130 sv2v' first."
    exit 1
fi

CONFIG_FILE="$SCRIPT_DIR/${MODULE}.config.mk"
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: $CONFIG_FILE missing."
    exit 1
fi

# Build artifacts (logs/, objects/, reports/, results/) live under
# build/orfs/ at the repo root so they're easy to gitignore + clean.
WORK_HOST="$REPO_ROOT/build/orfs"
WORK_GUEST="/work/build/orfs"
mkdir -p "$WORK_HOST"

# Magic is broken on asap7 in the orfs:latest image (no asap7.magicrc),
# so we drop the Magic-based streamout/DRC steps and rely on KLayout.
sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO_ROOT:/work \
    -w /OpenROAD-flow-scripts/flow \
    openroad/orfs:latest \
    make \
      DESIGN_CONFIG=/work/tech/asap7/orfs/${MODULE}.config.mk \
      WORK_HOME=$WORK_GUEST \
      GDS_FINISHING_TOOL=klayout \
      RUN_KLAYOUT_DRC=1"
ORFS_RC=$?
[[ $ORFS_RC -ne 0 ]] && exit $ORFS_RC

GDS="$WORK_HOST/results/asap7/$MODULE/base/6_final.gds"
PNG_DIR="$REPO_ROOT/build/render"
PNG="$PNG_DIR/${MODULE}_asap7.png"
if [[ -f "$GDS" ]]; then
    mkdir -p "$PNG_DIR"
    echo "Rendering ${MODULE}_asap7.png …"
    sg docker -c "docker run --rm --user $(id -u):$(id -g) \
        -v $REPO_ROOT:/work \
        -v $HOME/.volare:$HOME/.volare \
        -e RES=8192 \
        -e PDK_ROOT=$HOME/.volare \
        ghcr.io/efabless/openlane2:2.3.10 \
        klayout -b -r /work/tech/asap7/render_layout.py \
          -rd layout_path=/work/${GDS#$REPO_ROOT/} \
          -rd out_png=/work/${PNG#$REPO_ROOT/}" 2>&1 | tail -1
    echo "new GDS/png available: $PNG"
else
    echo "(no $GDS — flow didn't reach streamout)"
fi
