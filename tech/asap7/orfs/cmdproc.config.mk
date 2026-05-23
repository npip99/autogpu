export PLATFORM     = asap7
export DESIGN_NAME  = cmdproc

export VERILOG_FILES = /work/build/sv2v/chip_top.v
export SDC_FILE      = /work/tech/asap7/orfs/cmdproc.sdc

export CORE_UTILIZATION  = 65
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN       = 2
export PLACE_DENSITY     = 0.75

export SKIP_LAST_GASP ?= 1

# cmdproc infers a 64x256 (16384-bit) memory and several smaller ones.
# Default SYNTH_MEMORY_MAX_BITS is 4096 — bump so yosys maps them all to
# flip-flops instead of refusing to synthesize (asap7 has no SRAM macro).
export SYNTH_MEMORY_MAX_BITS = 32768
