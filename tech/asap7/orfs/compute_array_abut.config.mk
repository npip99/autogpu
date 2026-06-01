# Phase C of issue #32: full 32×32 compute_array with abutted mac_tmem_cell_tile.
#
# Architecture vs the existing compute_array.config.mk:
#   - Mac cells use the abutment-ready mac_tmem_cell_tile.lef (5 broadcast
#     signals on W↔E feedthrough pins; OBS stripped to M3 only).
#   - 32×32 mac grid abutted with ZERO inter-tile channel (pitch = 34.56
#     µm = exact tile width). Compare existing compute_array.config.mk's
#     55 µm pitch (channeled).
#   - skew_lane_a / skew_lane_b / cmd_unit remain channeled around the
#     array perimeter (their hardening is unchanged).
#   - Parent PDN: M7 vertical stripes + macro_grid M6-M7 connect
#     (compute_array_abut.pdn.tcl).
#   - Sized for 1300×1300 µm die — see gen calc in
#     compute_array_abut.macro_placement.tcl header.

export PLATFORM       = asap7
export DESIGN_NAME    = compute_array
export DESIGN_NICKNAME = compute_array_abut

# Use the chip_top_bcast1 sv2v output (MMA=32, BCAST_PIPE=1) — same input
# as the proven non-abutted compute_array.config.mk so RTL hardens
# identically up through synthesis.
export VERILOG_FILES = /work/build/sv2v/chip_top_bcast1.v
export SDC_FILE      = /work/tech/asap7/orfs/compute_array_abut.sdc

# Floorplan numbers must match compute_array_abut.macro_placement.tcl.
# Mac grid fits in x=[126.27, 1232.19], y=[126.27, 1232.19]; cmd_unit
# at (40,40), skew_a column at x=94.895, skew_b row at y=94.895.
export FLOORPLAN_DEF =
export DIE_AREA   = 0 0 1300 1300
export CORE_AREA  = 5 5 1295 1295

# Hardened leaf macros.
# Tile uses the post-processed mac_tmem_cell_tile.lef (OBS stripped to M3).
# Skew lanes + cmd_unit unchanged from the existing flow.
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

# Explicit placement (1089 macros: 1024 mac_tmem_cell_tile + 32+32 skew
# lanes + 1 cmd_unit). Abutted mac grid, channeled skew/cmd perimeter.
export MACRO_PLACEMENT_TCL = /work/tech/asap7/orfs/compute_array_abut.macro_placement.tcl

# Keep yosys from flattening the hierarchy — the 1089 named instances
# must stay visible for the macro placer to bind them.
export SYNTH_HIERARCHICAL = 1

# Zero halo + zero channel on the MAC tiles for true abutment. Skew
# lanes / cmd_unit get implicit channels from the macro_placement
# coordinates (10 µm gaps written in the TCL).
export MACRO_PLACE_HALO    = 0 0
export MACRO_PLACE_CHANNEL = 0 0

# Parent perimeter routing: leave M8/M9 for #33's clock infra. Tile
# internal routing caps at M6.
export MAX_ROUTING_LAYER = M7

# Custom parent PDN (see file header for rationale).
export PDN_TCL = /work/tech/asap7/orfs/compute_array_abut.pdn.tcl

# Skip last_gasp on first iteration (saves ~10 min). Re-enable when we
# need the final timing polish for a candidate tape-out.
export SKIP_LAST_GASP ?= 1
