#!/bin/bash
# Render any intermediate ORFS ODB checkpoint to a PNG so you can see the
# state of the design at that step (e.g. post-placement, post-CTS, post-GRT)
# even if the flow died at a later step and we have no 6_final.gds.
#
# Usage: render_odb.sh <module> <odb_basename>
#   e.g. render_odb.sh compute_array 3_5_place_dp
#        render_odb.sh compute_array 4_cts
#        render_odb.sh compute_array 2_4_floorplan_pdn
#
# Output: build/render/<module>_<odb_basename>.png
set -e
MODULE="${1:?usage: $0 <module> <odb_basename>}"
ODB="${2:?usage: $0 <module> <odb_basename>}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
ODB_PATH="$REPO_ROOT/build/orfs/results/asap7/$MODULE/base/${ODB}.odb"

if [[ ! -f "$ODB_PATH" ]]; then
    echo "ERROR: $ODB_PATH missing"
    exit 1
fi

# Step 1: open the ODB in OpenROAD and write out a DEF. Use a tmp script
# file rather than a here-doc — here-docs don't survive `sg docker -c`'s
# multi-level shell quoting and silently produce no output.
DEF_HOST="$REPO_ROOT/build/render/${MODULE}_${ODB}.def"
mkdir -p "$(dirname $DEF_HOST)"
TCL_TMP=$(mktemp /tmp/render_odb.XXXXXX.tcl)
cat > "$TCL_TMP" <<EOF
read_lef /OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7_tech_1x_201209.lef
read_lef /OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7sc7p5t_28_R_1x_220121a.lef
foreach m {mac_tmem_cell skew_lane_a skew_lane_b cmd_unit} {
    set lef /work/build/orfs/results/asap7/\$m/base/\$m.lef
    if {[file exists \$lef]} { read_lef \$lef }
}
read_db /work/build/orfs/results/asap7/$MODULE/base/${ODB}.odb
write_def /work/build/render/${MODULE}_${ODB}.def
EOF
sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO_ROOT:/work \
    -v /tmp:/tmp \
    openroad/orfs:latest \
    /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad -exit $TCL_TMP" 2>&1 | tail -3
rm -f "$TCL_TMP"

if [[ ! -f "$DEF_HOST" ]]; then
    echo "ERROR: DEF write failed"
    exit 1
fi

# Step 2: render the DEF as PNG via klayout. render_layout.py handles
# .def vs .gds branching based on extension.
PNG="$REPO_ROOT/build/render/${MODULE}_${ODB}.png"
echo "Rendering $PNG …"
sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO_ROOT:/work \
    -v $HOME/.volare:$HOME/.volare \
    -e RES=4096 \
    -e PDK_ROOT=$HOME/.volare \
    ghcr.io/efabless/openlane2:2.3.10 \
    klayout -b -r /work/tech/asap7/render_layout.py \
      -rd layout_path=/work/${DEF_HOST#$REPO_ROOT/} \
      -rd out_png=/work/${PNG#$REPO_ROOT/}" 2>&1 | tail -1
echo "Wrote $PNG"
