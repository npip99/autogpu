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
# Exit codes: 0=PASS, 1=FAIL (Vdrop above budget), 2=BLOCKED (PSM-0069).
#
# Requires: docker, openroad/orfs:latest image, the same prerequisites as
# run.sh (a completed `./run.sh <module>` so 6_final.odb / .spef exist).

set -e
MODULE="${1:?usage: $0 <module> [--budget F] [--activity A]}"
shift
BUDGET_FRACTION="0.10"   # 10% of VDD by default
ACTIVITY=""              # let ir_drop.tcl pick its default (0.10) unless set
while [[ $# -gt 0 ]]; do
    case "$1" in
        --budget)   BUDGET_FRACTION="$2"; shift 2 ;;
        --activity) ACTIVITY="$2";       shift 2 ;;
        *) echo "unknown flag: $1"; exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
WORK_HOST="$REPO_ROOT/build/orfs"
WORK_GUEST="/work/build/orfs"
ODB="$WORK_HOST/results/asap7/$MODULE/base/6_final.odb"
CONFIG_FILE="$SCRIPT_DIR/${MODULE}.config.mk"

if [[ ! -f "$ODB" ]]; then
    echo "ERROR: $ODB missing. Run './run.sh $MODULE' first."
    exit 1
fi
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: $CONFIG_FILE missing."
    exit 1
fi

REPORTS_HOST="$WORK_HOST/reports/asap7/$MODULE/base"
LOG="$REPORTS_HOST/ir_drop.log"
mkdir -p "$REPORTS_HOST"

# Step 1: probe ORFS-computed env vars (TECH_LEF, LIB_FILES, …) so the
# tcl can reuse ORFS's load_design helper unmodified.
ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT
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
    | grep -v ORFS_ENV_ > "$ENV_FILE"

if ! grep -q '^DESIGN_NAME=' "$ENV_FILE"; then
    echo "ERROR: could not probe ORFS env. Output:"
    cat "$ENV_FILE"
    exit 1
fi

# Step 2: run analyze_power_grid via our ir_drop.tcl, with the env we
# just probed passed in via --env-file. Output to REPORTS_DIR.
DOCKER_OUT="$(mktemp)"
ACTIVITY_ARG=""
[[ -n "$ACTIVITY" ]] && ACTIVITY_ARG="-e IR_ACTIVITY=$ACTIVITY"

set +e
sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO_ROOT:/work \
    --env-file $ENV_FILE \
    $ACTIVITY_ARG \
    -w /OpenROAD-flow-scripts/flow \
    openroad/orfs:latest \
    /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad -exit -no_splash \
        /work/tech/asap7/orfs/scripts/ir_drop.tcl" \
    > "$DOCKER_OUT" 2>&1
ORC=$?
set -e

cp "$DOCKER_OUT" "$REPORTS_HOST/ir_drop.openroad.log"

if [[ $ORC -ne 0 ]]; then
    echo "ERROR: openroad exited $ORC. See $REPORTS_HOST/ir_drop.openroad.log"
    tail -30 "$DOCKER_OUT"
    exit $ORC
fi

# Step 3: post-process. Pull worst-case Vdrop from openroad stdout (PSM
# prints per-net "IR report" blocks) + the per-node voltage CSV (used to
# locate failing instances). asap7 nominal VDD is 0.70 V (the "typical"
# corner). ORFS's $VOLTAGE may default to BC_VOLTAGE=0.77, but for IR-drop
# sign-off we measure budget against the nominal — that's the worst case
# the rail sees during normal operation. ir_drop.tcl tells us what it used.
VDD_USED=$(grep -oP 'VDD=\K[0-9.]+' "$DOCKER_OUT" | head -1)
[[ -z "$VDD_USED" ]] && VDD_USED=0.70
ACTIVITY_USED=$(grep -oP 'activity=\K[0-9.]+' "$DOCKER_OUT" | head -1)

set +e
python3 - "$DOCKER_OUT" "$REPORTS_HOST" "$MODULE" "$VDD_USED" "$BUDGET_FRACTION" "$ACTIVITY_USED" "$LOG" <<'PY'
import os, re, sys
log_text = open(sys.argv[1]).read()
rep_dir, module, vdd, budget_frac, activity, out_log = sys.argv[2:]
vdd = float(vdd); budget_frac = float(budget_frac)
budget_v = vdd * budget_frac

def parse_net(text, net):
    # PSM "IR report" block: "Worstcase IR drop: 6.78e-03 V"
    m = re.search(
        rf"Net\s+:\s+{net}\b.*?Worstcase IR drop:\s+([-0-9.eE+]+)\s+V"
        rf".*?Percentage drop\s+:\s+([-0-9.eE+]+)\s+%",
        text, re.S)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))

def psm_status(rep_dir, log_text, net):
    # PSM only writes error_file when there are violations. A clean grid
    # leaves the file unwritten but logs "PSM-0040 All shapes on net X
    # are connected."
    p = os.path.join(rep_dir, f"{net}_error.rpt")
    if os.path.exists(p) and os.path.getsize(p) > 0:
        body = open(p).read()
        n = sum(1 for ln in body.splitlines() if "violation type" in ln)
        return f"{n} PSM violation(s)"
    if re.search(rf"PSM-0040.*net {net}\b", log_text):
        return "PSM-0040: grid connected"
    return "unknown"

def per_instance_failures(csv_path, budget_v, vdd):
    """Return list of (drop_v, instance, layer, x, y) above budget, sorted desc.

    CSV header: Instance,Terminal,Layer,X location,Y location,Voltage
    Aggregate by instance taking that instance's worst node (lowest V).
    """
    if not os.path.exists(csv_path):
        return []
    worst_per_inst = {}
    with open(csv_path) as f:
        header = f.readline()
        for line in f:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            try:
                inst   = parts[0]
                layer  = parts[2]
                x      = float(parts[3])
                y      = float(parts[4])
                v      = float(parts[5])
            except ValueError:
                continue
            drop = vdd - v
            if drop > worst_per_inst.get(inst, (0,))[0]:
                worst_per_inst[inst] = (drop, layer, x, y)
    out = [(d, n, l, x, y) for n, (d, l, x, y) in worst_per_inst.items() if d > budget_v]
    out.sort(reverse=True)
    return out

lines = []
lines.append("=" * 70)
lines.append("IR-drop sign-off report")
lines.append("=" * 70)
lines.append(f"Module       : {module}")
lines.append(f"VDD nominal  : {vdd} V (asap7 typical corner)")
lines.append(f"Budget       : {budget_frac*100:.1f}% of VDD = {budget_v*1000:.2f} mV")
lines.append(f"Activity     : {activity} (global switching-activity factor, "
             f"set_power_activity -global -activity {activity})")
lines.append(f"Tool         : OpenROAD psm (analyze_power_grid)")
lines.append("")

worst_v = 0.0
worst_pct = 0.0
overall = "PASS"
for net in ("VDD", "VSS"):
    psm = psm_status(rep_dir, log_text, net)
    res = parse_net(log_text, net)
    if res is None:
        lines.append(f"{net}: NO IR REPORT (psm: {psm})")
        overall = "BLOCKED"
        continue
    drop_v, drop_pct = res
    worst_v = max(worst_v, drop_v)
    worst_pct = max(worst_pct, drop_pct)
    status = "PASS" if drop_v <= budget_v else "FAIL"
    if status == "FAIL":
        overall = "FAIL"
    # PSM violations on either net = grid problem; downgrade to BLOCKED.
    if "violation" in psm:
        overall = "BLOCKED"
    lines.append(f"{net}: worst Vdrop = {drop_v*1000:.3f} mV "
                 f"({drop_pct:.2f}%)   psm: {psm}   {status}")

lines.append("")
lines.append(f"WORST Vdrop  : {worst_v*1000:.3f} mV  ({worst_pct:.2f}%)")
lines.append(f"OVERALL      : {overall}")
lines.append("")

# Per-instance failure list (acceptance criterion 3). Aggregate per
# instance from VDD_voltage.csv so PDN reinforcement can target by name.
if overall == "FAIL":
    fails = per_instance_failures(
        os.path.join(rep_dir, "VDD_voltage.csv"), budget_v, vdd)
    lines.append(f"Failing instances ({len(fails)}); worst 10 by Vdrop:")
    for drop, inst, layer, x, y in fails[:10]:
        lines.append(f"  Vdrop={drop*1000:.2f}mV  {inst}  "
                     f"({x:.2f}, {y:.2f}) on {layer}")
    lines.append("")
elif overall == "BLOCKED":
    lines.append("BLOCKED — grid connectivity errors (likely PSM-0069). "
                 "Fix PDN (see A1) before IR-drop sign-off can complete.")
    lines.append("")

# Machine-readable summary line — last line for easy grep.
lines.append(f"SUMMARY: module={module} worst_vdrop={worst_v*1000:.3f}mV "
             f"worst_pct={worst_pct:.3f}% budget_mV={budget_v*1000:.2f} "
             f"activity={activity} status={overall}")

open(out_log, "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
# Exit 0=PASS, 1=FAIL (real Vdrop over budget), 2=BLOCKED (grid broken).
sys.exit({"PASS": 0, "FAIL": 1, "BLOCKED": 2}[overall])
PY
PY_RC=$?

echo ""
echo "Report: $LOG"
exit $PY_RC
