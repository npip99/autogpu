current_design compute_array

# 2500 ps (400 MHz) — matches the full compute_array.sdc target. The 1 GHz
# bcast0.sdc was an exploration variant that infeasible even on baseline
# (-1728 ps WNS at CTS). At 400 MHz combinational paths through the
# BCAST_PIPE=1 forward stage easily close setup, and the resizer has
# enough setup slack to insert hold-fix delay cells on cell-to-cell paths.
set clk_name    core_clock
set clk_port    clk
set clk_period  2500
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
