# Phase B 4×4 abutment harness — relaxed SDC matching compute_array's
# operating point (300 MHz). The mac_tmem_cell_tile hardens at fmax
# 495 MHz; targeting 1 GHz (mac_array_small.sdc default) creates massive
# setup violations that hurt the router. 3333 ps gives ample timing
# slack so GRT can focus on routability rather than timing-driven detours.

current_design mac_array_small

set clk_name    core_clock
set clk_port    clk
set clk_period  3333
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
