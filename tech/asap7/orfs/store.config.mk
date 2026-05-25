export PLATFORM     = asap7
export DESIGN_NAME  = store

# Pulled from chip_top.v — store doesn't instantiate sram_1rw (it uses
# tile_buf_8row banks, which are FFs), so the shared sv2v output works
# unchanged.
export VERILOG_FILES = /work/build/sv2v/chip_top.v
export SDC_FILE      = /work/tech/asap7/orfs/store.sdc

export CORE_UTILIZATION  = 50
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN       = 2
export PLACE_DENSITY     = 0.70

# 4 × tile_buf_8row banks → 32 Kib of FF storage inlined into store. Cap
# generously above the aggregate so yosys infers per-bank FF arrays.
export SYNTH_MEMORY_MAX_BITS = 65536

export SKIP_LAST_GASP ?= 1
