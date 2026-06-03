export PLATFORM     = asap7
export DESIGN_NAME  = load

export VERILOG_FILES = /work/build/sv2v/chip_top.v
export SDC_FILE      = /work/tech/asap7/orfs/load.sdc
export IO_CONSTRAINTS = /work/tech/asap7/orfs/scripts/load.pins.tcl

export CORE_UTILIZATION  = 65
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN       = 2
export PLACE_DENSITY     = 0.75

export SKIP_LAST_GASP ?= 1
