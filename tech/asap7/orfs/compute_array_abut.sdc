# Phase C 32×32 abutted compute_array — relaxed SDC for first hardening.
# 3333 ps = 300 MHz, matches mac_array_small_abut.sdc and compute_array's
# proven operating point on the non-abutted layout.
#
# NB: 300 MHz inherits PR #27's full-chip relaxation; 400 MHz is gated on
# #33's clock infrastructure.

current_design compute_array

set clk_name    core_clock
set clk_port    clk
set clk_period  3333
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]

# Broadcast feedthrough chain timing contract (see TILE_SPEC.md):
# reset_w, drain_en_w, drain_slot_w[1:0], scrub_en_w form a 32-hop
# combinational ripple across each row (~1100 µm of M4 wire). They are
# quasi-static during their active window — reset is held many cycles,
# drain_en pulses span many cycles before any cell samples in always_ff.
# Declare false-path so STA doesn't try to close them as single-cycle.
set_false_path -through [get_pins -hierarchical *u_cell/reset_w]
set_false_path -through [get_pins -hierarchical *u_cell/drain_en_w]
set_false_path -through [get_pins -hierarchical *u_cell/drain_slot_w*]
set_false_path -through [get_pins -hierarchical *u_cell/scrub_en_w]
