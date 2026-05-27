# smem PDN — channel-aligned grid + macro_grid welding rules for the
# 32 smem_bank hardened-leaf macros.
#
# Each smem_bank exposes VDD/VSS pins on M6. The parent grid here lays
# M5/M6/M7 stripes in the inter-bank channels and welds them to the
# leaf pins via the macro_grid. Same pattern as compute_array.pdn.tcl
# post-A1.
#
# Without the macro_grid welding rules, smem_bank instances ship with
# floating VDD/VSS pins (PSM-0069). Without channel-aligned stripes,
# ORFS's stock BLOCKS_grid_strategy.tcl is O(stripes × macros) and
# chokes on multi-macro designs.

add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDD$} -power
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSS$} -ground

set_voltage_domain -name {CORE} -power {VDD} -ground {VSS}

define_pdn_grid -name {top} -voltage_domains {CORE} -pins {M6 M7}

# M1 + M2 followpins for any stdcells outside the bank macros (smem's
# arbitration, OR-tree, write-forwarding logic).
add_pdn_stripe -grid {top} -layer {M1} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins
add_pdn_stripe -grid {top} -layer {M2} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins

# Power ring on perimeter.
add_pdn_ring -grid {top} -layers {M6 M7} -widths {0.544 0.544} \
             -spacings {0.096} -core_offset {0.504}

# M5/M6/M7 stripes — pitch matches smem_bank pitch (80 µm = 60 macro
# + 20 channel) so every other stripe lands in a channel between banks.
add_pdn_stripe -grid {top} -layer {M5} -width {0.120} -spacing {0.096} \
               -pitch {40.0} -offset {10.0} -extend_to_core_ring
add_pdn_stripe -grid {top} -layer {M6} -width {0.288} -spacing {0.096} \
               -pitch {40.0} -offset {10.0} -extend_to_core_ring
add_pdn_stripe -grid {top} -layer {M7} -width {0.288} -spacing {0.096} \
               -pitch {40.0} -offset {10.0} -extend_to_core_ring

add_pdn_connect -grid {top} -layers {M1 M2}
add_pdn_connect -grid {top} -layers {M2 M5}
add_pdn_connect -grid {top} -layers {M5 M6}
add_pdn_connect -grid {top} -layers {M6 M7}

# Macro PDN grid — welds the parent stripes to each smem_bank's M6
# VDD/VSS pins. Same A1 fix from compute_array.pdn.tcl.
define_pdn_grid -macro -name {macro_grid} -voltage_domains {CORE} \
    -halo {2.0 2.0 2.0 2.0} -default
add_pdn_connect -grid {macro_grid} -layers {M5 M6}
add_pdn_connect -grid {macro_grid} -layers {M6 M7}
