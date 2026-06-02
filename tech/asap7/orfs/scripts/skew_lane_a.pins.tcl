# skew_lane_a pin placement (B6 / #40 pure-abutment chain).
#
# Sourced by ORFS during floorplan via IO_CONSTRAINTS.
#
# Geometry (skew_lane_a sits vertically stacked in the W column of
# compute_array_abut — abutment is S↔N):
#
#   S edge (y=0)        : chain_w_s[0..259] + clk_w + reset
#                         M5 vertical pins; abuts with N edge of skew_a[i-1]
#   N edge (y=TILE)     : chain_e_n[0..259] + clk_e
#                         M5 vertical pins; abuts with S edge of skew_a[i+1]
#   W edge (x=0)        : push_byte[0..7] + push_now + push_slot[0..1]
#                         + push_accum
#                         M4 horizontal pins; these are PARENT-LEVEL slice
#                         taps from chain_w_s, NOT chain-abutted
#   E edge (x=TILE)     : edge_byte[0..7] + edge_valid + edge_slot[0..1]
#                         + edge_accum
#                         M4 horizontal pins; feeds mac mesh col 0 of row i
#
# Abutment invariants enforced here:
#   - chain_w_s[k] and chain_e_n[k] share X across S/N (zero-length parent
#     net when instances stack vertically — same global X coordinate)
#   - clk_w and clk_e share X across S/N (clk feedthrough by abutment)
#
# Pin X coordinates (S/N):
#   chain[0..259] at x = 0.5 + k*0.07  (range 0.50..18.63 µm)
#   clk pair      at x = 18.90
#   reset (S only) at x = 19.20

set TILE 30.0
set CHAIN_W 260
set CHAIN_PITCH 0.07
set CHAIN_X_BASE 0.50

# ---- S/N edges: chain + clk via abutment (M5 vertical) ----------------
for {set k 0} {$k < $CHAIN_W} {incr k} {
    set x [expr $CHAIN_X_BASE + $k * $CHAIN_PITCH]
    place_pin -pin_name "chain_w_s\[$k\]" -layer M5 -location [list $x 0.0]   -force_to_die_boundary
    place_pin -pin_name "chain_e_n\[$k\]" -layer M5 -location [list $x $TILE] -force_to_die_boundary
}

# clk_w / clk_e: feedthrough, S/N abutment (same X)
place_pin -pin_name clk_w -layer M5 -location [list 18.90 0.0]   -force_to_die_boundary
place_pin -pin_name clk_e -layer M5 -location [list 18.90 $TILE] -force_to_die_boundary

# reset is broadcast, only need S input (no abutment chain for reset)
place_pin -pin_name reset -layer M5 -location [list 19.20 0.0] -force_to_die_boundary

# ---- W edge: per-row taps + push family (M4 horizontal) ---------------
# Parent compute_array.sv slices chain_w_s[i*8 +: 8] → push_byte at the
# south edge area (parent M3 routing). These pins are the destination of
# that slice within each skew_a instance.
place_pin -pin_name "push_byte\[0\]" -layer M4 -location [list 0.0  3.0] -force_to_die_boundary
place_pin -pin_name "push_byte\[1\]" -layer M4 -location [list 0.0  4.5] -force_to_die_boundary
place_pin -pin_name "push_byte\[2\]" -layer M4 -location [list 0.0  6.0] -force_to_die_boundary
place_pin -pin_name "push_byte\[3\]" -layer M4 -location [list 0.0  7.5] -force_to_die_boundary
place_pin -pin_name "push_byte\[4\]" -layer M4 -location [list 0.0  9.0] -force_to_die_boundary
place_pin -pin_name "push_byte\[5\]" -layer M4 -location [list 0.0 10.5] -force_to_die_boundary
place_pin -pin_name "push_byte\[6\]" -layer M4 -location [list 0.0 12.0] -force_to_die_boundary
place_pin -pin_name "push_byte\[7\]" -layer M4 -location [list 0.0 13.5] -force_to_die_boundary
place_pin -pin_name push_now           -layer M4 -location [list 0.0 15.0] -force_to_die_boundary
place_pin -pin_name "push_slot\[0\]" -layer M4 -location [list 0.0 16.5] -force_to_die_boundary
place_pin -pin_name "push_slot\[1\]" -layer M4 -location [list 0.0 18.0] -force_to_die_boundary
place_pin -pin_name push_accum         -layer M4 -location [list 0.0 19.5] -force_to_die_boundary

# ---- E edge: edge_* outputs (M4 horizontal, feed mac mesh col 0) ------
# Y coordinates match chain_w_s tap byte range (rows 0..7 of macro,
# accessible from the bottom of the macro where chain slice lands).
place_pin -pin_name "edge_byte\[0\]" -layer M4 -location [list $TILE  3.0] -force_to_die_boundary
place_pin -pin_name "edge_byte\[1\]" -layer M4 -location [list $TILE  4.5] -force_to_die_boundary
place_pin -pin_name "edge_byte\[2\]" -layer M4 -location [list $TILE  6.0] -force_to_die_boundary
place_pin -pin_name "edge_byte\[3\]" -layer M4 -location [list $TILE  7.5] -force_to_die_boundary
place_pin -pin_name "edge_byte\[4\]" -layer M4 -location [list $TILE  9.0] -force_to_die_boundary
place_pin -pin_name "edge_byte\[5\]" -layer M4 -location [list $TILE 10.5] -force_to_die_boundary
place_pin -pin_name "edge_byte\[6\]" -layer M4 -location [list $TILE 12.0] -force_to_die_boundary
place_pin -pin_name "edge_byte\[7\]" -layer M4 -location [list $TILE 13.5] -force_to_die_boundary
place_pin -pin_name edge_valid         -layer M4 -location [list $TILE 15.0] -force_to_die_boundary
place_pin -pin_name "edge_slot\[0\]" -layer M4 -location [list $TILE 16.5] -force_to_die_boundary
place_pin -pin_name "edge_slot\[1\]" -layer M4 -location [list $TILE 18.0] -force_to_die_boundary
place_pin -pin_name edge_accum         -layer M4 -location [list $TILE 19.5] -force_to_die_boundary
