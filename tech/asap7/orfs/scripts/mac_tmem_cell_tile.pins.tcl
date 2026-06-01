# Abutment-ready IO pin placement for mac_tmem_cell_tile (issue #32).
#
# Sourced by ORFS during floorplan via IO_CONSTRAINTS. Implements the
# boundary contract in tech/asap7/TILE_SPEC.md:
#
#   - W edge pins on M4 horizontal at x=0
#   - E edge pins on M4 horizontal at x=tile_w     (abutment-symmetric Y)
#   - S edge pins on M5 vertical   at y=0
#   - N edge pins on M5 vertical   at y=tile_h     (abutment-symmetric X)
#
# Abutment invariants enforced here:
#   - a_in[k] / a_out[k]                share Y across W/E
#   - compute_in / compute_out          share Y
#   - slot_in[k] / slot_out[k]          share Y
#   - accum_in / accum_out              share Y
#   - reset_w / reset_e                 share Y       (broadcast feedthrough)
#   - drain_en_w / drain_en_e           share Y       (broadcast feedthrough)
#   - drain_slot_w[k] / drain_slot_e[k] share Y       (broadcast feedthrough)
#   - scrub_en_w / scrub_en_e           share Y       (broadcast feedthrough)
#   - b_in[k] / b_out[k]                share X across S/N
#   - drain_out[k] / drain_in[k]        share X across S/N (drain flows N→S)
#
# N-only: clk (single pin; needs a real clock tree at parent, see #33).

set TILE_W   34.56
set TILE_H   34.56

# ---- W/E edge (M4 horizontal) ----------------------------------------
# 12 datapath pairs at y=2.5..30.0 (pitch 2.5 µm) + 5 broadcast
# feedthrough pairs at y=30.7..33.5 (pitch 0.7 µm). All pairs share Y
# between W (input) and E (output) so abutment forms continuous nets.
set we_pins [list \
    {a_in[0]}       {a_out[0]}       2.5  \
    {a_in[1]}       {a_out[1]}       5.0  \
    {a_in[2]}       {a_out[2]}       7.5  \
    {a_in[3]}       {a_out[3]}      10.0  \
    {a_in[4]}       {a_out[4]}      12.5  \
    {a_in[5]}       {a_out[5]}      15.0  \
    {a_in[6]}       {a_out[6]}      17.5  \
    {a_in[7]}       {a_out[7]}      20.0  \
    {compute_in}    {compute_out}   22.5  \
    {slot_in[0]}    {slot_out[0]}   25.0  \
    {slot_in[1]}    {slot_out[1]}   27.5  \
    {accum_in}      {accum_out}     30.0  \
    {reset_w}       {reset_e}       30.7  \
    {drain_en_w}    {drain_en_e}    31.4  \
    {drain_slot_w[0]} {drain_slot_e[0]} 32.1  \
    {drain_slot_w[1]} {drain_slot_e[1]} 32.8  \
    {scrub_en_w}    {scrub_en_e}    33.5  \
]

foreach {w_pin e_pin y} $we_pins {
    place_pin -pin_name $w_pin -layer M4 -location [list 0.0     $y] -force_to_die_boundary
    place_pin -pin_name $e_pin -layer M4 -location [list $TILE_W $y] -force_to_die_boundary
}

# ---- S/N edge (M5 vertical) ------------------------------------------
# Shared X positions for S (b_in, drain_out) and N (b_out, drain_in)
# enforce N/S abutment symmetry. N-only: clk (deferred to #33 clock infra).

# b[k]: shared X for b_in[k] (S) and b_out[k] (N).
for {set k 0} {$k < 8} {incr k} {
    set x [expr 0.7 + $k * 0.7]
    place_pin -pin_name "b_in\[$k\]"  -layer M5 -location [list $x 0.0]    -force_to_die_boundary
    place_pin -pin_name "b_out\[$k\]" -layer M5 -location [list $x $TILE_H] -force_to_die_boundary
}

# drain[k]: shared X for drain_out[k] (S) and drain_in[k] (N).
# Drain flows N→S, so drain_in comes from north and drain_out leaves south.
for {set k 0} {$k < 32} {incr k} {
    set x [expr 7.0 + $k * 0.7]
    place_pin -pin_name "drain_out\[$k\]" -layer M5 -location [list $x 0.0]    -force_to_die_boundary
    place_pin -pin_name "drain_in\[$k\]"  -layer M5 -location [list $x $TILE_H] -force_to_die_boundary
}

# N-only: clk. (Other former broadcasts moved to W/E feedthrough above.)
place_pin -pin_name clk -layer M5 -location [list 29.4 $TILE_H] -force_to_die_boundary
