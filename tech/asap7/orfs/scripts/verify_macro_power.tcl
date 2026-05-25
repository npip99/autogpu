# verify_macro_power.tcl — physical sanity-check for hardened-macro PDN connectivity.
#
# Usage (inside ORFS docker, after a route step has written 6_final.odb):
#   openroad -exit -db <odb_path> verify_macro_power.tcl
#
# Walks every BLOCK (hardened-macro) instance in the post-route ODB. For
# each VDD/VSS pin shape on the instance, projects it into placed
# coordinates and checks that at least one parent power-net special-wire
# shape (stripe or via face) overlaps it on the same layer.
#
# This catches the failure mode where PSM-0069's complaint is real:
# pdngen never welded the parent stripe to the macro pin (layer mismatch,
# pitch off-by-one, missing connect rule). If pdngen did its job, every
# macro pin will be overlapped.
#
# Exit 0 = every macro power pin is covered by a parent power-net shape.
# Exit 1 = at least one macro pin has no overlap (real PDN bug).
# Exit 2 = bad invocation.

set block [[[ord::get_db] getChip] getBlock]
if {$block == "NULL"} {
    puts "ERROR: no block loaded. Invoke as: openroad -exit -db <odb> verify_macro_power.tcl"
    exit 2
}

# Index parent power-net special-wire shapes by "layer,net".
array set shapes {}
set indexed 0
foreach net_name {VDD VSS} {
    set net [$block findNet $net_name]
    if {$net == "NULL"} continue
    foreach swire [$net getSWires] {
        foreach sbox [$swire getWires] {
            set xlo [$sbox xMin] ; set ylo [$sbox yMin]
            set xhi [$sbox xMax] ; set yhi [$sbox yMax]
            if {[$sbox isVia]} {
                set via [$sbox getTechVia]
                if {$via == "NULL"} { set via [$sbox getBlockVia] }
                if {$via == "NULL"} { continue }
                set layer_list [list [$via getTopLayer] [$via getBottomLayer]]
            } else {
                set layer_list [list [$sbox getTechLayer]]
            }
            foreach layer $layer_list {
                set k "[$layer getName],$net_name"
                lappend shapes($k) [list $xlo $ylo $xhi $yhi]
                incr indexed
            }
        }
    }
}
puts "indexed $indexed parent power shapes across [array size shapes] layer/net buckets:"
foreach k [lsort [array names shapes]] {
    puts "  $k: [llength $shapes($k)] shapes"
}

set ok 0
set fail 0
set fail_lines {}
foreach inst [$block getInsts] {
    if {[[$inst getMaster] getType] != "BLOCK"} continue
    set iname [$inst getName]
    foreach iterm [$inst getITerms] {
        set net [$iterm getNet]
        if {$net == "NULL"} continue
        set nname [$net getName]
        if {$nname != "VDD" && $nname != "VSS"} continue
        foreach pg [$iterm getGeometries] {
            set layer [lindex $pg 0]
            set rect  [lindex $pg 1]
            set lname [$layer getName]
            set xlo [$rect xMin] ; set ylo [$rect yMin]
            set xhi [$rect xMax] ; set yhi [$rect yMax]
            set covered 0
            if {[info exists shapes($lname,$nname)]} {
                foreach s $shapes($lname,$nname) {
                    lassign $s ax ay bx by
                    if {$ax < $xhi && $bx > $xlo && $ay < $yhi && $by > $ylo} {
                        set covered 1
                        break
                    }
                }
            }
            if {$covered} {
                incr ok
            } else {
                incr fail
                lappend fail_lines "FAIL: $iname pin on $lname ($nname) bbox=($xlo $ylo $xhi $yhi)"
            }
        }
    }
}

puts ""
foreach l [lrange $fail_lines 0 19] { puts $l }
if {[llength $fail_lines] > 20} {
    puts "... and [expr {[llength $fail_lines] - 20}] more"
}
puts ""
puts "verify_macro_power: ok=$ok fail=$fail"
if {$fail > 0} { exit 1 } else { exit 0 }
