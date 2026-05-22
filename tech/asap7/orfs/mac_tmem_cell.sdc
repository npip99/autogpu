current_design mac_tmem_cell

# 1 GHz clock — asap7 lib uses 1ps time units, so period is in ps.
set clk_name    core_clock
set clk_port    clk
set clk_period  1000
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
