#!/bin/bash
# density_check.sh — metal-density early-warning check for an asap7
# hardened module.
#
# Usage: ./density_check.sh <module> [--bands <path>]
#
# Walks 6_final.gds with KLayout, computes global density per metal
# layer (M1..M9), and compares against documented bands (default in
# scripts/density_check.py; override with --bands <tsv>).
#
# Early-warning, not sign-off. Global density is conservative — a real
# foundry sign-off uses windowed density (20-50 µm windows). If any
# layer is OUT of band globally, the windowed result will be worse.
# This gives us the "is the architecture in the right ballpark"
# signal NOW so the PDK swap doesn't trigger re-floorplan.
#
# Outputs:
#   build/orfs/reports/asap7/<module>/base/density.log
#
# Exit codes:
#   0 — within bands (or untouched)
#   1 — at least one layer OVER max  → re-floorplan needed
#   2 — config / artifact error
#   3 — at least one layer UNDER min → needs dummy-metal fill (see
#       PDK_GAPS.md; fill methodology not implemented yet)

set -e
MODULE="${1:?usage: $0 <module> [--bands <path>]}"
shift
BANDS_HOST=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --bands) BANDS_HOST="$2"; shift 2 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

GDS="$REPO_ROOT/build/orfs/results/asap7/$MODULE/base/6_final.gds"
if [[ ! -f "$GDS" ]]; then
    echo "ERROR: $GDS missing. Run './tech/asap7/orfs/run.sh $MODULE' first." >&2
    exit 2
fi

# Determine actual top cell name. ORFS uses DESIGN_NAME (not DESIGN_NICKNAME)
# as the GDS top, so for variants like compute_array_tiny_bcast0 (nickname)
# the actual top is compute_array (DESIGN_NAME).
TOP_CELL="$MODULE"
CFG="$SCRIPT_DIR/${MODULE}.config.mk"
if [[ -f "$CFG" ]]; then
    cfg_top=$(grep -E '^export\s+DESIGN_NAME\s*=' "$CFG" | head -1 \
              | sed -E 's/^export\s+DESIGN_NAME\s*=\s*([^[:space:]]+).*/\1/')
    [[ -n "$cfg_top" ]] && TOP_CELL="$cfg_top"
fi

REPORT_HOST="$REPO_ROOT/build/orfs/reports/asap7/$MODULE/base/density.log"
REPORT_GUEST="/work/build/orfs/reports/asap7/$MODULE/base/density.log"
GDS_GUEST="/work/${GDS#$REPO_ROOT/}"
mkdir -p "$(dirname "$REPORT_HOST")"

BANDS_ARG=""
if [[ -n "$BANDS_HOST" ]]; then
    if [[ ! -f "$BANDS_HOST" ]]; then
        echo "ERROR: bands file $BANDS_HOST missing" >&2
        exit 2
    fi
    BANDS_ARG="-rd bands=/work/${BANDS_HOST#$REPO_ROOT/}"
fi

set +e
sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO_ROOT:/work \
    openroad/orfs:latest \
    klayout -b -r /work/tech/asap7/orfs/scripts/density_check.py \
      -rd gds=$GDS_GUEST \
      -rd top=$TOP_CELL \
      -rd report=$REPORT_GUEST \
      $BANDS_ARG"
RC=$?
set -e

exit $RC
