# IR-drop sign-off — invoked under ORFS env (DESIGN_NAME, RESULTS_DIR, etc.).
#
# Reuses ORFS's load_design helper so we get TECH_LEF, SC_LEF, liberty,
# SDC, parasitics (setRC.tcl) for free. Then runs analyze_power_grid with
# both -voltage_file and -error_file for downstream parsing.
source $::env(SCRIPTS_DIR)/util.tcl
source $::env(SCRIPTS_DIR)/load.tcl

# 6_final.sdc isn't always written (e.g. tiny variants where ORFS's
# do-copy rule didn't fire); fall back to 6_1_fill.sdc which is the
# upstream input to final_report and contains identical constraints.
set sdc 6_final.sdc
if {![file exists $::env(RESULTS_DIR)/$sdc]} {
    set sdc 6_1_fill.sdc
}
load_design 6_final.odb $sdc

set_propagated_clock [all_clocks]

# SPEF brings in real parasitics from the routed design. If missing
# (RCX disabled), we already loaded setRC.tcl estimates via load.tcl.
set spef_path $::env(RESULTS_DIR)/6_final.spef
if {[file exists $spef_path]} {
    read_spef $spef_path
    puts "ir_drop: loaded SPEF $spef_path"
} else {
    puts "ir_drop: WARNING no SPEF at $spef_path — using setRC.tcl estimates"
}

# Activity / toggle rate used for switching-power estimation.
# IR_ACTIVITY env var overrides; default = 0.10 (10%). Documented in
# the output report so the assumption is traceable.
set activity 0.10
if {[info exists ::env(IR_ACTIVITY)]} {
    set activity $::env(IR_ACTIVITY)
}
set_power_activity -global -activity $activity
puts "ir_drop: activity=$activity (global toggle rate)"

# Power-supply voltage for IR analysis. For sign-off we use the TYPICAL
# corner — that's what spec budgets are written against. ORFS's $VOLTAGE
# defaults to BC (best-case = 0.77 V on asap7), which understates drop;
# prefer TC_VOLTAGE if defined, then VOLTAGE, then 0.70. IR_VDD overrides.
set vdd 0.70
if {[info exists ::env(VOLTAGE)]}    { set vdd $::env(VOLTAGE) }
if {[info exists ::env(TC_VOLTAGE)]} { set vdd $::env(TC_VOLTAGE) }
if {[info exists ::env(IR_VDD)]}     { set vdd $::env(IR_VDD) }
set vss 0.0
set_pdnsim_net_voltage -net VDD -voltage $vdd
set_pdnsim_net_voltage -net VSS -voltage $vss
puts "ir_drop: VDD=$vdd V  VSS=$vss V"

# Per-instance switching power. -digits 4 keeps watts visible at our
# scale (leaf macros are in the mW range).
puts "ir_drop: -----  report_power  -----"
report_power -digits 4
puts "ir_drop: ---------------------------"

set rep_dir $::env(REPORTS_DIR)
file mkdir $rep_dir

# Per-node voltage_file is needed to identify worst-case node by location.
# error_file captures PSM-0069 (unconnected shape) and PSM-0040 (clean grid).
foreach net {VDD VSS} {
    set voltage_file $rep_dir/${net}_voltage.csv
    set error_file   $rep_dir/${net}_error.rpt
    puts "ir_drop: analyze_power_grid -net $net"
    # If the grid is broken, analyze_power_grid throws; catch so we
    # still write the error file and move on to the other net.
    if {[catch {
        analyze_power_grid -net $net \
            -voltage_file $voltage_file \
            -error_file   $error_file
    } err]} {
        puts "ir_drop: $net analysis FAILED: $err"
        puts "ir_drop: see $error_file for PSM violations (likely PSM-0069)"
    }
}

exit
