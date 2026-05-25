# compute_array — hierarchical hardening on asap7 with hardened leaf macros.
#
# Treats mac_tmem_cell (×1024), skew_lane_a (×32), skew_lane_b (×32), and
# cmd_unit (×1) as black-box macros, each with its own LEF/LIB/GDS from the
# corresponding per-module ORFS run under build/orfs/results/asap7/<m>/base/.
#
# DIE_AREA + macro positions come from tech/asap7/orfs/scripts/gen_compute_array_floorplan.py,
# which mirrors the sky130 compute_array layout at asap7 scale.
#
# Run with: ./tech/asap7/orfs/run.sh compute_array

export PLATFORM     = asap7
export DESIGN_NAME      = compute_array
export DESIGN_NICKNAME = compute_array_clk5000

export VERILOG_FILES = /work/build/sv2v/chip_top.v
export SDC_FILE      = /work/tech/asap7/orfs/compute_array_clk5000.sdc

# Absolute floorplan (must match macro_placement.tcl numbers).
export FLOORPLAN_DEF =
export DIE_AREA  = 0 0 1950 1950
export CORE_AREA = 20 20 1930 1930

# Hardened leaf macros — LEFs come from `generate_abstract` in each leaf run.
ASAP7_RESULTS = /work/build/orfs/results/asap7
export ADDITIONAL_LEFS = \
    $(ASAP7_RESULTS)/mac_tmem_cell/base/mac_tmem_cell.lef \
    $(ASAP7_RESULTS)/skew_lane_a/base/skew_lane_a.lef \
    $(ASAP7_RESULTS)/skew_lane_b/base/skew_lane_b.lef \
    $(ASAP7_RESULTS)/cmd_unit/base/cmd_unit.lef
export ADDITIONAL_LIBS = \
    $(ASAP7_RESULTS)/mac_tmem_cell/base/mac_tmem_cell_typ.lib \
    $(ASAP7_RESULTS)/skew_lane_a/base/skew_lane_a_typ.lib \
    $(ASAP7_RESULTS)/skew_lane_b/base/skew_lane_b_typ.lib \
    $(ASAP7_RESULTS)/cmd_unit/base/cmd_unit_typ.lib
export ADDITIONAL_GDS = \
    $(ASAP7_RESULTS)/mac_tmem_cell/base/6_final.gds \
    $(ASAP7_RESULTS)/skew_lane_a/base/6_final.gds \
    $(ASAP7_RESULTS)/skew_lane_b/base/6_final.gds \
    $(ASAP7_RESULTS)/cmd_unit/base/6_final.gds

# Explicit placement (1089 macros: 1024 mac_tmem_cell + 32+32 skew_lanes + 1 cmd_unit).
export MACRO_PLACEMENT_TCL = /work/tech/asap7/orfs/compute_array.macro_placement.tcl

# Keep yosys from flattening the hierarchy — the 1024 mac_tmem_cell, 32 skew_a,
# 32 skew_b, and 1 cmd_unit instances must stay as named instances for the
# macro placer to bind them.
export SYNTH_HIERARCHICAL = 1

# Halo + channel spacing around each macro instance (µm). Routing tracks fit
# in the gap between macro edges; small enough to keep die compact.
export MACRO_PLACE_HALO = 2 2
export MACRO_PLACE_CHANNEL = 4 4

# Custom PDN — stripes placed in the channels BETWEEN macros, never over
# them. asap7's BLOCKS_grid_strategy.tcl clips per-stripe against per-macro
# halos and is O(stripes × macros), so it chokes at >20min on 1089 macros.
# Our PDN aligns its stripe pitch + offset to the mac-grid channel centers,
# so pdngen never needs halo clipping.
export PDN_TCL = /work/tech/asap7/orfs/compute_array.pdn.tcl

export SKIP_LAST_GASP ?= 1

