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
