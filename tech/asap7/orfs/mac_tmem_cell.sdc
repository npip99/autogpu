current_design mac_tmem_cell

# 1 GHz clock — asap7 lib uses 1ps time units, so period is in ps.
set clk_name    core_clock
set clk_port    clk_w
set clk_period  1000
set clk_io_pct  0.2

# clk_w (the W-edge feedthrough input — issue #40) is the actual clock.
# clk_e is its unbuffered combinational pass-through (`assign clk_e = clk_w`).
# Declaring clk_w as the clock here lets write_timing_model emit:
#   1. flop-output arcs (a_out, drain_out, etc.) characterized against clk_w
#   2. a clk_w → clk_e combinational arc that lets parent-level STA
#      propagate the clock across abutted tiles.
# Without this declaration the abstract .lib has no timing arcs and
# parent STA reports "no paths found" (fmax = inf).
create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
