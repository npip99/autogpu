# V1 SDC for clk_chain_32 — 300 MHz (matches the abut SDCs).
#
# Tell CTS: chain[32] is a generated clock derived from clk_in via the
# 32-buffer chain. CTS must NOT rebuild this — just propagate STA delay
# through the chain. The single terminal FF (q) gets its clock from
# chain[32] (the generated clock); the data path is trivially short
# (d_in port → FF.D). What matters: the clock arrival time at q.CLK,
# which is the cumulative insertion through all 32 stages.

current_design clk_chain_32

set clk_name    core_clock
set clk_port    clk_in
set clk_period  3333
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

# Declare the chain output as a generated clock so CTS leaves it alone.
create_generated_clock -name chain_end \
                       -source [get_ports clk_in] \
                       -divide_by 1 \
                       [get_pins g_buf\[31\].u_buf/Y]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]

# Preserve the chain — block the resizer from removing it as dangling logic.
set_dont_touch [get_cells -hier "*u_buf*"]
set_dont_touch [get_nets -hier "*chain*"]
set_propagated_clock [all_clocks]
