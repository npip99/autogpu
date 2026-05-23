# compute_array PDN — stripes laid only in inter-macro channels.
#
# Floorplan geometry from gen_compute_array_floorplan.py:
#   mac grid origin       (mac_x0, mac_y0) = (126.27, 126.27) µm
#   mac pitch             45.0 µm     (rows + cols both)
#   mac size              34.543 µm   → channel width = 10.457 µm
#   channel centers (x or y):
#     126.27 + i*45 + 34.543/2 + 10.457/2  for i=0..30
#     = 168.54 + i*45
#
# Strategy: vertical M6 stripes at every channel center, horizontal M7
# stripes at every channel center. Each stripe runs through 32 µm of open
# die (the channel + a sliver into adjacent rings). They never overlap
# any macro footprint, so pdngen does no halo clipping (the slow path on
# BLOCKS_grid_strategy.tcl). Vias drop at every M6/M7 intersection inside
# the channels and at leaf-macro M6 power-pin crossings.

add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDD$} -power
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSS$} -ground

set_voltage_domain -name {CORE} -power {VDD} -ground {VSS}

define_pdn_grid -name {top} -voltage_domains {CORE} -pins {M6 M7}

add_pdn_ring   -grid {top} -layers {M6 M7} -widths {0.544 0.544} \
               -spacings {0.096} -core_offset {0.504}

# Stripes in the channels between macro columns (M6 vertical) and rows
# (M7 horizontal). Pitch = mac grid pitch, offset = channel center.
add_pdn_stripe -grid {top} -layer {M6} -width {0.288} -spacing {0.096} \
               -pitch {45.0} -offset {168.54} -extend_to_core_ring
add_pdn_stripe -grid {top} -layer {M7} -width {0.288} -spacing {0.096} \
               -pitch {45.0} -offset {168.54} -extend_to_core_ring

# Connect: M6↔M7 at every channel intersection (top grid internal)
# AND wherever a top-level M7 stripe crosses a leaf macro's M6 power pin
# (leaf macros expose VDD/VSS pins on M6 — see e.g. skew_lane_a.lef).
add_pdn_connect -grid {top} -layers {M6 M7}
