export PLATFORM     = asap7
export DESIGN_NAME  = mac_array_small

# mac_array_small isn't instantiated by chip_top so it's not in chip_top.v.
# tech/sky130/Makefile compiles a standalone sv2v file for it.
export VERILOG_FILES = /work/build/sv2v/mac_array_small.v
export SDC_FILE      = /work/tech/asap7/orfs/mac_array_small.sdc

export CORE_UTILIZATION  = 65
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN       = 2
export PLACE_DENSITY     = 0.75

export SKIP_LAST_GASP ?= 1
