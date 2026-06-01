current_design cmd_unit

set clk_name    core_clock
set clk_port    clk_w
# 300 MHz (3333 ps) matches the integration SDC. cmd_unit hardened at 1 GHz
# on master, but the clk_w/clk_e port rename + add for #40 pushed the
# post-resizer utilization to 85% (resizer over-buffering for the 1 GHz
# target) → GRT-0232 routability failure. The integration only needs
# 300 MHz, so target it directly here.
set clk_period  3333
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
