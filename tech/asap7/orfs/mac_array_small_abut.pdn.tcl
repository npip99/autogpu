# Custom parent PDN for the 4×4 abutment harness (Phase B of #32).
#
# IMPORTANT: the tile's exposed power pins are on **M6 horizontal**
# (six VDD + six VSS stripes per tile, full tile width). So the parent
# needs **M7 vertical** stripes that pass over the tiles, plus a
# macro_grid M6-M7 connect rule to drop vias where the M7 verticals
# cross the M6 pins. Same A1 pattern (PR #5) but one layer up because
# the tile exposes pins higher than `compute_array`'s leaves did.

add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDD$} -power
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSS$} -ground

set_voltage_domain -name {CORE} -power {VDD} -ground {VSS}

define_pdn_grid -name {top} -voltage_domains {CORE} -pins {M7}

# M1/M2 followpins for stdcells in the perimeter strip.
add_pdn_stripe -grid {top} -layer {M1} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins
add_pdn_stripe -grid {top} -layer {M2} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins

# Perimeter ring on M6 + M7 (horizontal + vertical pair at the chip
# edge).
add_pdn_ring -grid {top} -layers {M6 M7} -widths {0.288 0.288} \
             -spacings {0.096} -core_offset {0.504}

# M5 vertical stripes — needed so M2 followpins in the stdcell strip
# can connect upward through the via stack (M2 → M5 → M6 → M7).
# Without these the M2-M5 connect rule has no M5 shape to land on.
add_pdn_stripe -grid {top} -layer {M5} -width {0.12} -spacing {0.072} \
               -pitch {5.4} -offset {1.5} -extend_to_core_ring

# M7 vertical stripes across the die. These pass over every tile and
# cross each tile's six M6 horizontal power pins; macro_grid below
# drops M6-M7 vias at each crossing.
add_pdn_stripe -grid {top} -layer {M7} -width {0.288} -spacing {0.096} \
               -pitch {5.4} -offset {1.5} -extend_to_core_ring

# Connect via stack so stdcells in the perimeter strip get power too:
# stdcell followpins → M5 → M6 → M7.
add_pdn_connect -grid {top} -layers {M1 M2}
add_pdn_connect -grid {top} -layers {M2 M5}
add_pdn_connect -grid {top} -layers {M5 M6}
add_pdn_connect -grid {top} -layers {M6 M7}

# Macro grid: weld parent M7 stripes to each tile's M6 power pins.
# halo {0 0 0 0} because tiles abut with zero channel.
define_pdn_grid -macro -name {macro_grid} -voltage_domains {CORE} \
    -halo {0 0 0 0} -default
add_pdn_connect -grid {macro_grid} -layers {M6 M7}
