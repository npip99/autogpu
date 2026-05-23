# Re-emit a leaf macro's LEF with realistic per-layer obstructions, but
# without M6/M7 blocking — so the parent's PDN can use those layers.
#
# Two competing requirements:
#  - Parent's GRT pin-access analyzer needs *real* obstructions on the
#    leaf's internal routing layers (M1..M5). If the LEF reports those
#    layers as free, GRT picks bad access points through metal that is
#    actually occupied internally and bombs with [DRT-0073].
#  - Parent's PDN needs M6/M7 clear of obstructions so it can run power
#    stripes over the macro — otherwise [PDN-0006] "VSS blocked by M6/M7
#    obstructions" stalls floorplan.
#
# Step 1 (this script): write_abstract_lef -bloat_occupied_layers gives
# obstructions on every layer the leaf touched, including the M6/M7 power
# stripes from the platform PDN.
# Step 2 (sed in run.sh): strip M6/M7 OBS blocks from the LEF post-hoc.
#
# Expects env: MODULE_NAME, RESULTS_DIR.
set module $::env(MODULE_NAME)
set results_dir $::env(RESULTS_DIR)

read_lef $::env(TECH_LEF)
foreach lef $::env(SC_LEF) {
  read_lef $lef
}
read_db $results_dir/6_final.odb
write_abstract_lef -bloat_occupied_layers $results_dir/$module.lef
puts "Wrote bloated $results_dir/$module.lef"
