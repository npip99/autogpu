# skew_lane_b pin placement (B6 / #40 pure-abutment chain).
#
# Mirror of skew_lane_a but the chain hops horizontally:
#
#   W edge (x=0)      : chain_w_w[0..259] + clk_w + reset
#                       + per-col push_byte/now/slot/accum taps
#                       M4 horizontal pins; abuts with E edge of skew_b[j-1]
#                       (or driven by cmd_unit's E-edge chain output for j=0)
#   E edge (x=TILE)   : chain_e_e[0..259] + clk_e
#                       M4 horizontal pins; abuts with W edge of skew_b[j+1]
#   N edge (y=TILE)   : edge_byte[0..7] + edge_valid + edge_slot[0..1]
#                       + edge_accum
#                       M5 vertical pins; feeds mac mesh row 0 of col j
#   S edge (y=0)      : empty (skew_b sits at south boundary of compute_array;
#                       chip-IO drain_* pins go here at the parent level)
#
# Abutment invariants:
#   - chain_w_w[k] and chain_e_e[k] share Y across W/E (zero-length parent
#     net when instances stack horizontally — same global Y coordinate)
#   - clk_w and clk_e share Y across W/E

set TILE 30.0
set CHAIN_W 260
set CHAIN_PITCH 0.07
set CHAIN_Y_BASE 0.50

# ---- W/E edges: chain + clk via abutment (M4 horizontal) --------------
for {set k 0} {$k < $CHAIN_W} {incr k} {
    set y [expr $CHAIN_Y_BASE + $k * $CHAIN_PITCH]
    place_pin -pin_name "chain_w_w\[$k\]" -layer M4 -location [list 0.0   $y] -force_to_die_boundary
    place_pin -pin_name "chain_e_e\[$k\]" -layer M4 -location [list $TILE $y] -force_to_die_boundary
}

# clk_w / clk_e: feedthrough, W/E abutment (same Y)
place_pin -pin_name clk_w -layer M4 -location [list 0.0   18.90] -force_to_die_boundary
place_pin -pin_name clk_e -layer M4 -location [list $TILE 18.90] -force_to_die_boundary

# reset is broadcast, only W input
place_pin -pin_name reset -layer M4 -location [list 0.0 19.20] -force_to_die_boundary

# Per-col taps (sliced from chain_w_w at parent level) — also W edge but
# spaced AFTER the chain pins (Y range 19.5-27 µm)
place_pin -pin_name "push_byte\[0\]" -layer M4 -location [list 0.0 19.50] -force_to_die_boundary
place_pin -pin_name "push_byte\[1\]" -layer M4 -location [list 0.0 19.92] -force_to_die_boundary
place_pin -pin_name "push_byte\[2\]" -layer M4 -location [list 0.0 20.34] -force_to_die_boundary
place_pin -pin_name "push_byte\[3\]" -layer M4 -location [list 0.0 20.76] -force_to_die_boundary
place_pin -pin_name "push_byte\[4\]" -layer M4 -location [list 0.0 21.18] -force_to_die_boundary
place_pin -pin_name "push_byte\[5\]" -layer M4 -location [list 0.0 21.60] -force_to_die_boundary
place_pin -pin_name "push_byte\[6\]" -layer M4 -location [list 0.0 22.02] -force_to_die_boundary
place_pin -pin_name "push_byte\[7\]" -layer M4 -location [list 0.0 22.44] -force_to_die_boundary
place_pin -pin_name push_now           -layer M4 -location [list 0.0 22.86] -force_to_die_boundary
place_pin -pin_name "push_slot\[0\]" -layer M4 -location [list 0.0 23.28] -force_to_die_boundary
place_pin -pin_name "push_slot\[1\]" -layer M4 -location [list 0.0 23.70] -force_to_die_boundary
place_pin -pin_name push_accum         -layer M4 -location [list 0.0 24.12] -force_to_die_boundary

# ---- N edge: edge_* outputs (M5 vertical, feed mac mesh row 0) --------
# X coordinates spaced across the macro top edge
place_pin -pin_name "edge_byte\[0\]" -layer M5 -location [list  3.0 $TILE] -force_to_die_boundary
place_pin -pin_name "edge_byte\[1\]" -layer M5 -location [list  4.5 $TILE] -force_to_die_boundary
place_pin -pin_name "edge_byte\[2\]" -layer M5 -location [list  6.0 $TILE] -force_to_die_boundary
place_pin -pin_name "edge_byte\[3\]" -layer M5 -location [list  7.5 $TILE] -force_to_die_boundary
place_pin -pin_name "edge_byte\[4\]" -layer M5 -location [list  9.0 $TILE] -force_to_die_boundary
place_pin -pin_name "edge_byte\[5\]" -layer M5 -location [list 10.5 $TILE] -force_to_die_boundary
place_pin -pin_name "edge_byte\[6\]" -layer M5 -location [list 12.0 $TILE] -force_to_die_boundary
place_pin -pin_name "edge_byte\[7\]" -layer M5 -location [list 13.5 $TILE] -force_to_die_boundary
place_pin -pin_name edge_valid         -layer M5 -location [list 15.0 $TILE] -force_to_die_boundary
place_pin -pin_name "edge_slot\[0\]" -layer M5 -location [list 16.5 $TILE] -force_to_die_boundary
place_pin -pin_name "edge_slot\[1\]" -layer M5 -location [list 18.0 $TILE] -force_to_die_boundary
place_pin -pin_name edge_accum         -layer M5 -location [list 19.5 $TILE] -force_to_die_boundary
