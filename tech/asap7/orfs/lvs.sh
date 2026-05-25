#!/bin/bash
# LVS (Layout-vs-Schematic) driver for an asap7 hardened block.
#
# Usage: ./lvs.sh <module> [--gds path] [--verilog path]
#   ./lvs.sh mac_tmem_cell
#   ./lvs.sh compute_array_tiny_bcast0
#
# Defaults to comparing the post-route 6_final.gds against 6_final.v
# under build/orfs/results/asap7/<module>/base/.
#
# Method: cell-instance LVS via KLayout's LayoutToNetlist + Verilog
# netlist parser. asap7 has no shipped transistor-level LVS rules (see
# tech/asap7/DESIGN.md for the PDK gap analysis), so standard cells +
# hardened macros are treated as black-box subcircuits. The check
# catches: misplaced/missing cells, shorts/opens in routing, pin
# swaps, mis-routed buses, and floating macro power pins. It does not
# check standard-cell-internal transistor topology.
#
# Exit code: 0 = LVS clean, 1 = LVS fail, 2 = configuration error.
#
# Report: reports/asap7/<module>/lvs.log (plus a one-line summary on
# stdout).
set -e

MODULE="${1:?usage: $0 <module> [--gds path] [--verilog path]}"
shift || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
RESULTS_HOST="$REPO_ROOT/build/orfs/results/asap7/$MODULE/base"
RESULTS_GUEST="/work/build/orfs/results/asap7/$MODULE/base"

# Allow overriding GDS / Verilog paths (rare — e.g. for comparing against
# a hand-edited golden netlist or a different module's run).
GDS="$RESULTS_HOST/6_final.gds"
VERILOG="$RESULTS_HOST/6_final.v"
while [[ $# -gt 0 ]]; do
    case $1 in
        --gds)     GDS="$2"; shift 2 ;;
        --verilog) VERILOG="$2"; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

if [[ ! -f "$GDS" ]]; then
    echo "ERROR: $GDS missing. Run './tech/asap7/orfs/run.sh $MODULE' first." >&2
    exit 2
fi
if [[ ! -f "$VERILOG" ]]; then
    echo "ERROR: $VERILOG missing." >&2
    exit 2
fi

# The "module" the user names on the command line is the ORFS run
# directory name (e.g., compute_array_tiny_bcast0), but the actual top
# cell in the GDS / Verilog module can differ (e.g., DESIGN_NAME
# compute_array with DESIGN_NICKNAME compute_array_tiny_bcast0). Read
# the config.mk to pick up DESIGN_NAME if available; otherwise default
# to the module name.
TOP_CELL="$MODULE"
CFG="$SCRIPT_DIR/${MODULE}.config.mk"
if [[ -f "$CFG" ]]; then
    cfg_top=$(grep -E '^export\s+DESIGN_NAME\s*=' "$CFG" | head -1 \
              | sed -E 's/^export\s+DESIGN_NAME\s*=\s*([^[:space:]]+).*/\1/')
    [[ -n "$cfg_top" ]] && TOP_CELL="$cfg_top"
fi

REPORT_HOST="$REPO_ROOT/build/orfs/reports/asap7/$MODULE/lvs.log"
REPORT_GUEST="/work/build/orfs/reports/asap7/$MODULE/lvs.log"
mkdir -p "$(dirname "$REPORT_HOST")"
NETLIST_OUT_HOST="$REPO_ROOT/build/orfs/reports/asap7/$MODULE/layout_netlist.cir"
NETLIST_OUT_GUEST="/work/build/orfs/reports/asap7/$MODULE/layout_netlist.cir"

# Translate host paths under $REPO_ROOT into the in-container /work prefix.
# We mount the entire repo, plus the build tree, plus the script into
# /work. If the user passed --gds/--verilog with a non-repo path, fall
# back to mounting that file directly.
to_guest() {
    local p="$1"
    case "$p" in
        "$REPO_ROOT"/*)  echo "/work/${p#$REPO_ROOT/}" ;;
        *)               echo "$p" ;;
    esac
}
GDS_GUEST="$(to_guest "$GDS")"
VERILOG_GUEST="$(to_guest "$VERILOG")"

EXTRA_MOUNTS=()
[[ "$GDS_GUEST" == "$GDS" ]] && EXTRA_MOUNTS+=("-v" "$GDS:$GDS")
[[ "$VERILOG_GUEST" == "$VERILOG" ]] && EXTRA_MOUNTS+=("-v" "$VERILOG:$VERILOG")

echo "Running LVS for $MODULE..."
echo "  layout:  $GDS"
echo "  netlist: $VERILOG"

# Run in the orfs:latest container — KLayout 0.30+ is installed there.
# No commercial tools; image is the same one the synthesis flow uses.
set +e
sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO_ROOT:/work \
    ${EXTRA_MOUNTS[*]} \
    openroad/orfs:latest \
    klayout -b -r /work/tech/asap7/orfs/scripts/lvs.py \
      -rd gds=$GDS_GUEST \
      -rd verilog=$VERILOG_GUEST \
      -rd top=$TOP_CELL \
      -rd report=$REPORT_GUEST \
      -rd netlist_out=$NETLIST_OUT_GUEST"
RC=$?
set -e

# The last line of the report is empty; the summary is in the body.
# Print a deterministic one-line PASS/FAIL summary regardless of what
# KLayout printed during the run.
if [[ $RC -eq 0 ]]; then
    SUMMARY="LVS PASS: $MODULE"
else
    SUMMARY="LVS FAIL: $MODULE (see $REPORT_HOST)"
fi
echo
echo "===================="
echo "$SUMMARY"
echo "===================="
exit $RC
