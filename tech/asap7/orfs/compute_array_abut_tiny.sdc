# #40 4×4 integration SDC — same 300 MHz target.

current_design compute_array

set clk_name    core_clock
set clk_port    clk
set clk_period  3333
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]

# Feedthrough false-paths (same classification as #36/PR rules):
# drain_en_w stays timed (single-cycle snapshot); reset/drain_slot/scrub_en
# false-pathed (quasi-static).
set_false_path -through [get_pins -hierarchical *u_cell/reset_w]
set_false_path -through [get_pins -hierarchical *u_cell/drain_slot_w*]
set_false_path -through [get_pins -hierarchical *u_cell/scrub_en_w]

# Block-level I/O false-paths — same scope-shift as compute_array.sdc
# (PR #27 / issue #25). At block level the chip-IO ports' input/output
# delays don't reflect the real chip_top boundary timing; trying to close
# them here produces ~hundreds of bogus hold/setup violations that the
# resizer can't repair (saw 336 hold violations, RSZ-0060 max buffer
# count reached, build died at CTS). The real launch/capture flops live
# in chip_top, and compute_array's abstract .lib gives chip_top STA the
# true clk-pin-relative I/O arcs there. Defer block-level IO closure to
# chip_top (#28).
set io_data_inputs [all_inputs -no_clocks]
set_false_path -hold  -from $io_data_inputs
set_false_path -hold  -to   [all_outputs]
set_false_path -setup -from $io_data_inputs
set_false_path -setup -to   [all_outputs]
