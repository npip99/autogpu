#!/bin/bash
# Per-module synthesis diagnostic.
#
# Runs yosys+ABC on each module separately, but inside a SINGLE yosys session
# so liberty load and container startup costs are amortized. After each
# module we print wall time + cell count.
#
# Use to:
#   - See per-module progress instead of one opaque chip-level run
#   - Find which module hangs ABC if any do
#
# Usage:
#   ./per_module_synth.sh                 # default module list
#   ./per_module_synth.sh foo bar baz     # specific modules in order

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SV_FILE="$REPO_ROOT/build/sv2v/chip_top.v"
PDK_VERSION="$(ls "$HOME/.volare/volare/sky130/versions/" | head -1)"
LIB="$HOME/.volare/volare/sky130/versions/$PDK_VERSION/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
OUT_DIR="$REPO_ROOT/build/per_module"
SCRIPT="$OUT_DIR/all.ys"
LOG="$OUT_DIR/all.log"

[[ -f "$SV_FILE" ]] || { echo "ERROR: $SV_FILE missing. Run 'make sv2v' first."; exit 1; }
mkdir -p "$OUT_DIR"

# Discover module names from the sv2v output. Parameterized modules get
# specialized names (e.g. fpnew_fma → fpnew_fma_EA93F), so hard-coding
# would drift every time a parameter changes.
mapfile -t DEFAULT_MODULES < <(grep -oE '^module [A-Za-z_][A-Za-z_0-9]*' "$SV_FILE" | awk '{print $2}')

if [[ $# -gt 0 ]]; then
    MODULES=("$@")
else
    MODULES=("${DEFAULT_MODULES[@]}")
fi

# Build the yosys script: load source + liberty once, then per-module synth.
# `tee -o ...` writes the stat output and an end-marker for each module so the
# host can parse cell count + walltime.
{
    # Load source + liberty once, snapshot the pristine state, then per-module
    # synth restores from the snapshot. Avoids re-parsing the verilog 17 times.
    echo "read_verilog -sv -formal $SV_FILE"
    echo "read_liberty -lib $LIB"
    echo "design -save pristine"
    for M in "${MODULES[@]}"; do
        echo "log === START $M ==="
        echo "design -load pristine"
        echo "hierarchy -check -top $M"
        # -noshare matches the chip-level config (SYNTH_SHARE_RESOURCES: false).
        # SHARE is SAT-driven and can blow up on certain memory-heavy modules.
        echo "synth -top $M -flatten -noshare"
        echo "dfflibmap -liberty $LIB"
        echo "abc -liberty $LIB"
        echo "opt_clean -purge"
        echo "stat -liberty $LIB"
        echo "log === END $M ==="
    done
} > "$SCRIPT"

echo "Running yosys on ${#MODULES[@]} modules; tail $LOG for live progress."
echo

sg docker -c "docker run --rm -i \
    -v $REPO_ROOT:$REPO_ROOT \
    -v $HOME/.volare:$HOME/.volare \
    -w $SCRIPT_DIR \
    ghcr.io/efabless/openlane2:2.3.10 \
    yosys -s $SCRIPT < /dev/null" 2>&1 \
  | awk '{ print strftime("[%H:%M:%S] "), $0; fflush() }' \
  | tee "$LOG"

# Post-process: per-module elapsed from line-level timestamps.
echo
echo "=== Summary ==="
awk '
    function ts_to_secs(s,   h, m, sec) {
        sub(/^\[/, "", s); sub(/\]$/, "", s)
        split(s, a, ":")
        return a[1]*3600 + a[2]*60 + a[3]
    }
    /=== START / { mod=$4; t0=ts_to_secs($1) }
    /Number of cells:/ { cells=$NF }
    /=== END / {
        elapsed = ts_to_secs($1) - t0
        if (elapsed < 0) elapsed += 86400
        printf "%-22s %8ds  %s cells\n", $4, elapsed, cells
        cells=""
    }
' "$LOG"
