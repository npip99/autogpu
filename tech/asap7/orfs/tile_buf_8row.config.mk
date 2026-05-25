export PLATFORM     = asap7
export DESIGN_NAME  = tile_buf_8row

# Standalone sv2v target — built by tech/sky130/Makefile (shared with sky130).
export VERILOG_FILES = /work/build/sv2v/tile_buf_8row.v
export SDC_FILE      = /work/tech/asap7/orfs/tile_buf_8row.sdc

# 8 × 1024 bits of FFs plus 2 × 1024-bit perimeter buses. Modest util keeps
# the perimeter pin density routable; sky130 uses 30% with FP_SIZING relative,
# but ORFS prefers utilization-driven sizing — 50% leaves comfortable channels.
export CORE_UTILIZATION  = 50
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN       = 2
export PLACE_DENSITY     = 0.70

# 8 × 1024 = 8192-bit memory in tile_buf_8row.sv synthesizes as a flop array.
# ORFS asap7 default SYNTH_MEMORY_MAX_BITS is 4096 (steers larger memories
# to SRAM macros). No asap7 macro is the right shape (no 8×1024) and the
# SV comment explicitly says "Synthesizable as FFs today (~8 KB)", so raise
# the cap to permit the FF inference.
export SYNTH_MEMORY_MAX_BITS = 8192

export SKIP_LAST_GASP ?= 1
