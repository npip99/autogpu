# chip_top PDN — simple grid plus M1/M2 followpins.
#
# chip_top has only ~37 macros (1 compute_array + 4 sub-block macros +
# 32 fakeram banks), so ORFS's stock BLOCKS_grid_strategy.tcl O(stripes ×
# macros) cost is fine here (unlike compute_array's 1089 macros). But
# the asap7 default `BLOCKS_grid_strategy.tcl` config caused PDN-0006
# blocking errors during early experiments, so we ship our own minimal
# PDN here for predictability.
#
# Known limitation (A1 / PSM-0069): this PDN does NOT include a
# `-macro` grid that welds the parent stripes to the hardened-leaf
# VDD/VSS pins. The same bug present in compute_array.pdn.tcl recurs
# here. On `verify_macro_power.tcl` this will report ~37 macros with
# zero overlapping parent power-net shapes. Will be fixed when A1 lands
# (the same fix applies at both compute_array and chip_top scopes —
# define_pdn_grid -macro with add_pdn_connect to weld stripes to the
# leaf-macro VDD/VSS pins). For now: documented and accepted per
# tech/asap7/problems/A6_chip_top.md.

add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDD$} -power
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSS$} -ground

set_voltage_domain -name {CORE} -power {VDD} -ground {VSS}

define_pdn_grid -name {top} -voltage_domains {CORE} -pins {M6 M7}

# M1 + M2 followpins for chip-level stdcells (smem/store glue + inlined
# RTL from cmdproc/load/barrier instances). Required: stdcell rails wire
# back to the M5/M6/M7 grid through these.
add_pdn_stripe -grid {top} -layer {M1} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins
add_pdn_stripe -grid {top} -layer {M2} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins

# Power ring on the perimeter — M6 horizontal, M7 vertical (per asap7
# routing direction conventions).
add_pdn_ring -grid {top} -layers {M6 M7} -widths {0.544 0.544} \
             -spacings {0.096} -core_offset {0.504}

# Vertical M5/M6 stripes + horizontal M7 stripes at a coarse pitch — the
# chip_top die is only ~750×800 µm and macro placement leaves multiple
# wide channels, so a 60 µm pitch covers everything with margin.
add_pdn_stripe -grid {top} -layer {M5} -width {0.120} -spacing {0.096} \
               -pitch {60.0} -offset {10.0} -extend_to_core_ring
add_pdn_stripe -grid {top} -layer {M6} -width {0.288} -spacing {0.096} \
               -pitch {60.0} -offset {10.0} -extend_to_core_ring
add_pdn_stripe -grid {top} -layer {M7} -width {0.288} -spacing {0.096} \
               -pitch {60.0} -offset {10.0} -extend_to_core_ring

add_pdn_connect -grid {top} -layers {M1 M2}
add_pdn_connect -grid {top} -layers {M2 M5}
add_pdn_connect -grid {top} -layers {M5 M6}
add_pdn_connect -grid {top} -layers {M6 M7}
