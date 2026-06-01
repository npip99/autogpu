# Measure the 32-stage clock chain on the existing clk_chain_32 6_final
# database. CTS rebuilt the clock distribution to the FFs but my 32-buffer
# chain instances are still in the placed/routed DEF — they're just dangling
# (their outputs go nowhere useful). We can still time them manually by
# asking OpenSTA for the propagated delay from chain[0] to chain[31].

read_lef /OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7_tech_1x_201209.lef
read_lef /OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7sc7p5t_28_R_1x_220121a.lef
read_liberty /OpenROAD-flow-scripts/flow/platforms/asap7/lib/NLDM/asap7sc7p5t_AO_RVT_FF_nldm_211120.lib.gz
read_liberty /OpenROAD-flow-scripts/flow/platforms/asap7/lib/NLDM/asap7sc7p5t_INVBUF_RVT_FF_nldm_220122.lib.gz
read_liberty /OpenROAD-flow-scripts/flow/platforms/asap7/lib/NLDM/asap7sc7p5t_OA_RVT_FF_nldm_211120.lib.gz
read_liberty /OpenROAD-flow-scripts/flow/platforms/asap7/lib/NLDM/asap7sc7p5t_SIMPLE_RVT_FF_nldm_211120.lib.gz
read_liberty /OpenROAD-flow-scripts/flow/platforms/asap7/lib/NLDM/asap7sc7p5t_SEQ_RVT_FF_nldm_220123.lib

read_db /work/build/orfs/results/asap7/clk_chain_32/base/6_final.odb
read_sdc /work/build/orfs/results/asap7/clk_chain_32/base/6_final.sdc
source /OpenROAD-flow-scripts/flow/platforms/asap7/setRC.tcl
read_spef /work/build/orfs/results/asap7/clk_chain_32/base/6_final.spef
set_propagated_clock [all_clocks]

# Locate the chain. Yosys preserves instance names with "g_buf[N].u_buf".
# In the final ODB names might be flattened — try a few patterns.
puts "\n=== Looking for chain buffers ==="
set chain_insts [get_cells -hier *u_buf*]
puts "Found [llength $chain_insts] chain buffer instances"

if {[llength $chain_insts] == 0} {
    # Try with escaped brackets
    set chain_insts [get_cells -hier "g_buf*u_buf"]
    puts "Retry with g_buf*: [llength $chain_insts]"
}

# Dump each buffer's input/output pin positions
puts "\n=== Per-buffer placement + per-stage clock delay ==="
foreach inst [lsort -dictionary $chain_insts] {
    set name [get_full_name $inst]
    set bbox [$inst getBBox]
    set x [expr ([$bbox xMin] + [$bbox xMax]) / 2.0 / 1000.0]
    puts [format "%s @ x=%.2f µm" $name $x]
}

# Measure full chain insertion: report timing for any path that touches
# the chain end.
puts "\n=== Try report_check_types on chain[31] ==="
catch {report_check_types -from {g_buf[31].u_buf/A} -through {g_buf[31].u_buf/Y}} msg
puts $msg

puts "\n=== Done ==="
