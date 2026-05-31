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
#   - a_in[k] / a_out[k]       share Y across W/E
#   - compute_in / _out        share Y
#   - slot_in[k] / slot_out[k] share Y
#   - accum_in / _out          share Y
#   - b_in[k] / b_out[k]       share X across S/N
#   - drain_out[k] / drain_in[k] share X across S/N (drain flows N→S,
#     so out is on S edge, in is on N edge)
#
# Tile-only extras on N edge: clk, reset, drain_en, drain_slot[0..1],
# scrub_en. Placed east of the drain bus so they don't conflict.

set TILE_W   34.56
set TILE_H   34.56

# ---- W/E edge (M4 horizontal, 12 pins per edge, pitch 2.5 µm) --------
# Y values shared by W (a_in/compute_in/slot_in/accum_in) and
# E (a_out/compute_out/slot_out/accum_out).
set we_y [list 2.5 5.0 7.5 10.0 12.5 15.0 17.5 20.0 22.5 25.0 27.5 30.0]

# Pairs of (W_pin_name, E_pin_name).
set we_pins [list \
    {a_in[0]}     {a_out[0]} \
    {a_in[1]}     {a_out[1]} \
    {a_in[2]}     {a_out[2]} \
    {a_in[3]}     {a_out[3]} \
    {a_in[4]}     {a_out[4]} \
    {a_in[5]}     {a_out[5]} \
    {a_in[6]}     {a_out[6]} \
    {a_in[7]}     {a_out[7]} \
    {compute_in}  {compute_out} \
    {slot_in[0]}  {slot_out[0]} \
    {slot_in[1]}  {slot_out[1]} \
    {accum_in}    {accum_out} \
]

set i 0
foreach {w_pin e_pin} $we_pins {
    set y [lindex $we_y $i]
    place_pin -pin_name $w_pin -layer M4 -location [list 0.0     $y] -force_to_die_boundary
    place_pin -pin_name $e_pin -layer M4 -location [list $TILE_W $y] -force_to_die_boundary
    incr i
}

# ---- S/N edge (M5 vertical) ------------------------------------------
# Shared X positions for S (b_in, drain_out) and N (b_out, drain_in)
# enforce N/S abutment symmetry. North-only extras (clk, reset, drain_en,
# drain_slot, scrub_en) sit east of the drain bus on N edge.

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

# N-only: clk, reset, parent broadcast controls. Placed east of the
# drain bus so they don't conflict with the abutment-symmetric pins.
set n_only [list \
    {clk}           29.4 \
    {reset}         30.1 \
    {drain_en}      30.8 \
    {drain_slot[0]} 31.5 \
    {drain_slot[1]} 32.2 \
    {scrub_en}      32.9 \
]
foreach {pin_name x} $n_only {
    place_pin -pin_name $pin_name -layer M5 -location [list $x $TILE_H] -force_to_die_boundary
}
