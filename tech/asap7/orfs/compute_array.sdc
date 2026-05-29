current_design compute_array

# 400 MHz (2500 ps) — see tech/asap7/DESIGN.md "Clock period vs RC budget".
# 1 GHz was off by -1.22 ns on the cmd_unit → east-mac broadcast path;
# wire RC across 1.5 mm of asap7 metal can't be repaired by the resizer.
# asap7 lib uses 1 ps time units.
set clk_name    core_clock
set clk_port    clk
set clk_period  2500
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]

# ── Issue #25: exclude I/O paths from block-level HOLD closure ───────────
# The full 32×32 die (1950 µm) gives the parent clock tree a ~3 ns insertion
# delay to reach 1089 macro clock pins, while every input is constrained at
# only 500 ps (0.2 × 2500). Input→first-flop data then arrives ~2.5 ns before
# the late capture clock → ~1586 huge I/O HOLD violations, with ZERO failing
# flop-to-flop paths (verified on the placed 32×32 ODB: excluding I/O hold
# moves hold WNS from -651 ps to +10 ps, setup unchanged at +212 ps). At full
# scale CTS repair_timing pads each I/O bit with ~50 buffers and exhausts the
# budget (RSZ-0060, 95205 buffers) before route — the issue-#25 symptom.
#
# This is NOT macro-to-macro CTS skew (the issue's original hypothesis). The
# genuine inter-macro skew hold (~2186 endpoints, worst -56 ps) is small and
# repair_timing closes it to 0 ps on its own once the I/O paths stop
# exhausting the budget.
#
# Block-level I/O hold is not meaningful standalone: the real launch register
# lives in chip_top (cmdproc → compute_array), and compute_array's abstract
# .lib folds the 3 ns insertion into a relaxed (negative) input-hold arc, so
# the I/O hold relationship is re-closed at chip_top against its own clock
# tree. chip_top (A6) must carry the matching boundary-hold check. tiny_bcast0
# (400 µm, ~300 ps insertion) never hit this, so the constraint is a no-op
# there.
set_false_path -hold -from [all_inputs -no_clocks]
set_false_path -hold -to [all_outputs]
