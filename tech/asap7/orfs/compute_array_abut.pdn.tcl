# Phase C 32×32 abutted compute_array PDN.
#
# Same pattern as mac_array_small_abut.pdn.tcl scaled up:
#   - Tile exposes M6-horizontal power pins (followpin stripes)
#   - Parent runs M7 vertical stripes at 5.4 µm pitch over the entire die
#   - macro_grid M6-M7 connect rule welds parent → tile power
#   - Perimeter stdcell strip needs M1/M2 followpins + M5 verticals
#     bridging up to M6/M7

add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDD$} -power
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSS$} -ground

set_voltage_domain -name {CORE} -power {VDD} -ground {VSS}

define_pdn_grid -name {top} -voltage_domains {CORE} -pins {M7}

# M1/M2 followpins for the perimeter stdcell strip (broadcast chain
# drivers in col 0, b-edge byte drivers in row 0, etc.).
add_pdn_stripe -grid {top} -layer {M1} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins
add_pdn_stripe -grid {top} -layer {M2} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins

# Perimeter ring on M6 + M7.
add_pdn_ring -grid {top} -layers {M6 M7} -widths {0.288 0.288} \
             -spacings {0.096} -core_offset {0.504}

# M5 vertical stripes — bridge M2 followpins up to M6/M7.
add_pdn_stripe -grid {top} -layer {M5} -width {0.12} -spacing {0.072} \
               -pitch {5.4} -offset {1.5} -extend_to_core_ring

# M7 vertical stripes across the die. Pass over every tile and cross
# each tile's six M6 horizontal power pins; macro_grid welds via at
# each crossing.
add_pdn_stripe -grid {top} -layer {M7} -width {0.288} -spacing {0.096} \
               -pitch {5.4} -offset {1.5} -extend_to_core_ring

# Via stack: stdcell followpins → M5 → M6 → M7.
add_pdn_connect -grid {top} -layers {M1 M2}
add_pdn_connect -grid {top} -layers {M2 M5}
add_pdn_connect -grid {top} -layers {M5 M6}
add_pdn_connect -grid {top} -layers {M6 M7}

# Macro grid: weld parent M7 stripes to each tile's M6 power pins.
# halo {0 0 0 0} because tiles abut with zero channel.
define_pdn_grid -macro -name {macro_grid} -voltage_domains {CORE} \
    -halo {0 0 0 0} -default
add_pdn_connect -grid {macro_grid} -layers {M6 M7}
