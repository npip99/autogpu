# compute_array_tiny_slowpipe2 — A2 experiment archive.
#
# Hypothesis: a second BCAST_PIPE stage breaks the cmd_unit ->
# skew_lane broadcast into THREE shorter segments instead of two,
# pushing fmax higher than the single-pipe baseline.
#
# Result: post-route setup 0, hold 0, 0 violations, fmax 443 MHz —
# barely +3 MHz over "slow" (440 MHz), at the cost of one extra cycle
# of broadcast latency (pymodel ComputeArray(bcast_pipe=2) models it
# correctly; cocotb lockstep passes). Not worth the latency hit on
# tiny_bcast0 — the production config stays at BCAST_PIPE=1. Kept as
# data for the latency/fmax curve.

export PLATFORM     = asap7
export DESIGN_NAME      = compute_array
export DESIGN_NICKNAME = compute_array_tiny_slowpipe2

export VERILOG_FILES = /work/build/sv2v/compute_array_tiny_bcast2.v
export SDC_FILE      = /work/tech/asap7/orfs/compute_array_tiny_slow.sdc

# Absolute floorplan (must match macro_placement.tcl numbers).
export FLOORPLAN_DEF =
export DIE_AREA  = 0 0 400 400
export CORE_AREA = 20 20 380 380

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
export MACRO_PLACEMENT_TCL = /work/tech/asap7/orfs/compute_array_tiny.macro_placement.tcl

# Keep yosys from flattening the hierarchy — the 1024 mac_tmem_cell, 32 skew_a,
# 32 skew_b, and 1 cmd_unit instances must stay as named instances for the
# macro placer to bind them.
export SYNTH_HIERARCHICAL = 1

# Halo + channel spacing around each macro instance (µm). Routing tracks fit
# in the gap between macro edges; small enough to keep die compact.
export MACRO_PLACE_HALO = 2 2
export MACRO_PLACE_CHANNEL = 4 4

# Forbid tap cells inside the inter-macro channels. asap7's tapcell.tcl
# uses these env vars to keep stdcell rows from touching macros. With a
# 20.5 µm channel and 11 µm halo each side (= 22 µm exclusion), no
# stdcell rows form between macros — so no fragmented M1 followpins
# (PDN-0179). Tap cells still get placed in the wide perimeter band
# outside the mac grid where rows are continuous.
export MACRO_ROWS_HALO_X = 11
export MACRO_ROWS_HALO_Y = 11


# Custom PDN — stripes placed in the channels BETWEEN macros, never over
# them. asap7's BLOCKS_grid_strategy.tcl clips per-stripe against per-macro
# halos and is O(stripes × macros), so it chokes at >20min on 1089 macros.
# Our PDN aligns its stripe pitch + offset to the mac-grid channel centers,
# so pdngen never needs halo clipping.
export PDN_TCL = /work/tech/asap7/orfs/compute_array.pdn.tcl

export SKIP_LAST_GASP ?= 1


