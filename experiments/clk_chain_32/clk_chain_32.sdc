# V1 SDC for clk_chain_32 — 300 MHz (matches the abut SDCs).
#
# create_clock at chain input. Full propagated-clock STA — no
# set_propagated_clock short-circuiting. We want to see the actual
# wire+buffer delay accumulate across 32 stages.

current_design clk_chain_32

set clk_name    core_clock
set clk_port    clk_in
set clk_period  3333
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
