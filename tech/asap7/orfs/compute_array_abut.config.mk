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

# Plain chip_top.v: the cmd_unit→skew_a[0] / cmd_unit→skew_b[0] arcs
# are short (cmd_unit and skew chain-heads abut at the SW corner per
# compute_array_abut.macro_placement.tcl), and the broadcast delay
# lives inside the hardened skew_lane_a/b chain register (B6 #40).
# The parent BCAST_PIPE knob was deleted in #45 — see compute_array.sv
# header comment for the rationale.
export VERILOG_FILES = /work/build/sv2v/chip_top.v
export SDC_FILE      = /work/tech/asap7/orfs/compute_array_abut.sdc

# Floorplan numbers must match compute_array_abut.macro_placement.tcl.
# Mac grid fits in x=[126.27, 1232.19], y=[126.27, 1232.19]; cmd_unit
# at (40,40), skew_a column at x=94.895, skew_b row at y=94.895.
export FLOORPLAN_DEF =
# B6: back to 1300×1300 (pure abutment chain removed the resizer buffer
# congestion that made the 1500×1500 workaround necessary). Mac grid is
# at x=[134.895, 1240.815] per the regenerated macro_placement.tcl, so
# 1300 leaves ~60 µm E/N strip + ~135 µm W/S strip (cmd_unit + skew).
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

# Pin compute_array's perimeter pins by chip-top adjacency (smem-W, store-S).
# Eliminates the drain_row_data L-shape wrap-around that was the source of
# the east-strip congestion at GRT.
export IO_CONSTRAINTS = /work/tech/asap7/orfs/scripts/compute_array_abut.pins.tcl

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

# WORKAROUND (#40): GRT iter cap + allow_congestion. Take-4 hit a stuck
# mazeRouteMSMDOrder3D iter in GRT (perf: 97% CPU, iter stuck >47 min).
# UNCONFIRMED which specific net causes the spin — see tech/RCA_DISCIPLINE.md
# for the diagnosis process that should be followed before claiming a
# root cause. 5-iter cap exits GRT and hands residual overflow to DRT
# for local fix. ORFS variable is GLOBAL_ROUTE_ARGS (singular ROUTE) —
# the var that REPLACES the flow's defaults; verify name against
# /OpenROAD-flow-scripts/flow/scripts/variables.yaml when in doubt.
# HOLD_SLACK_MARGIN is no longer needed (parent pa_chain is gone in B4,
# no parent/macro clock-skew hold storm).
# Take 13: remove GRT iter cap to let GRT converge naturally. Previous
# attempts capped at 5 iters and used -allow_congestion, but the post-
# recover_power incremental GRT call doesn't accept those args and
# fails with GRT-0116 whenever first-pass GRT leaves residual overflow.
# With B6's reduced parent fanout + HOLD_SLACK_MARGIN limiting resizer
# buffer insertion, GRT should converge in reasonable time. If not,
# diagnose_grt.sh will identify what's actually congesting.
# export GLOBAL_ROUTE_ARGS = -allow_congestion -congestion_iterations 5 -congestion_report_iter_step 5 -verbose
export SKIP_INCREMENTAL_REPAIR = 1

# Take 11 had 350 hold-violated endpoints post-CTS → resizer inserted
# hold buffers (250+ visible at W mac boundary) → GRT-0116 at post-
# recover_power incremental call. HOLD_SLACK_MARGIN=-2000 ps tells the
# resizer to stop chasing hold below -2 ns of slack, which leaves real
# violations un-buffered for chip_top to clean up via clock-tree
# balancing or post-place CTS optimization. Workaround — real fix is
# B7: tighten skew_lane's internal clock distribution so adjacent
# chain registers see matched insertion delay.
export HOLD_SLACK_MARGIN = -2000
