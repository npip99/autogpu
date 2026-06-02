export PLATFORM     = asap7
export DESIGN_NAME  = skew_lane_b

export VERILOG_FILES = /work/build/sv2v/chip_top.v
export SDC_FILE      = /work/tech/asap7/orfs/skew_lane_b.sdc

# B6 (#40): same tile size as skew_lane_a, with chain pins on W/E for
# horizontal abutment in compute_array's south row.
export DIE_AREA   = 0 0 30 30
export CORE_AREA  = 2 2 28 28

export IO_CONSTRAINTS = /work/tech/asap7/orfs/scripts/skew_lane_b.pins.tcl

export PLACE_DENSITY = 0.75

export SKIP_LAST_GASP ?= 1
