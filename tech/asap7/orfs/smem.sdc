current_design smem

# 400 MHz (2500 ps) — matches the chip-wide target (compute_array.sdc;
# chip_top runs slower still at 4000 ps). An earlier 1 GHz target was an
# outlier that left the wr_addr→bank address-decode arc ~253 ps short on
# setup. At 2500 ps setup closes with ~196 ps margin AND post-route repair
# closes hold to +58 ps (the relaxed clock gives the resizer the setup
# headroom to insert hold buffers). See B2_smem_hold_timing.md.
set clk_name    core_clock
set clk_port    clk
set clk_period  2500
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
