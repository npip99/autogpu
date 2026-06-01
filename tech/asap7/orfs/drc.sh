#!/bin/bash
# DRC (Design Rule Check) driver for an asap7 hardened block.
#
# Usage: ./drc.sh <module> [--gds path]
#   ./drc.sh cmd_unit
#   ./drc.sh compute_array_tiny_bcast0
#   ./drc.sh mac_tmem_cell --gds /path/to/other.gds
#
# Runs the asap7 KLayout DRC deck (asap7.lydrc, shipped in the orfs:latest
# image) against the post-route GDS and postprocesses the KLayout report
# database (.lyrdb) into a one-line SUMMARY of violation counts per rule
# category. Re-runnable standalone against any existing 6_final.gds without
# redoing the flow.
#
# DECK CORRECTION (#39): the shipped deck (laurentc2's community KLayout port)
# encodes M4.S.5/M5.S.5 as a 25nm spacing check, but the official ASAP7 DRM
# nominal M4/M5 spacing is 24nm (rules M4.S.1/M5.S.1). The 1nm-high value
# false-flags the DRM-compliant 24nm routing grid on every short edge. Rather
# than fork the 561-line deck (which would drift from the unpinned :latest
# image), we COPY the image's current deck at runtime and patch only those two
# lines 25nm->24nm — so we always track upstream and override the minimum. If
# the patched pattern is absent (deck changed/fixed upstream), drc.sh warns
# instead of silently mis-patching.
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

# Image's shipped deck (source of truth, tracks :latest); patched copy lives
# under the report dir (gitignored build artifact, not a committed fork).
DRC_DECK_SRC="/OpenROAD-flow-scripts/flow/platforms/asap7/drc/asap7.lydrc"

REPORT_DIR_HOST="$REPO_ROOT/build/orfs/reports/asap7/$MODULE/base"
REPORT_DIR_GUEST="/work/build/orfs/reports/asap7/$MODULE/base"
RDB_HOST="$REPORT_DIR_HOST/drc.lyrdb"
RDB_GUEST="$REPORT_DIR_GUEST/drc.lyrdb"
DECK_GUEST="$REPORT_DIR_GUEST/asap7.patched.lydrc"
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

# Inside the container: verify the deck exists, copy it to a writable patched
# copy, correct M4.S.5/M5.S.5 spacing 25nm->24nm (#39 — warn if the pattern is
# gone upstream), then run KLayout. -b: batch (no GUI). Same -rd contract as
# the ORFS `make drc` rule (klayout.sh is just a passthrough).
PATCH='
  [ -f "$SRC" ] || exit 42
  cp "$SRC" "$DECK"
  for L in m4 m5; do
    if grep -q "${L}.space(25.nm" "$DECK"; then
      sed -i "s/${L}.space(25.nm, projection)/${L}.space(24.nm, projection)/g" "$DECK"
    else
      echo "WARN[#39]: ${L}.space(25.nm) not found in upstream deck — the deck changed; re-verify the M4.S.5/M5.S.5 24nm correction is still needed/correct." >&2
    fi
  done
  klayout -b -rd in_gds="$GDS" -rd report_file="$RDB" -r "$DECK"'
set +e
sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO_ROOT:/work \
    ${EXTRA_MOUNTS[*]} \
    -e SRC=$DRC_DECK_SRC -e DECK=$DECK_GUEST \
    -e GDS=$GDS_GUEST -e RDB=$RDB_GUEST \
    openroad/orfs:latest \
    bash -c '$PATCH'" \
    > "$LOG_HOST" 2>&1
RC=$?
set -e
cat "$LOG_HOST"
grep -q "WARN\[#39\]" "$LOG_HOST" && echo "  ⚠ deck-correction drift — see WARN[#39] above"

if [[ $RC -eq 42 ]]; then
    echo "SUMMARY: VACUOUS — no asap7 DRC deck in image ($DRC_DECK_SRC)" | tee -a "$LOG_HOST"
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
