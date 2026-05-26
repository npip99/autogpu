#!/bin/bash
# antenna_check.sh — antenna sign-off for an ORFS-hardened asap7 module.
#
# Usage: ./tech/asap7/orfs/antenna_check.sh [--with-overlay] <module_name>
#   (or) ANTENNA_OVERLAY=1 ./tech/asap7/orfs/antenna_check.sh <module_name>
#
# Reads the post-route ODB produced by ./run.sh <module_name> and invokes
# OpenROAD's `check_antennas` to count antenna-rule violations. The check
# is purely a sign-off pass — it does not modify the design. (The actual
# repair_antennas pass is already integrated into the ORFS route step;
# see `scripts/global_route.tcl` and `scripts/detail_route.tcl` inside
# the openroad/orfs:latest image.)
#
# DEFAULT (no flag, no env var): runs against the stock asap7 platform
# LEFs. They ship with ZERO antenna rules, so the check returns "0
# violations" vacuously and this script exits 4. This is the honest
# tape-out posture — see PDK_GAPS.md.
#
# OPT-IN --with-overlay / ANTENNA_OVERLAY=1:
#   tech/asap7/orfs/asap7_antenna_overlay.lef — canonical source of the
#     PREDICTIVE per-layer antenna ratios derived from public asap7-paper
#     geometry. Antenna_check.tcl parses this LEF and attaches the rules
#     to the in-memory tech layers AFTER read_db (LEF re-reads alone
#     don't propagate antenna properties — see antenna_check.tcl's
#     OVERLAY MODE comment).
#   build/asap7_stdcell_with_antenna.lef — offline inspectable copy of
#     the stdcell LEF with ANTENNAGATEAREA injected on every input pin,
#     auto-regenerated via scripts/inject_antenna_gate_area.py. Not
#     consumed at check time (the TCL applies the same gate-area value
#     in-memory) — kept so users can diff/audit the predictive numbers
#     against the stock LEF.
# In this mode the check produces a real (non-vacuous) result, exit 0 or
# 2. Treat any "FAIL" out of overlay mode as "investigate", NOT as a
# tape-out gate — predictive numbers don't bind a foundry.
#
# Outputs:
#   build/orfs/reports/asap7/<module>/antenna.log — full report
#   stdout — single-line summary
#
# Exit codes:
#   0 — antenna clean (zero violations remain after repair_antennas)
#   1 — usage error / required artifact missing
#   2 — antenna violations remain in the routed design
#   3 — OpenROAD invocation failed
#   4 — PDK is missing antenna rules (asap7 platform-level gap; see
#       tech/asap7/PDK_GAPS.md). Exiting nonzero by design — a vacuous
#       "zero violations" with zero rules is NOT a tape-out sign-off.

set -e

# Parse optional --with-overlay flag (or ANTENNA_OVERLAY=1 env var).
WITH_OVERLAY="${ANTENNA_OVERLAY:-0}"
if [[ "${1:-}" == "--with-overlay" ]]; then
    WITH_OVERLAY=1
    shift
fi
MODULE="${1:?usage: $0 [--with-overlay] <module_name>}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
CONFIG_FILE="$SCRIPT_DIR/${MODULE}.config.mk"
RESULTS_HOST="$REPO_ROOT/build/orfs/results/asap7/$MODULE/base"
REPORT_DIR_HOST="$REPO_ROOT/build/orfs/reports/asap7/$MODULE"
REPORT_HOST="$REPORT_DIR_HOST/antenna.log"
ODB_HOST="$RESULTS_HOST/6_final.odb"

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "antenna_check: ERROR: $CONFIG_FILE missing" >&2
    exit 1
fi
if [[ ! -f "$ODB_HOST" ]]; then
    echo "antenna_check: ERROR: $ODB_HOST missing — run ./tech/asap7/orfs/run.sh $MODULE first" >&2
    exit 1
fi

mkdir -p "$REPORT_DIR_HOST"

# Extract ADDITIONAL_LEFS from the per-module config.mk via make. The
# config.mk uses make $(VAR) syntax so we can't just source it from bash.
# Print-target trick: include the config, then echo the variable.
ADDITIONAL_LEFS_RAW=$(make -s -f - <<EOF 2>/dev/null
include $CONFIG_FILE
print:
	@printf '%s\n' '\$(ADDITIONAL_LEFS)'
EOF
)
# ADDITIONAL_LEFS in the config is expressed in *guest* paths (/work/...).
# That's exactly what we need to pass into the container — no translation.
MACRO_LEFS="$ADDITIONAL_LEFS_RAW"

# Tech + stdcell LEFs are platform-fixed. Hard-code the asap7 paths
# (guest-side); the script always runs inside the docker.
TECH_LEF_GUEST=/OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7_tech_1x_201209.lef
# Use only the R-flavor stdcell LEF (matches what ORFS's asap7 config.mk
# loads via SC_LEF). Loading L/SL/SRAM/DFFHQNH* duplicates pin definitions.
SC_LEF_GUEST=/OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7sc7p5t_28_R_1x_220121a.lef
SC_LEF_HOST_STOCK="$HOME/.volare/asap7/libs.ref/asap7sc7p5t_rvt/lef/asap7sc7p5t_28_R_1x_220121a.lef"

# Overlay artifacts (only used when WITH_OVERLAY=1).
#   OVERLAY_LEF: human-readable source of truth for predictive antenna
#     ratios. Passed into the docker via /work/... so the in-container
#     TCL can parse it and attach rules in-memory after read_db.
#   PATCHED_STDCELL_LEF: regenerated each run for offline inspection —
#     a copy of the stdcell LEF with ANTENNAGATEAREA injected on every
#     input pin. Lets users diff against the stock LEF to see exactly
#     which numbers the predictive sign-off is using. NOT loaded by
#     antenna_check.tcl (read_db wipes per-pin antenna data the LEF
#     would have supplied anyway; the TCL re-applies it in-memory).
#   OVERLAY_GATE_AREA_UM2: per-input-pin gate area to apply in-memory.
#     Must match the value baked into inject_antenna_gate_area.py so
#     the offline artifact stays consistent with the live check.
OVERLAY_LEF_HOST="$SCRIPT_DIR/asap7_antenna_overlay.lef"
OVERLAY_LEF_GUEST=/work/tech/asap7/orfs/asap7_antenna_overlay.lef
PATCHED_STDCELL_LEF_HOST="$REPO_ROOT/build/asap7_stdcell_with_antenna.lef"
OVERLAY_GATE_AREA_UM2=0.005

if [[ "$WITH_OVERLAY" == "1" ]]; then
    # PREDICTIVE-SIGN-OFF mode. (Not foundry-verified.)
    if [[ ! -f "$OVERLAY_LEF_HOST" ]]; then
        echo "antenna_check: ERROR: overlay LEF $OVERLAY_LEF_HOST missing" >&2
        exit 1
    fi
    if [[ ! -f "$SC_LEF_HOST_STOCK" ]]; then
        echo "antenna_check: ERROR: host-mirrored stdcell LEF $SC_LEF_HOST_STOCK missing — overlay mode needs it to (re)generate $PATCHED_STDCELL_LEF_HOST for offline inspection" >&2
        exit 1
    fi
    echo "antenna_check: --with-overlay — regenerating $PATCHED_STDCELL_LEF_HOST (offline diff artifact)"
    python3 "$SCRIPT_DIR/scripts/inject_antenna_gate_area.py" \
        "$SC_LEF_HOST_STOCK" "$PATCHED_STDCELL_LEF_HOST"
fi

ODB_GUEST=/work/build/orfs/results/asap7/$MODULE/base/6_final.odb
REPORT_GUEST=/work/build/orfs/reports/asap7/$MODULE/antenna.log

if [[ "$WITH_OVERLAY" == "1" ]]; then
    echo "antenna_check: $MODULE — running check_antennas with PREDICTIVE overlay against $ODB_HOST"
else
    echo "antenna_check: $MODULE — running check_antennas against $ODB_HOST"
fi

OVERLAY_ENV=""
if [[ "$WITH_OVERLAY" == "1" ]]; then
    OVERLAY_ENV="-e ANTENNA_OVERLAY_LEF=$OVERLAY_LEF_GUEST \
    -e ANTENNA_OVERLAY_GATE_AREA=$OVERLAY_GATE_AREA_UM2 \
    -e ANTENNA_OVERLAY_NOTE=Derived_from_Clark_2016_asap7_paper_geometry"
fi

set +e
sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO_ROOT:/work \
    -w /OpenROAD-flow-scripts/flow \
    -e MODULE_NAME=$MODULE \
    -e TECH_LEF=$TECH_LEF_GUEST \
    -e SC_LEF=$SC_LEF_GUEST \
    -e MACRO_LEFS='$MACRO_LEFS' \
    -e ODB_FILE=$ODB_GUEST \
    -e REPORT_FILE=$REPORT_GUEST \
    $OVERLAY_ENV \
    openroad/orfs:latest \
    /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad -no_init -no_splash -exit \
    /work/tech/asap7/orfs/scripts/antenna_check.tcl"
OR_RC=$?
set -e

# Decide pass/fail. Map OpenROAD tcl exit codes to our spec. The TCL
# layer uses 3=missing env (wrapper bug, shouldn't happen at runtime)
# and 4=openroad command failure; both collapse to bash exit 3 since
# from the user's perspective they're the same outcome ("the check did
# not run"). The OR_RC value is echoed in the diagnostic so a failing
# TCL exit is still traceable.
case $OR_RC in
    0) STATUS="clean" ;;
    2) STATUS="violations" ;;
    *) echo "antenna_check: ERROR: openroad/tcl exit $OR_RC" >&2
       echo "antenna_check: $MODULE: openroad invocation failed (TCL exit $OR_RC)"
       exit 3 ;;
esac

# Independent PDK-gap detection: if neither the platform tech LEF nor
# (in overlay mode) the overlay LEF contributes any ANTENNA properties,
# then check_antennas had no rules to evaluate against and the
# "0 violations" outcome is vacuous. This is a real tape-out blocker —
# surfacing it as a distinct exit code so it can't be mistaken for clean
# sign-off.
if [[ "$WITH_OVERLAY" == "1" ]]; then
    # Overlay LEF lives on the host.
    RULE_COUNT=$(grep -c -E '^[[:space:]]*ANTENNA' "$OVERLAY_LEF_HOST" || echo 0)
else
    # Stock tech LEF lives inside the docker image. Grep it via a
    # one-shot container. Track docker exit separately so a docker
    # failure (image gone, sg permission revoked) does not silently
    # report 0 rules → VACUOUS PASS.
    set +e
    RULE_COUNT_RAW=$(sg docker -c "docker run --rm openroad/orfs:latest \
        grep -c -E '^[[:space:]]*ANTENNA' $TECH_LEF_GUEST" 2>/dev/null)
    DOCKER_RC=$?
    set -e
    if (( DOCKER_RC != 0 && DOCKER_RC != 1 )); then
        # grep -c exits 1 when zero matches, which is exactly the asap7
        # case; treat 1 as "0 matches", but anything else (2+, 125+) is
        # a real docker/grep failure.
        echo "antenna_check: ERROR: docker rule-count probe exited $DOCKER_RC (cannot verify PDK gap)" >&2
        exit 3
    fi
    RULE_COUNT=$(printf '%s' "$RULE_COUNT_RAW" | tr -d '[:space:]')
    : "${RULE_COUNT:=0}"
fi

# Parse violation count from the report's machine-readable summary line.
VIO_COUNT=0
if [[ -f "$REPORT_HOST" ]]; then
    VIO_COUNT=$(grep -E "^ANTENNA_SUMMARY" "$REPORT_HOST" | tail -1 | sed -n 's/.*violations=\([0-9]*\).*/\1/p')
    VIO_COUNT="${VIO_COUNT:-0}"
fi

if (( RULE_COUNT == 0 )) && [[ "$STATUS" == "clean" ]]; then
    echo "antenna_check: $MODULE: VACUOUS PASS — asap7 tech LEF has zero antenna rules. See tech/asap7/PDK_GAPS.md."
    echo "antenna_check: report: $REPORT_HOST"
    exit 4
fi

OVERLAY_TAG=""
if [[ "$WITH_OVERLAY" == "1" ]]; then
    OVERLAY_TAG=" (PREDICTIVE overlay — NOT foundry sign-off)"
fi

if [[ "$STATUS" == "clean" ]]; then
    echo "antenna_check: $MODULE: CLEAN${OVERLAY_TAG} — 0 violations (PDK has $RULE_COUNT antenna properties on routing layers)"
    echo "antenna_check: report: $REPORT_HOST"
    exit 0
else
    echo "antenna_check: $MODULE: FAIL${OVERLAY_TAG} — $VIO_COUNT violation(s) remain after repair_antennas"
    echo "antenna_check: report: $REPORT_HOST"
    exit 2
fi
