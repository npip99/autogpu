#!/bin/bash
# ir_drop.sh — static IR-drop sign-off for one ORFS-hardened submodule.
#
# Usage: ./ir_drop.sh <module_name> [--budget <fraction>] [--activity <0..1>]
#
# Runs OpenROAD's psm (analyze_power_grid) on
#   build/orfs/results/asap7/<module>/base/6_final.odb
# with a documented switching-activity factor, and reports:
#   - worst-case absolute Vdrop (volts)
#   - worst-case % drop vs nominal VDD
#   - pass/fail vs --budget (default 10% of VDD)
#   - for failing instances: location (x, y) so the PDN can be reinforced
#
# Outputs (under build/orfs/reports/asap7/<module>/base/):
#   ir_drop.log          — human + machine-readable summary (`SUMMARY: …`)
#   ir_drop.openroad.log — raw openroad stdout (report_power + psm)
#   VDD_voltage.csv      — per-node voltages (Instance,Terminal,Layer,X,Y,V)
#   VDD_error.rpt        — PSM-0069 violations (only when grid is broken)
#   (same _voltage.csv / _error.rpt for VSS)
#
# Exit codes:
#   0 = PASS
#   1 = FAIL  (Vdrop above budget)
#   2 = BLOCKED (PSM-0069 grid disconnection — fix PDN first)
#   3 = tool / env failure (openroad crashed, docker probe failed, bad args)
#
# Requires: docker, openroad/orfs:latest image, the same prerequisites as
# run.sh (a completed `./run.sh <module>` so 6_final.odb / .spef exist).

usage() {
    cat <<EOF
Usage: $0 <module_name> [--budget <fraction>] [--activity <0..1>]

Runs static IR-drop sign-off via OpenROAD psm on a completed ORFS module.

Flags:
  --budget <fraction>   pass/fail threshold as a fraction of VDD (default 0.10)
  --activity <0..1>     switching activity factor (default 0.10)
  -h, --help            show this help and exit

Exit: 0=PASS, 1=FAIL, 2=BLOCKED (grid PDN bug), 3=tool/env failure.
EOF
}

set -e
# Run a command with `set -e` temporarily disabled, then restore it and
# return the command's exit code. Use for steps whose non-zero exit is a
# meaningful outcome (FAIL=1, BLOCKED=2 from the postprocessor; openroad
# errors we report ourselves) rather than a reason to abort.
run_unchecked() {
    local rc
    set +e
    "$@"
    rc=$?
    set -e
    return $rc
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    "") usage >&2; exit 3 ;;
esac
MODULE="$1"
shift
BUDGET_FRACTION="0.10"   # 10% of VDD by default
ACTIVITY=""              # let ir_drop.tcl pick its default (0.10) unless set
is_float_0_to_1() {
    # accept e.g. 0.05, 0.1, 1, 1.0 — reject empty / non-numeric / >1
    [[ "$1" =~ ^(0(\.[0-9]+)?|1(\.0+)?)$ ]]
}
while [[ $# -gt 0 ]]; do
    case "$1" in
        --budget)
            [[ $# -ge 2 ]] || { echo "ERROR: --budget needs a value" >&2; exit 3; }
            is_float_0_to_1 "$2" || { echo "ERROR: --budget '$2' must be in (0, 1]" >&2; exit 3; }
            BUDGET_FRACTION="$2"; shift 2 ;;
        --activity)
            [[ $# -ge 2 ]] || { echo "ERROR: --activity needs a value" >&2; exit 3; }
            is_float_0_to_1 "$2" || { echo "ERROR: --activity '$2' must be in [0, 1]" >&2; exit 3; }
            ACTIVITY="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown flag: $1" >&2; usage >&2; exit 3 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
WORK_HOST="$REPO_ROOT/build/orfs"
WORK_GUEST="/work/build/orfs"
ODB="$WORK_HOST/results/asap7/$MODULE/base/6_final.odb"
CONFIG_FILE="$SCRIPT_DIR/${MODULE}.config.mk"

if [[ ! -f "$ODB" ]]; then
    echo "ERROR: $ODB missing. Run './run.sh $MODULE' first." >&2
    exit 3
fi
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: $CONFIG_FILE missing." >&2
    exit 3
fi

REPORTS_HOST="$WORK_HOST/reports/asap7/$MODULE/base"
LOG="$REPORTS_HOST/ir_drop.log"
mkdir -p "$REPORTS_HOST"

# Step 1: probe ORFS-computed env vars (TECH_LEF, LIB_FILES, …) so the
# tcl can reuse ORFS's load_design helper unmodified.
ENV_FILE="$(mktemp)"
DOCKER_OUT="$(mktemp)"
trap 'rm -f "$ENV_FILE" "$DOCKER_OUT"' EXIT
sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO_ROOT:/work \
    -e FLOW_HOME=/OpenROAD-flow-scripts/flow \
    -e WORK_HOME=$WORK_GUEST \
    -w /OpenROAD-flow-scripts/flow \
    openroad/orfs:latest \
    make -s -f /work/tech/asap7/orfs/scripts/_ir_drop_env.mk \
        DESIGN_CONFIG=/work/tech/asap7/orfs/${MODULE}.config.mk \
        print-env" \
    | sed -n '/ORFS_ENV_BEGIN/,/ORFS_ENV_END/p' \
    | grep -v '^ORFS_ENV_' > "$ENV_FILE"

if ! grep -q '^DESIGN_NAME=' "$ENV_FILE"; then
    echo "ERROR: could not probe ORFS env. Output:" >&2
    cat "$ENV_FILE" >&2
    exit 3
fi

# Step 2: run analyze_power_grid via our ir_drop.tcl, with the env we
# just probed passed in via --env-file. Output to REPORTS_DIR.
ACTIVITY_ARG=""
[[ -n "$ACTIVITY" ]] && ACTIVITY_ARG="-e IR_ACTIVITY=$ACTIVITY"

run_unchecked sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO_ROOT:/work \
    --env-file $ENV_FILE \
    $ACTIVITY_ARG \
    -w /OpenROAD-flow-scripts/flow \
    openroad/orfs:latest \
    /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad -exit -no_splash \
        /work/tech/asap7/orfs/scripts/ir_drop.tcl" \
    > "$DOCKER_OUT" 2>&1
ORC=$?

cp "$DOCKER_OUT" "$REPORTS_HOST/ir_drop.openroad.log"

if [[ $ORC -ne 0 ]]; then
    echo "ERROR: openroad exited $ORC. See $REPORTS_HOST/ir_drop.openroad.log" >&2
    tail -30 "$DOCKER_OUT" >&2
    # Don't propagate $ORC — it could be 1 or 2, colliding with our
    # FAIL/BLOCKED contract. Tool failures get exit 3.
    exit 3
fi

# Step 3: post-process. Pull VDD + activity from the tcl's stdout
# (it prints them explicitly so they're traceable in reports).
VDD_USED=$(grep -oP 'VDD=\K[0-9.]+' "$DOCKER_OUT" | head -1)
[[ -z "$VDD_USED" ]] && VDD_USED=0.70
ACTIVITY_USED=$(grep -oP 'activity=\K[0-9.]+' "$DOCKER_OUT" | head -1)

# Run the extracted postprocessor (see scripts/ir_drop_postprocess.py +
# scripts/tests/test_ir_drop_postprocess.py). It computes worst Vdrop
# from the per-node voltage CSVs (authoritative) and detects grid
# disconnection from the PSM error files. Exit codes 0/1/2 map directly
# to PASS / FAIL / BLOCKED.
#
# PASS=0 is fine under set -e, but FAIL=1 and BLOCKED=2 are legitimate
# outcomes that must not abort the shell before we print the report path.
run_unchecked python3 "$SCRIPT_DIR/scripts/ir_drop_postprocess.py" \
    "$DOCKER_OUT" "$REPORTS_HOST" "$MODULE" \
    "$VDD_USED" "$BUDGET_FRACTION" "$ACTIVITY_USED" "$LOG"
PY_RC=$?

echo ""
echo "Report: $LOG"
exit $PY_RC
