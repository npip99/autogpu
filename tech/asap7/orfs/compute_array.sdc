current_design compute_array

# 300 MHz (3333 ps) — see tech/asap7/DESIGN.md "Clock period vs RC budget".
# PR #27 relaxed this from 400 MHz (2500 ps) to absorb the `push_a`/`push_b`
# broadcast-wire setup at full 32×32 (single BCAST_PIPE register fanning out
# ~1600 µm to the far-column skew_lanes).
#
# Issue #31 (this branch) restructures the broadcast in compute_array.sv as
# a per-skew_lane relay chain (tap_index=0 + parent-level chain registers),
# which by itself moves the worst broadcast setup from -451 ps to ~-190 ps
# at 2500 ps — but does NOT yet close 400 MHz because the placer is
# clustering the chain registers instead of distributing them along the
# skew_lanes. Recovering the full 400 MHz requires explicit placement
# constraints that bind each `pa_chain[i]` adjacent to `skew_lane_a[i]` and
# `pb_chain[j]` adjacent to `skew_lane_b[j]` — tracked as a follow-up. Until
# that lands, this SDC stays at 3333 ps (proven to close end-to-end). The
# chain RTL still lands because it is the prerequisite for issue #32 (tile
# abutment) and because it improves the broadcast setup substantially even
# without the placement constraint. tiny_bcast0 stays at 2500 ps.
# asap7 lib uses 1 ps time units.
set clk_name    core_clock
set clk_port    clk
set clk_period  3333
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]

# ── Issue #25: exclude I/O paths from block-level timing closure ─────────
# The full 32×32 die (1950 µm) gives the parent clock tree a ~3 ns insertion
# delay to reach 1089 macro clock pins, while every I/O port is constrained
# at only 500 ps (0.2 × 2500). That insertion delay *exceeds the clock period*
# (2500 ps), so every boundary path is off by more than a cycle relative to
# the clk pin:
#   - HOLD: input→first-flop data arrives ~2.5 ns before the late capture
#     clock → ~1586 huge I/O hold violations. At full scale CTS repair_timing
#     pads each I/O bit with ~50 buffers and exhausts the budget (RSZ-0060,
#     95205 buffers) before route — the issue-#25 symptom.
#   - SETUP: outputs (e.g. drain_row_data[*]) launch ~3 ns into the 2.5 ns
#     period and miss their deadline → ~1443 I/O setup violations that
#     repair_timing plateaus on (~-666 ps) and cannot fix.
#
# BOTH are I/O-only. Verified on the placed/post-route 32×32 ODB: every
# failing endpoint starts or ends at a port; ZERO failing flop-to-flop paths.
# Excluding I/O hold moved hold WNS from -651 ps to +10 ps. Internal
# (macro-to-macro) timing closes on its own — this is NOT the macro-to-macro
# CTS skew the issue hypothesized (that set is small, worst -56 ps hold,
# repair closes it to 0).
#
# Block-level I/O timing is not meaningful standalone: the real launch/capture
# registers live in chip_top (cmdproc ↔ compute_array), and compute_array's
# abstract .lib characterizes the true clk-pin-relative I/O arcs (folding in
# the 3 ns insertion), so STA at chip_top sees the real boundary timing and
# must close it there — it is NOT hidden by these false-paths. See issue for
# the chip_top boundary-closure work (useful-skew balancing / insertion-delay
# reduction / registered interface); A6/chip_top owns it. tiny_bcast0 (400 µm,
# ~300 ps insertion) never hit this, so these constraints are no-ops there.
set io_data_inputs [all_inputs -no_clocks]
set_false_path -hold  -from $io_data_inputs
set_false_path -hold  -to   [all_outputs]
set_false_path -setup -from $io_data_inputs
set_false_path -setup -to   [all_outputs]
