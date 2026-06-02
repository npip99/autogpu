#!/usr/bin/env bash
# diagnose_grt.sh — fast evidence-based diagnosis of stuck/slow GRT.
#
# Usage:
#     tech/asap7/orfs/scripts/diagnose_grt.sh <design_name>
#
# Reads ORFS-emitted artifacts ONLY — no source code, no debug symbols,
# no need to attach to / kill the running build. Walks the chain:
#
#   1. Convergence trend across congestion-N.rpt files
#   2. Persistent stuck nets at the latest congestion report
#   3. Hot bbox locations (where on the die)
#   4. Translate net IDs → synth output → resizer report
#
# Stops at the first definitive answer.
#
# Validated method, see tech/RCA_DISCIPLINE.md § "Stage-specific shortcuts".

set -e
DESIGN="${1:?usage: $0 <design_name>}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
REPORTS="$REPO/build/orfs/reports/asap7/$DESIGN/base"
RESULTS="$REPO/build/orfs/results/asap7/$DESIGN/base"

if [[ ! -d "$REPORTS" ]]; then
    echo "ERROR: no reports dir: $REPORTS"
    echo "is design name right? did the build reach GRT yet?"
    exit 1
fi

cd "$REPO"

echo "===================================================================="
echo " GRT diagnosis for $DESIGN"
echo "===================================================================="
echo

# 1) Convergence trend ------------------------------------------------------
echo "----- 1) Convergence trend -----"
CONG_RPTS=$(ls "$REPORTS"/congestion-*.rpt 2>/dev/null)
if [[ -z "$CONG_RPTS" ]]; then
    echo "no congestion reports yet — GRT hasn't hit -congestion_report_iter_step"
    echo "  (default step is 5, so iter 5 is first report)"
    exit 0
fi
echo "(line count proxies violation count — descending = converging)"
wc -l $CONG_RPTS
echo

# 2) Persistent nets at latest report ---------------------------------------
LATEST=$(ls "$REPORTS"/congestion-*.rpt | sort -V | tail -1)
echo "----- 2) Persistent stuck nets at $(basename $LATEST) -----"
NETS=$(grep -oE "net:net[0-9]+" "$LATEST" | sort -u | sed 's/net://')
N_NETS=$(echo "$NETS" | wc -l)
echo "$N_NETS unique nets persistently overflowed at this iter:"
echo "$NETS" | head -20 | tr '\n' ' '
echo
[[ $N_NETS -gt 20 ]] && echo "  ...($((N_NETS-20)) more, truncated)"
echo

# 3) Hot bbox locations -----------------------------------------------------
echo "----- 3) Hot bbox locations (geometric hotspots) -----"
echo "(repeated bboxes show where overflowed g-cells cluster)"
grep "bbox" "$LATEST" | sort | uniq -c | sort -rn | head -10
echo

# 4) Translate net IDs → source --------------------------------------------
echo "----- 4) Resolve net IDs to design signals -----"
SYNTH="$RESULTS/1_2_yosys.v"
if [[ ! -f "$SYNTH" ]]; then
    echo "no synth output yet at $SYNTH — skip"
else
    FOUND_IN_SYNTH=0
    NOT_IN_SYNTH=0
    for n in $NETS; do
        if grep -qE "\b$n\b" "$SYNTH" 2>/dev/null; then
            FOUND_IN_SYNTH=$((FOUND_IN_SYNTH + 1))
        else
            NOT_IN_SYNTH=$((NOT_IN_SYNTH + 1))
        fi
    done
    echo "of $N_NETS stuck nets:"
    echo "  $FOUND_IN_SYNTH found in 1_2_yosys.v (= original RTL signal — look up by name)"
    echo "  $NOT_IN_SYNTH not in synth output (= resizer-inserted buffer net)"
    echo
    if [[ $NOT_IN_SYNTH -gt 0 ]]; then
        echo ">>>>> Stuck nets are RESIZER-INSERTED — check 3_resizer.rpt critical path:"
        RESIZER_RPT="$REPORTS/3_resizer.rpt"
        if [[ -f "$RESIZER_RPT" ]]; then
            echo "(top of resizer critical path — the RTL signal this buffer chain serves)"
            grep -A 5 "Startpoint:\|Endpoint:" "$RESIZER_RPT" | head -30
        else
            echo "  3_resizer.rpt not found — resizer didn't run yet?"
        fi
    fi
fi

echo
echo "===================================================================="
echo " Hypothesis & next steps"
echo "===================================================================="
echo
echo "Walk the chain above to form ONE testable hypothesis. Examples:"
echo "  - Stuck nets are RTL signals X, Y → cap fanout / split RTL"
echo "  - Stuck nets are resizer buffers on path A → B → reduce wire length"
echo "    by moving B (or its source) closer to A's home"
echo "  - Hot bbox is a specific edge/corner → reduce demand there"
echo "    (e.g., re-route some pins to a different edge via IO_CONSTRAINTS)"
echo
echo "Always cite the evidence file when proposing the fix. See"
echo "tech/RCA_DISCIPLINE.md for the full process."
