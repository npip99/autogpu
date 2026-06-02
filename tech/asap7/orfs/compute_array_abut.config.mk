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
# BCAST_PIPE=0 (not 1). PR #27's BCAST_PIPE=1 was the right answer for
# the OLD fan-out broadcast topology where cmd_unit drove 32 skew_lanes via
# one corner-to-corner wire (~1500 µm; setup was -451 ps without the pipe).
# In the #34 chain-restructure + #40 abutment-feedthrough topology, cmd_unit
# drives push_byte → skew_a[0] (~50 µm), then skew_a[i] → skew_a[i+1] via
# 35 µm abutment hops. No long fan-out wire. The pa_pipe[0] parent
# register (capture endpoint at BCAST_PIPE>0) was the source of 26K hold
# buffer insertions at 32×32 due to clock-skew between cmd_unit's internal
# CTS (~ns) and parent CTS (parent grew at 32×32 to match). With
# BCAST_PIPE=0 the launch (cmd_unit internal flop) and capture (skew_a[0]
# internal flop) both sit inside hardened macros with similar internal CTS
# insertion → no skew → no hold storm.
export VERILOG_FILES = /work/build/sv2v/chip_top_bcast0.v
export SDC_FILE      = /work/tech/asap7/orfs/compute_array_abut.sdc

# Floorplan numbers must match compute_array_abut.macro_placement.tcl.
# Mac grid fits in x=[126.27, 1232.19], y=[126.27, 1232.19]; cmd_unit
# at (40,40), skew_a column at x=94.895, skew_b row at y=94.895.
export FLOORPLAN_DEF =
# Bumped 1300→1500 to give E+N perimeter strips ~270 µm of empty routing
# room. Mac mesh stays in SW (per macro_placement.tcl hardcoded coords:
# mac at x,y ∈ [126.27, 1232.19]). The previous 1300×1300 left only ~68 µm
# of E/N strip — too tight for the maze router to find detours around
# congestion on the 8K-fanout chip clk net (perf showed iter 16 spent
# 97% of CPU in mazeRouteMSMDOrder3D, never converging). W/S strips
# stay tight (IO_CONSTRAINTS pins SMEM/cmd to W, drain to S — those
# nets only have short trips to make).
export DIE_AREA   = 0 0 1500 1500
export CORE_AREA  = 5 5 1495 1495

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

# Tell the resizer to stop chasing hold below this slack (in ps). At 32×32
# the parent-level pa_pipe[*] flops capture cmd_unit's registered outputs;
# cmd_unit's internal CTS adds ~ns of insertion delay while parent CTS has
# little, so the launch-capture clock skew creates hold violations the
# resizer can't fully patch (saw WNS=-1813 ps, 26681 hold buffers inserted
# before RSZ-0060 max-buffer-count kill). -2000 ps lets the resizer stop
# instead of burning out. WHY: same kind of trade master compute_array
# made via PR #27 — the real fix is balancing cmd_unit's internal CTS
# against parent CTS, but that's a separate timing-closure task; for #40
# we just need geometry + topology to close.
export HOLD_SLACK_MARGIN = -2000

# Cap GRT extra iterations + allow exit with residual congestion. The 1300×1300
# build hung iter 16/30 for 78+ min on the 8K-fanout chip clk net (perf:
# 97% of CPU in grt::FastRouteCore::mazeRouteMSMDOrder3D). Caps stop GRT
# from chasing every overflow; remaining overflow is handed to DRT which
# patches it locally instead of globally re-routing the whole clk tree.
export OR_GLOBAL_ROUTING_ARGS = -allow_congestion -congestion_iterations 5
