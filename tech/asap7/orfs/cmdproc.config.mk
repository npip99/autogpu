export PLATFORM     = asap7
export DESIGN_NAME  = cmdproc

export VERILOG_FILES = /work/build/sv2v/chip_top.v
export SDC_FILE      = /work/tech/asap7/orfs/cmdproc.sdc
export IO_CONSTRAINTS = /work/tech/asap7/orfs/scripts/cmdproc.pins.tcl

# Relaxed 65→50 util + 0.75→0.65 density after the first 3333 ps run hit
# 23K DRT spacing violations from congestion (71K stdcells + 677 IOs +
# 16K-bit FF IMEM packed too tight). 50/0.65 matches the other chip_top
# leaves (load, store, barrier) which close cleanly.
export CORE_UTILIZATION  = 50
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN       = 2
export PLACE_DENSITY     = 0.65

export SKIP_LAST_GASP ?= 1

# cmdproc infers a 64x256 (16384-bit) memory and several smaller ones.
# Default SYNTH_MEMORY_MAX_BITS is 4096 — bump so yosys maps them all to
# flip-flops instead of refusing to synthesize (asap7 has no SRAM macro).
export SYNTH_MEMORY_MAX_BITS = 32768
