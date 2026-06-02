export PLATFORM     = asap7
export DESIGN_NAME  = skew_lane_a

export VERILOG_FILES = /work/build/sv2v/chip_top.v
export SDC_FILE      = /work/tech/asap7/orfs/skew_lane_a.sdc

# B6 (#40): 30×30 µm tile to accommodate the added 260-bit chain register
# (~260 extra flops on top of the original 31-stage internal shift register).
# DIE_AREA matches; CORE_AREA leaves 2 µm margin per side.
export DIE_AREA   = 0 0 30 30
export CORE_AREA  = 2 2 28 28

# Explicit pin placement — chain pins must be at abutment-aligned coords
# (chain_w_s[k] at same X as chain_e_n[k]) so vertically-stacked skew_a
# instances form zero-length parent nets via abutment.
export IO_CONSTRAINTS = /work/tech/asap7/orfs/scripts/skew_lane_a.pins.tcl

export PLACE_DENSITY = 0.75

export SKIP_LAST_GASP ?= 1
