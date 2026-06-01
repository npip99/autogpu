#!/bin/bash
# DRC (Design Rule Check) driver for an asap7 hardened block.
#
# Usage: ./drc.sh <module> [--gds path]
#   ./drc.sh cmd_unit
#   ./drc.sh compute_array_tiny_bcast0
#   ./drc.sh mac_tmem_cell --gds /path/to/other.gds
#
# Runs the asap7 KLayout DRC deck
# (/OpenROAD-flow-scripts/flow/platforms/asap7/drc/asap7.lydrc, shipped in
# the orfs:latest image) against the post-route GDS and postprocesses the
# KLayout report database (.lyrdb) into a one-line SUMMARY of violation
# counts per rule category. Mirrors the ORFS `make drc` invocation
# (klayout -rd in_gds=… -rd report_file=… -r asap7.lydrc) so the numbers
# match what the flow itself would report, but is re-runnable standalone
# against any existing 6_final.gds without redoing the flow.
#
# This fills the DRC slot in the sign-off matrix alongside ir_drop.sh,
# lvs.sh, antenna_check.sh, and density_check.sh; report path + exit-code
# contract follow the same convention. See tech/asap7/DESIGN.md.
#
# Exit codes:
#   0 = CLEAN              (zero violations)
#   1 = violations present (per-category counts in the log + .lyrdb)
#   2 = vacuous            (no DRC deck available for the platform)
#   3 = tool/env failure   (klayout crash, missing GDS, no report produced)
#
# Reports (under build/orfs/reports/asap7/<module>/base/):
#   drc.lyrdb  — raw KLayout report database (open in klayout to inspect markers)
#   drc.log    — full run log, ending in a one-line `SUMMARY:` for grepping
set -e

MODULE="${1:?usage: $0 <module> [--gds path]}"
shift || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
RESULTS_HOST="$REPO_ROOT/build/orfs/results/asap7/$MODULE/base"

GDS="$RESULTS_HOST/6_final.gds"
while [[ $# -gt 0 ]]; do
    case $1 in
        --gds) GDS="$2"; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 3 ;;
    esac
done

if [[ ! -f "$GDS" ]]; then
    echo "ERROR: $GDS missing. Run './tech/asap7/orfs/run.sh $MODULE' first." >&2
    exit 3
fi

# asap7 DRC deck lives inside the image at a fixed path.
DRC_DECK_GUEST="/OpenROAD-flow-scripts/flow/platforms/asap7/drc/asap7.lydrc"

REPORT_DIR_HOST="$REPO_ROOT/build/orfs/reports/asap7/$MODULE/base"
REPORT_DIR_GUEST="/work/build/orfs/reports/asap7/$MODULE/base"
RDB_HOST="$REPORT_DIR_HOST/drc.lyrdb"
RDB_GUEST="$REPORT_DIR_GUEST/drc.lyrdb"
LOG_HOST="$REPORT_DIR_HOST/drc.log"
mkdir -p "$REPORT_DIR_HOST"
rm -f "$RDB_HOST"

# Translate a host path under $REPO_ROOT into the in-container /work prefix;
# mount it directly otherwise (e.g. a --gds outside the repo).
to_guest() {
    local p="$1"
    case "$p" in
        "$REPO_ROOT"/*) echo "/work/${p#$REPO_ROOT/}" ;;
        *)              echo "$p" ;;
    esac
}
GDS_GUEST="$(to_guest "$GDS")"
EXTRA_MOUNTS=()
[[ "$GDS_GUEST" == "$GDS" ]] && EXTRA_MOUNTS+=("-v" "$GDS:$GDS")

echo "Running DRC for $MODULE..."
echo "  layout: $GDS"
echo "  deck:   asap7.lydrc"

# -b: batch (no GUI), exit when done. Same deck + -rd contract the ORFS
# `make drc` rule uses, run directly (klayout.sh is just a passthrough).
set +e
sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO_ROOT:/work \
    ${EXTRA_MOUNTS[*]} \
    openroad/orfs:latest \
    bash -c '[[ -f $DRC_DECK_GUEST ]] || exit 42; \
      klayout -b -rd in_gds=$GDS_GUEST -rd report_file=$RDB_GUEST -r $DRC_DECK_GUEST'" \
    > "$LOG_HOST" 2>&1
RC=$?
set -e
cat "$LOG_HOST"

if [[ $RC -eq 42 ]]; then
    echo "SUMMARY: VACUOUS — no asap7 DRC deck in image ($DRC_DECK_GUEST)" | tee -a "$LOG_HOST"
    exit 2
fi
if [[ $RC -ne 0 || ! -f "$RDB_HOST" ]]; then
    echo "SUMMARY: TOOL/ENV FAILURE — klayout rc=$RC, no report produced" | tee -a "$LOG_HOST"
    exit 3
fi

# Postprocess the .lyrdb (KLayout report database, XML) into per-category
# counts. Each <item> is one violation marker tagged with its rule <category>.
SUMMARY=$(python3 - "$RDB_HOST" "$MODULE" <<'PY'
import sys, xml.etree.ElementTree as ET
rdb, module = sys.argv[1], sys.argv[2]
try:
    root = ET.parse(rdb).getroot()
except Exception as e:
    print(f"SUMMARY: PARSE ERROR on {rdb}: {e}"); sys.exit(0)
# Tally violation markers by rule category. Each <item> is one marker; its
# <category> text is the rule name, emitted quoted (e.g. 'M4.S.5'). A few
# deck rules have a blank name; bucket those under "(unnamed-rule)".
from collections import Counter
counts = Counter()
for item in root.iter("item"):
    cat = (item.findtext("category") or "").strip().strip("'").strip()
    counts[cat or "(unnamed-rule)"] += 1
total = sum(counts.values())
if total == 0:
    print(f"SUMMARY: CLEAN — {module}: 0 DRC violations")
else:
    detail = ", ".join(f"{c}={n}" for c, n in counts.most_common())
    print(f"SUMMARY: {total} DRC violations — {module}: {detail}")
PY
)
echo "$SUMMARY" | tee -a "$LOG_HOST"

echo
echo "===================="
echo "$SUMMARY"
echo "  report: $RDB_HOST"
echo "===================="

# 0 if the SUMMARY says CLEAN, else 1 (violations present).
[[ "$SUMMARY" == *"CLEAN"* ]] && exit 0 || exit 1
