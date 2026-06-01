#!/bin/bash
# DRC (Design Rule Check) driver for an asap7 hardened block.
#
# Usage: ./drc.sh <module> [--gds path]
#   ./drc.sh cmd_unit
#   ./drc.sh compute_array_tiny_bcast0
#   ./drc.sh mac_tmem_cell --gds /path/to/other.gds
#
# Runs the asap7 KLayout DRC deck (asap7.lydrc, shipped in the orfs image)
# UNMODIFIED against the post-route GDS — the deck stays authoritative, never
# mutated — and classifies each violation against a reviewable WAIVER LIST
# (drc_waivers.tsv): known asap7-KLayout-port artifacts and checks the OpenROAD
# router can't satisfy (deferred to the authoritative Calibre deck). Re-runnable
# standalone against any existing 6_final.gds without redoing the flow.
#
# GATE SEMANTICS: drc.sh fails only on *unwaived* violations. So exit 0 means
# "no new/unexplained DRC" (usable in CI), and every suppression is auditable
# in drc_waivers.tsv with a one-line justification (vs silently sed-ing the
# deck). See tech/asap7/DESIGN.md and issue #39 for why the waivers exist.
#
# This fills the DRC slot in the sign-off matrix alongside ir_drop.sh, lvs.sh,
# antenna_check.sh, density_check.sh; report path + exit-code contract follow
# the same convention.
#
# Exit codes:
#   0 = CLEAN              (zero UNWAIVED violations; waived ones are reported)
#   1 = unwaived violations present (per-rule counts in the log + .lyrdb)
#   2 = vacuous            (no DRC deck available for the platform)
#   3 = tool/env failure   (klayout crash, missing GDS, no report produced)
#
# Reports (under build/orfs/reports/asap7/<module>/base/):
#   drc.lyrdb  — raw KLayout report database (UNMODIFIED deck; all markers)
#   drc.log    — full run log, ending in a one-line `SUMMARY:` for grepping
set -e

MODULE="${1:?usage: $0 <module> [--gds path]}"
shift || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$SCRIPT_DIR/orfs_image.sh"   # pinned ORFS image (was :latest)
RESULTS_HOST="$REPO_ROOT/build/orfs/results/asap7/$MODULE/base"
WAIVERS="$SCRIPT_DIR/drc_waivers.tsv"

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

# Shipped deck (authoritative, run unmodified). Lives inside the image.
DRC_DECK_SRC="/OpenROAD-flow-scripts/flow/platforms/asap7/drc/asap7.lydrc"

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
echo "  deck:   asap7.lydrc (unmodified)"

# -b: batch (no GUI). Same deck + -rd contract the ORFS `make drc` rule uses.
set +e
sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO_ROOT:/work \
    ${EXTRA_MOUNTS[*]} \
    -e SRC=$DRC_DECK_SRC -e GDS=$GDS_GUEST -e RDB=$RDB_GUEST \
    ${ORFS_IMAGE} \
    bash -c '[ -f \"\$SRC\" ] || exit 42; klayout -b -rd in_gds=\"\$GDS\" -rd report_file=\"\$RDB\" -r \"\$SRC\"'" \
    > "$LOG_HOST" 2>&1
RC=$?
set -e
cat "$LOG_HOST"

if [[ $RC -eq 42 ]]; then
    echo "SUMMARY: VACUOUS — no asap7 DRC deck in image ($DRC_DECK_SRC)" | tee -a "$LOG_HOST"
    exit 2
fi
if [[ $RC -ne 0 || ! -f "$RDB_HOST" ]]; then
    echo "SUMMARY: TOOL/ENV FAILURE — klayout rc=$RC, no report produced" | tee -a "$LOG_HOST"
    exit 3
fi

# Postprocess: classify each .lyrdb marker against the waiver list. Each <item>
# is one marker; its <category> is the rule (the deck quotes it, e.g. 'M4.S.5',
# and leaves M1.S.2's name blank — resolve blanks from the rule description).
SUMMARY=$(python3 - "$RDB_HOST" "$WAIVERS" "$MODULE" <<'PY'
import sys, xml.etree.ElementTree as ET
from collections import Counter
rdb, waiver_file, module = sys.argv[1], sys.argv[2], sys.argv[3]

def rule_id(name, desc):
    name = (name or "").strip()
    if name:
        return name
    # blank category name (deck bug, e.g. M1.S.2) → take the id from the desc
    return ((desc or "").strip().split(":", 1)[0].strip()) or "(unnamed)"

try:
    root = ET.parse(rdb).getroot()
except Exception as e:
    print(f"SUMMARY: PARSE ERROR on {rdb}: {e}"); sys.exit(0)

# category <name> (as referenced by items) → resolved rule id
catmap = {}
for c in root.iter("category"):
    nm = c.findtext("name")
    if nm is None:          # an item's <category> reference, not a definition
        continue
    catmap[nm] = rule_id(nm, c.findtext("description"))

counts = Counter()
for item in root.iter("item"):
    raw = (item.findtext("category") or "").strip().strip("'")
    rid = catmap.get(raw) or rule_id(raw, "")
    counts[rid] += 1

waived = {}
try:
    for ln in open(waiver_file):
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        rid = ln.split("\t", 1)[0].strip()
        if rid:
            waived[rid] = True
except FileNotFoundError:
    pass

total = sum(counts.values())
unw = {r: n for r, n in counts.items() if r not in waived}
wv  = {r: n for r, n in counts.items() if r in waived}
nu, nw = sum(unw.values()), sum(wv.values())

def fmt(d):
    return ", ".join(f"{r}={n}" for r, n in Counter(d).most_common())

if total == 0:
    print(f"SUMMARY: CLEAN — {module}: 0 DRC violations")
elif nu == 0:
    print(f"SUMMARY: CLEAN — {module}: 0 unwaived ({nw} waived: {fmt(wv)})")
else:
    print(f"SUMMARY: {module}: {nu} UNWAIVED ({fmt(unw)}) | {nw} waived ({fmt(wv)})")
PY
)
echo "$SUMMARY" | tee -a "$LOG_HOST"

echo
echo "===================="
echo "$SUMMARY"
echo "  report: $RDB_HOST   waivers: $WAIVERS"
echo "===================="

# 0 iff no UNWAIVED violations (SUMMARY says CLEAN), else 1.
[[ "$SUMMARY" == *"CLEAN"* ]] && exit 0 || exit 1
