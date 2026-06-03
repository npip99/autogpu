# store PDN — power grid + macro_grid welding for 4 tile_buf_8row banks.
#
# tile_buf_8row exposes VDD/VSS pins on M6 (same convention as smem_bank).
# The default ORFS BLOCKS_grid_strategy does NOT include macro_grid welding
# rules, so without this file the tile_buf_8row instances ship with floating
# VDD/VSS pins (PDN-0233 / PSM-0069).
#
# Pattern lifted from tech/asap7/orfs/smem.pdn.tcl — same VDD/VSS layer
# (M6) and same macro_grid -default approach.

add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDD$} -power
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSS$} -ground

set_voltage_domain -name {CORE} -power {VDD} -ground {VSS}

define_pdn_grid -name {top} -voltage_domains {CORE} -pins {M6 M7}

# M1 + M2 followpins for any stdcells outside the banks (drain FSM,
# beat sequencer, dtype conversion, mc_wr generator).
add_pdn_stripe -grid {top} -layer {M1} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins
add_pdn_stripe -grid {top} -layer {M2} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins

# Power ring on perimeter.
add_pdn_ring -grid {top} -layers {M6 M7} -widths {0.544 0.544} \
             -spacings {0.096} -core_offset {0.504}

# M5/M6/M7 stripes — generic pitch (no specific bank-pitch alignment
# needed for store; only 4 banks vs smem's 16).
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

# Macro PDN grid — welds the parent stripes to each tile_buf_8row's
# M6 VDD/VSS pins. Same fix from smem.pdn.tcl / compute_array.pdn.tcl.
define_pdn_grid -macro -name {macro_grid} -voltage_domains {CORE} \
    -halo {2.0 2.0 2.0 2.0} -default
add_pdn_connect -grid {macro_grid} -layers {M5 M6}
add_pdn_connect -grid {macro_grid} -layers {M6 M7}
