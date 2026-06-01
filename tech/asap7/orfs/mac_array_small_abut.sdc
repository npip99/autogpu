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
# (`assign *_e = *_w` inside each tile). Three of the four signals are
# QUASI-STATIC during their active window (see tech/asap7/TILE_SPEC.md
# § "Broadcast feedthrough timing contract") and are false-pathed here:
#
#   - reset_w     : held many cycles by reset_seq
#   - drain_slot_w: constant for the whole drain op (drain_saved_slot)
#   - scrub_en_w  : multi-cycle scrub window
#
# drain_en_w is INTENTIONALLY NOT false-pathed. It's a single-cycle
# snapshot pulse: cmd_unit's D_IDLE→D_PULSE asserts it for exactly one
# clock, every cell must capture it on the SAME edge to load
# storage[drain_slot] simultaneously, then data shifts north over
# MMA_M cycles. Disabling STA on drain_en would mask a real
# 32-column functional hazard — let STA time it as a normal single-cycle
# path. At M=N=4 (this harness) it closes easily; at M=N=32 it likely
# won't, which is genuine information #40 must act on.
set_false_path -through [get_pins -hierarchical *u_cell/reset_w]
set_false_path -through [get_pins -hierarchical *u_cell/drain_slot_w*]
set_false_path -through [get_pins -hierarchical *u_cell/scrub_en_w]
