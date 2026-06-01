# Phase B 4×4 abutment harness — relaxed SDC matching compute_array's
# operating point (300 MHz). The mac_tmem_cell_tile hardens at fmax
# 495 MHz; targeting 1 GHz (mac_array_small.sdc default) creates massive
# setup violations that hurt the router. 3333 ps gives ample timing
# slack so GRT can focus on routability rather than timing-driven detours.
#
# NB: this 300 MHz inherits PR #27's relaxation of the full-chip target.
# A separate 400 MHz path requires #33's clock infrastructure.

current_design mac_array_small

set clk_name    core_clock
set clk_port    clk
set clk_period  3333
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]

# Broadcast feedthrough chains are combinational across the W→E row
# (`assign *_e = *_w` inside each tile). At M=N=4 the chain is 4 hops
# (~140 µm). At M=N=32 it grows to 32 hops (~1100 µm) which will not
# meet a single-cycle path. The contract is that these signals must be
# QUASI-STATIC during their active window (see tech/asap7/TILE_SPEC.md
# § "Broadcast feedthrough timing contract"): reset is held many
# cycles, drain_en/scrub_en pulses span many cycles before the receiver
# samples. Declare false-path on the chain endpoints so STA doesn't
# block on it. (multicycle would also work but false_path is more honest:
# the signals don't have a meaningful single-edge timing window.)
set_false_path -through [get_pins -hierarchical *u_cell/reset_w]
set_false_path -through [get_pins -hierarchical *u_cell/drain_en_w]
set_false_path -through [get_pins -hierarchical *u_cell/drain_slot_w*]
set_false_path -through [get_pins -hierarchical *u_cell/scrub_en_w]
