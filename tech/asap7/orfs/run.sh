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
source "$SCRIPT_DIR/orfs_image.sh"   # pinned ORFS image (was :latest)
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
# NUM_CORES caps OpenROAD's thread count per container. Default to
# nproc, but override (e.g. NUM_CORES=8) when running several modules
# in parallel to avoid oversubscription on a busy host.
NUM_CORES="${NUM_CORES:-$(nproc)}"
# ORFS make uses incremental targets keyed on file timestamps in
# results/. A config.mk knob change (e.g. CORE_UTILIZATION) does NOT
# invalidate those, so we wipe the per-module result/object/log trees
# before each run. Skip with KEEP=1 if you want make's incremental
# behavior (e.g. resuming after a single-step failure).
if [[ -z "$KEEP" ]]; then
    rm -rf "$WORK_HOST/results/asap7/$MODULE" \
           "$WORK_HOST/objects/asap7/$MODULE" \
           "$WORK_HOST/logs/asap7/$MODULE" \
           "$WORK_HOST/reports/asap7/$MODULE"
fi
# The `generate_abstract` target runs the GDS flow then synthesizes a
# LEF + LIB from the final layout. We always run it so the macro is
# usable as a black-box in a parent design (e.g. compute_array).
sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO_ROOT:/work \
    -w /OpenROAD-flow-scripts/flow \
    ${ORFS_IMAGE} \
    make \
      DESIGN_CONFIG=/work/tech/asap7/orfs/${MODULE}.config.mk \
      WORK_HOME=$WORK_GUEST \
      NUM_CORES=$NUM_CORES \
      GDS_FINISHING_TOOL=klayout \
      RUN_KLAYOUT_DRC=1 \
      LEC_CHECK=0 \
      generate_abstract"
ORFS_RC=$?
[[ $ORFS_RC -ne 0 ]] && exit $ORFS_RC

# Re-write the abstract LEF without -bloat_occupied_layers, so the module
# can be used as a hardened macro inside another design without blocking
# the parent's PDN. ORFS's stock generate_abstract.tcl always passes
# -bloat_occupied_layers, which tags every routing layer the module
# touched as an obstruction over the whole macro footprint — including
# the layers we deliberately reserved for parent PDN via MAX_ROUTING_LAYER.
# Always do this — harmless if nobody uses the macro as a leaf.
echo "Re-writing $MODULE.lef with bloated obstructions (then stripping M6/M7) …"
sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO_ROOT:/work \
    -w /OpenROAD-flow-scripts/flow \
    -e MODULE_NAME=$MODULE \
    -e RESULTS_DIR=/work/build/orfs/results/asap7/$MODULE/base \
    -e TECH_LEF=/OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7_tech_1x_201209.lef \
    -e SC_LEF=/OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7sc7p5t_28_R_1x_220121a.lef \
    ${ORFS_IMAGE} \
    /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad -exit \
    /work/tech/asap7/orfs/scripts/rewrite_abstract_lef.tcl" 2>&1 | tail -3
# Post-process: strip OBS so the parent design can use these layers over
# the macro footprint without conflicting.
#   M1  — parent runs M1 followpins for any stdcells in the inter-macro
#         channels. Macro M1 OBS would fragment those into "unrepairable
#         channels" (PDN-0179). Power-only layer, no signal conflict.
#   M4  — abutment pin layer for W/E pairs. Bloating M4 OBS across the
#         full tile face blocks DRT from landing pin-access vias at the
#         abutment seam, causing tiny M4 boundary shorts (issue #32 Phase B
#         hit 37–45 of these even with 2 µm CORE_AREA inset). M4 is sparse
#         inside the tile so parent over-macro M4 routing is unlikely to
#         conflict. M3 OBS still blocks broad parent routing across the tile.
#   M6/M7 — parent's PDN stripes go here. Leaf internal PDN uses M5/M6
#         which the bloat tags; stripping lets parent stripes pass through.
python3 $REPO_ROOT/tech/asap7/orfs/scripts/strip_lef_obs_layers.py \
    $REPO_ROOT/build/orfs/results/asap7/$MODULE/base/$MODULE.lef M1 M2 M4 M5 M6 M7

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
