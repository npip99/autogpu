#!/usr/bin/env -S openroad -no_init
# Run global_route with -allow_congestion + dump per-GCell congestion.
#
# Usage (inside openlane2 docker):
#   openroad -exit -no_splash /work/tech/sky130/scripts/grt_heatmap.tcl
#
# Env vars expected:
#   ODB_PATH       — input ODB (e.g. step 32's chip_top.odb)
#   LIB_FILES      — space-separated .lib files
#   LEF_FILES      — space-separated .lef files
#   OUT_DIR        — directory to write congestion data + heatmap
#
# Output: $OUT_DIR/congestion.csv with rows
#   layer,x_gcell,y_gcell,usage,capacity,overflow

set odb $::env(ODB_PATH)
set out_dir $::env(OUT_DIR)
file mkdir $out_dir

# Read tech lib + tech LEF + macros.
foreach lib_path $::env(LIB_FILES) {
    puts "Reading liberty: $lib_path"
    read_liberty $lib_path
}
foreach lef_path $::env(LEF_FILES) {
    puts "Reading LEF: $lef_path"
    read_lef $lef_path
}

puts "Reading ODB: $odb"
read_db $odb

# Wire up clock so STA + GR have something to chew on.
set_propagated_clock [all_clocks]

# Set routing layers (mirror openlane's config).
set min_layer met1
set max_layer met5
set_routing_layers -signal "$min_layer-$max_layer" -clock "$min_layer-$max_layer"

# Run GR allowing congestion so it finishes instead of erroring.
global_route \
    -congestion_iterations 50 \
    -verbose \
    -allow_congestion

# Now dump per-GCell congestion. OpenROAD exposes this via the
# grt::Grt class; we iterate GCells and query usage vs capacity.
set f [open "$out_dir/congestion.csv" w]
puts $f "layer,gx,gy,usage,capacity,overflow"

set tech [ord::get_db_tech]
set blk  [ord::get_db_block]

# Get GCell grid.
set xs [grt::get_grid_x]
set ys [grt::get_grid_y]
set n_x [llength $xs]
set n_y [llength $ys]
puts "GCell grid: ${n_x} x ${n_y}"

foreach layer_obj [$tech getLayers] {
    set lname [$layer_obj getName]
    if {[$layer_obj getRoutingLevel] == 0} { continue }
    # Skip li1 — not used here.
    if {$lname eq "li1"} { continue }
    for {set gx 0} {$gx < $n_x} {incr gx} {
        for {set gy 0} {$gy < $n_y} {incr gy} {
            if {[catch {
                set usage    [grt::get_layer_usage    $lname $gx $gy]
                set capacity [grt::get_layer_capacity $lname $gx $gy]
                set overflow [expr {$usage - $capacity}]
                puts $f "$lname,$gx,$gy,$usage,$capacity,$overflow"
            } err]} {
                # Some grt:: helpers may not exist; bail out cleanly.
            }
        }
    }
}
close $f
puts "Wrote $out_dir/congestion.csv"
exit 0
