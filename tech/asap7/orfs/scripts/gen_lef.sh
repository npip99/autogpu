#!/bin/bash
# Generate abstract LEF + LIB for a module that already has 6_final.odb
# from a prior build (but no generate_abstract was run). Reads the ODB
# in-place (under build/orfs/results/asap7/<module>/base/) and produces
# <module>.lef + <module>_typ.lib next to it.
#
# Usage: gen_lef.sh <module> [extra_lef ...]
#   e.g. gen_lef.sh compute_array_tiny_bcast0 mac_tmem_cell.lef cmd_unit.lef
set -e
MODULE="${1:?usage: $0 <module> [extra_lef ...]}"
shift
EXTRAS=("$@")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
source "$SCRIPT_DIR/../orfs_image.sh"   # pinned ORFS image (was :latest)

RESULTS_HOST="$REPO_ROOT/build/orfs/results/asap7/$MODULE/base"
if [[ ! -f "$RESULTS_HOST/6_final.odb" ]]; then
    echo "ERROR: $RESULTS_HOST/6_final.odb missing." >&2
    exit 1
fi

ASAP7_RESULTS_GUEST=/work/build/orfs/results/asap7
EXTRA_LEFS_GUEST=""
EXTRA_LIBS_GUEST=""
for e in "${EXTRAS[@]}"; do
    # Each extra is "<module>" → resolves to <module>/base/<module>.lef + <module>_typ.lib
    EXTRA_LEFS_GUEST+=" $ASAP7_RESULTS_GUEST/$e/base/$e.lef"
    EXTRA_LIBS_GUEST+=" $ASAP7_RESULTS_GUEST/$e/base/${e}_typ.lib"
done

# asap7 stdcell liberty files — RVT TT corner is what ORFS uses for typ.
ASAP7_LIB_DIR=/OpenROAD-flow-scripts/flow/platforms/asap7/lib/NLDM
LIBERTY_FILES=" \
    $ASAP7_LIB_DIR/asap7sc7p5t_INVBUF_RVT_TT_nldm_220122.lib.gz \
    $ASAP7_LIB_DIR/asap7sc7p5t_SEQ_RVT_TT_nldm_220123.lib \
    $ASAP7_LIB_DIR/asap7sc7p5t_AO_RVT_TT_nldm_211120.lib.gz \
    $ASAP7_LIB_DIR/asap7sc7p5t_OA_RVT_TT_nldm_211120.lib.gz \
    $ASAP7_LIB_DIR/asap7sc7p5t_SIMPLE_RVT_TT_nldm_211120.lib.gz"

sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO_ROOT:/work \
    -w /OpenROAD-flow-scripts/flow \
    -e MODULE_NAME=$MODULE \
    -e RESULTS_DIR=$ASAP7_RESULTS_GUEST/$MODULE/base \
    -e TECH_LEF=/OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7_tech_1x_201209.lef \
    -e SC_LEF=/OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7sc7p5t_28_R_1x_220121a.lef \
    -e EXTRA_LEFS='$EXTRA_LEFS_GUEST' \
    -e EXTRA_LIBS='$EXTRA_LIBS_GUEST' \
    -e LIBERTY_FILES='$LIBERTY_FILES' \
    ${ORFS_IMAGE} \
    /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad -exit \
    /work/tech/asap7/orfs/scripts/gen_lef_from_odb.tcl"

# Strip M6/M7 OBS so parent's PDN can pass over the macro.
python3 $REPO_ROOT/tech/asap7/orfs/scripts/strip_lef_obs_layers.py \
    $RESULTS_HOST/$MODULE.lef M1 M2 M5 M6 M7

echo "Generated $RESULTS_HOST/$MODULE.lef and ${MODULE}_typ.lib"
