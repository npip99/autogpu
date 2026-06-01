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

# Broadcast feedthrough chains are combinational W→E rows. Three of
# the four are quasi-static (false-pathed below); drain_en_w is a
# single-cycle snapshot pulse that MUST close. See TILE_SPEC.md
# § "Broadcast feedthrough timing contract" for full reasoning.
#
# At M=N=32 the drain_en chain is 32 hops × ~35 µm = ~1100 µm of M4.
# Letting STA time it surfaces whether the ripple closes inside one
# clock at the target frequency — a real architectural data point #40
# needs (the snapshot pulse cannot be pipelined the way push_a/b was
# in PR #34, because every column must sample the same edge).
set_false_path -through [get_pins -hierarchical *u_cell/reset_w]
set_false_path -through [get_pins -hierarchical *u_cell/drain_slot_w*]
set_false_path -through [get_pins -hierarchical *u_cell/scrub_en_w]

# Block-level I/O false-paths — same scope-shift as compute_array.sdc
# (PR #27 / issue #25). Without these, block-level STA tries to close
# chip-IO-to-internal-flop paths using the unrealistic per-port reference
# clock (no insertion delay at the port, ~ns of insertion at the flop).
# Result: massive bogus hold violations (#40's compute_array_abut_tiny
# 4×4 saw 336 hold viol + 2778 buffers + RSZ-0060 max-buffer-count kill
# at CTS without these). Real IO timing closes at chip_top via the
# abstract .lib's flop-edge-relative arcs — defer to #28.
set io_data_inputs [all_inputs -no_clocks]
set_false_path -hold  -from $io_data_inputs
set_false_path -hold  -to   [all_outputs]
set_false_path -setup -from $io_data_inputs
set_false_path -setup -to   [all_outputs]
