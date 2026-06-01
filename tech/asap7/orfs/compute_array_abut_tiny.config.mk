# compute_array_abut_tiny — 4×4 abutted compute_array (#40 integration test).
#
# Mirrors compute_array_abut.config.mk but at M=N=4 scale to validate the
# full architecture (cmd_unit + 4 skew_a + 4 skew_b + 4×4 abutted mac mesh)
# before scaling to 32×32.

export PLATFORM         = asap7
export DESIGN_NAME      = compute_array
export DESIGN_NICKNAME  = compute_array_abut_tiny

# 4×4 verilog (chip_top_bcast1's compute_array — same as compute_array_tiny_bcast1)
export VERILOG_FILES = /work/build/sv2v/compute_array_tiny_bcast1.v
export SDC_FILE      = /work/tech/asap7/orfs/compute_array_abut_tiny.sdc

# 350×350 µm die (computed by gen — cmd at SW, skew_a column W, skew_b
# row S, 4×4 mac mesh in interior abutted).
export FLOORPLAN_DEF =
export DIE_AREA   = 0 0 350 350
export CORE_AREA  = 5 5 345 345

# Hardened-leaf macros (all from #40 re-hardens with clk_w/clk_e).
ASAP7_RESULTS = /work/build/orfs/results/asap7
export ADDITIONAL_LEFS = \
    $(ASAP7_RESULTS)/mac_tmem_cell_tile/base/mac_tmem_cell_tile.lef \
    $(ASAP7_RESULTS)/skew_lane_a/base/skew_lane_a.lef \
    $(ASAP7_RESULTS)/skew_lane_b/base/skew_lane_b.lef \
    $(ASAP7_RESULTS)/cmd_unit/base/cmd_unit.lef
export ADDITIONAL_LIBS = \
    $(ASAP7_RESULTS)/mac_tmem_cell_tile/base/mac_tmem_cell_typ.lib \
    $(ASAP7_RESULTS)/skew_lane_a/base/skew_lane_a_typ.lib \
    $(ASAP7_RESULTS)/skew_lane_b/base/skew_lane_b_typ.lib \
    $(ASAP7_RESULTS)/cmd_unit/base/cmd_unit_typ.lib
export ADDITIONAL_GDS = \
    $(ASAP7_RESULTS)/mac_tmem_cell_tile/base/6_final.gds \
    $(ASAP7_RESULTS)/skew_lane_a/base/6_final.gds \
    $(ASAP7_RESULTS)/skew_lane_b/base/6_final.gds \
    $(ASAP7_RESULTS)/cmd_unit/base/6_final.gds

export MACRO_PLACEMENT_TCL = /work/tech/asap7/orfs/compute_array_abut_tiny.macro_placement.tcl
export SYNTH_HIERARCHICAL = 1

# Mac tiles abut with zero channel. Skew/cmd have implicit ~10 µm channels
# (placed at fixed coords in the macro_placement TCL).
export MACRO_PLACE_HALO    = 0 0
export MACRO_PLACE_CHANNEL = 0 0

export MAX_ROUTING_LAYER = M7
export PDN_TCL = /work/tech/asap7/orfs/compute_array_abut.pdn.tcl
export SKIP_LAST_GASP ?= 1
