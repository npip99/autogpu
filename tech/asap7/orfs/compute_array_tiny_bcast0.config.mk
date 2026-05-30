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
export DESIGN_NICKNAME = compute_array_tiny_bcast0

# PRODUCTION tiny config. Closes hold + setup at 0 violations.
#
# Two changes from the original 1 GHz / BCAST_PIPE=0 / HOLD_SLACK_MARGIN=-200
# state:
#   1. BCAST_PIPE=1 — parent-level pipeline stage on every cmd_unit ->
#      skew_lane / mac_tmem_cell broadcast (and a matching output pipe
#      on the chip-external completion signals, so cocotb cycle-by-
#      cycle lockstep still passes; pymodel models the same latency
#      via its bcast_pipe= ctor arg). The pipe flops live in the
#      parent's CTS domain, which lets the resizer add hold-fix delay
#      on a much shorter inter-macro segment.
#   2. clock period 1000 ps -> 2500 ps in compute_array_tiny_bcast0.sdc.
#      At 1 GHz baseline itself had -1728 ps setup WNS; the 1 GHz target
#      was always aspirational on this design. (tiny stays at 2500 ps /
#      400 MHz; the full compute_array.sdc was later relaxed to 3333 ps /
#      300 MHz for the long far-column broadcast — issue #25 — which tiny's
#      short 4-wide broadcast does not need.)
#
# Other approaches explored that did NOT beat this config (kept in the
# tree as documentation, see headers of):
#   compute_array_tiny_slow.config.mk      — same settings, baseline
#   compute_array_tiny_slowbal.config.mk   — + CTS_CLUSTER tweaks (no gain)
#   compute_array_tiny_slowuskew.config.mk — + reversed useful-skew SDC (no gain)
#   compute_array_tiny_slowpipe2.config.mk — BCAST_PIPE=2 (+3 MHz fmax, +1 cycle latency)
#
# See tech/asap7/problems/A2_hold_timing_rtl.md for the journey.
export VERILOG_FILES = /work/build/sv2v/compute_array_tiny_bcast1.v
export SDC_FILE      = /work/tech/asap7/orfs/compute_array_tiny_bcast0.sdc

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

# HOLD_SLACK_MARGIN intentionally not set (default 0). With BCAST_PIPE=1
# in the parent and a 2500 ps clock period in compute_array_tiny_bcast0.sdc,
# repair_timing converges to 0 hold violations cleanly. If a future
# change pushes hold WNS back negative, do not re-add this knob — fix
# the root cause. See tech/asap7/problems/A2_hold_timing_rtl.md.

