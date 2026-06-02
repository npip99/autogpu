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

# skew_a + skew_b clk feedthrough chains: keep CTS/resizer from inserting
# buffers on the parent-level chain wires (skew_a[i].clk_e → skew_a[i+1].clk_w
# and same for skew_b). Inserting a buffer would un-match the per-hop
# insertion delay that the chain achieves by going through one hardened
# macro per hop. Without this, the resizer's hold-fix pass would chase the
# parent-CTS skew and re-introduce the same -4ns hold WNS this chain is
# designed to eliminate.
set_dont_touch [get_nets clk_chain_a_e*]
set_dont_touch [get_nets clk_chain_a_w*]
set_dont_touch [get_nets clk_chain_b_e*]
set_dont_touch [get_nets clk_chain_b_w*]

# B6 (#40): multicycle path through the skew_a / skew_b broadcast chain.
#
# Each skew_lane_a/b instance has an INTERNAL 260-bit chain register
# (chain_w_s → chain_e_n, registered on clk_w). The chain spans 32
# instances, so u_cmd's push_a_bytes output reaches skew_a[31].chain_w_s
# 32 cycles after u_cmd emits it. The systolic schedule is correct by
# construction.
#
# BUT — ORFS `write_timing_model` produces an abstract skew_lane LEF/.lib
# that does NOT expose the chain register's pin-to-pin sequential arc.
# Parent STA sees chain_w_s → chain_e_n as COMBINATIONAL, treating the
# full u_cmd → skew_a[31] route as a single-cycle path (1500 µm wire).
# The resizer then inserts buffer chains to "fix" this nonexistent
# violation, congesting the W mac boundary at GRT.
#
# set_multicycle_path tells parent STA the truth: the path is 32 cycles
# wide. setup 32, hold 31 (standard shift-register pattern).
#
# TODO before tape-out: replace this constraint with proper abstract-lib
# generation that exposes the chain register's sequential arcs natively.
# Until then, the multicycle is the industry-standard workaround for
# hardened-macro shift registers. See tech/RCA_DISCIPLINE.md.
set_multicycle_path 32 -setup -through [get_pins -hierarchical *u_a/chain_w_s*]
set_multicycle_path 31 -hold  -through [get_pins -hierarchical *u_a/chain_w_s*]
set_multicycle_path 32 -setup -through [get_pins -hierarchical *u_b/chain_w_w*]
set_multicycle_path 31 -hold  -through [get_pins -hierarchical *u_b/chain_w_w*]

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
