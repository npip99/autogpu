current_design compute_array

# 100 MHz (10000 ps) — variant config to find a period this design meets.
# See tech/asap7/DESIGN.md "Clock period vs RC budget".
set clk_name    core_clock
set clk_port    clk
set clk_period  10000
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
