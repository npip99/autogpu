# Re-emit a LEF + timing LIB from an existing 6_final.odb without rerunning
# the rest of the flow. Used for chip_top integration when the original
# build's `generate_abstract` step was skipped or failed (e.g. PSM-0069
# at the final report, after the layout itself completed).
#
# Mirrors what ORFS's flow/scripts/generate_abstract.tcl does, minus the
# SDC-based clock setup that requires a fresh load. We load the ODB,
# read SPEF + SDC from the same directory, then write the abstract LEF
# (bloated obstructions for parent GRT) and the timing LIB.
#
# Expects env: MODULE_NAME, RESULTS_DIR.

set module $::env(MODULE_NAME)
set results_dir $::env(RESULTS_DIR)

read_lef $::env(TECH_LEF)
foreach lef $::env(SC_LEF) {
  read_lef $lef
}

# Liberty for write_timing_model — it needs library data to characterize
# cell delays in the abstract model.
foreach lib $::env(LIBERTY_FILES) {
  read_liberty $lib
}

# Pull in any leaf macros the module instantiated (compute_array → mac_tmem_cell etc.).
if { [info exists ::env(EXTRA_LEFS)] && $::env(EXTRA_LEFS) ne "" } {
  foreach lef $::env(EXTRA_LEFS) {
    read_lef $lef
  }
}
if { [info exists ::env(EXTRA_LIBS)] && $::env(EXTRA_LIBS) ne "" } {
  foreach lib $::env(EXTRA_LIBS) {
    read_liberty $lib
  }
}

read_db $results_dir/6_final.odb

# SDC + SPEF for timing model.
if { [file exists $results_dir/6_final.sdc] } {
  read_sdc $results_dir/6_final.sdc
}
if { [file exists $results_dir/6_final.spef] } {
  read_spef $results_dir/6_final.spef
}

set_propagated_clock [all_clocks]
set_clock_latency -source 0 [all_clocks]

write_timing_model $results_dir/${module}_typ.lib
write_abstract_lef -bloat_occupied_layers $results_dir/$module.lef
puts "Wrote $results_dir/$module.lef and ${module}_typ.lib"
