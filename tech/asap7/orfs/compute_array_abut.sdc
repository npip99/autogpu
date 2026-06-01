# Phase C 32×32 abutted compute_array — relaxed SDC for first hardening.
# 3333 ps = 300 MHz, matches mac_array_small_abut.sdc and compute_array's
# proven operating point on the non-abutted layout.

current_design compute_array

set clk_name    core_clock
set clk_port    clk
set clk_period  3333
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
