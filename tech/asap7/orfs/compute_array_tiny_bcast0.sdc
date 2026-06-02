current_design compute_array

# 2500 ps (400 MHz) — closes hold + setup at 0 violations. Was 1 GHz;
# at that target setup WNS was -1267 ps and hold WNS -251 ps. fmax of
# the routed design is ~440 MHz (period_min 2270 ps post-route), so
# 2500 ps leaves ~230 ps slack.
# See tech/asap7/problems/A2_hold_timing_rtl.md for the journey.
set clk_name    core_clock
set clk_port    clk
set clk_period  2500
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
