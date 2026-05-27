# compute_array PDN — stripes laid only in inter-macro channels.
#
# Floorplan geometry from gen_compute_array_floorplan.py:
#   mac grid origin       (mac_x0, mac_y0) = (126.27, 126.27) µm
#   mac pitch             55.0 µm     (rows + cols both)
#   mac size              34.543 µm   → channel width = 20.457 µm
#   channel centers (x or y):
#     126.27 + i*55 + 34.543/2 + 20.457/2  for i=0..30
#     = 173.54 + i*55
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

# M1 + M2 followpins for chip-level stdcells (tap, decap, endcap, hold
# buffers ORFS scatters around macros). Required: without them, tap cells
# at the die perimeter have no power and PSM-0069 fails.
#
# Leaf macro LEFs strip M1+M2+M6+M7 obstructions (see strip_lef_obs_layers
# in run.sh) so these followpins can extend through inter-mac channels
# without PDN-0179 fragmentation.
add_pdn_stripe -grid {top} -layer {M1} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins
add_pdn_stripe -grid {top} -layer {M2} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins

add_pdn_ring   -grid {top} -layers {M6 M7} -widths {0.544 0.544} \
               -spacings {0.096} -core_offset {0.504}

# Intermediate M5 stripes plus M6/M7 channel-aligned stripes. Pitch 27.5
# (half mac pitch) so stripes hit both inter-mac channels and perimeter
# band. Leaves have M1+M2+M5+M6+M7 OBS stripped so any of these can
# pass over a macro without conflict.
add_pdn_stripe -grid {top} -layer {M5} -width {0.120} -spacing {0.096} \
               -pitch {27.5} -offset {8.54} -extend_to_core_ring
add_pdn_stripe -grid {top} -layer {M6} -width {0.288} -spacing {0.096} \
               -pitch {27.5} -offset {8.54} -extend_to_core_ring
add_pdn_stripe -grid {top} -layer {M7} -width {0.288} -spacing {0.096} \
               -pitch {27.5} -offset {8.54} -extend_to_core_ring

# Connect rules — full via stack so pdngen can route M2 followpin → M6 grid.
add_pdn_connect -grid {top} -layers {M1 M2}     ;# stdcell rail crossings
add_pdn_connect -grid {top} -layers {M2 M5}     ;# followpin up to intermediate
add_pdn_connect -grid {top} -layers {M5 M6}     ;# intermediate to channel grid
add_pdn_connect -grid {top} -layers {M6 M7}     ;# channel intersections

# Macro PDN grid — welds parent stripes to hardened-leaf VDD/VSS pins.
# Leaf abstract LEFs expose power pins on M6 as horizontal stripes at
# y = mac_y + {2.529, 2.913, 7.929, 8.313, ...} (5.4 µm period), spanning
# the full macro x-width (~2.05 to 32.51). Parent verticals (M5 at x =
# 8.54+k*27.5 and M7 at the same x-positions) cross over each macro at
# least once and drop M5/M6 and M6/M7 vias onto every leaf pin row.
#
# Without this -macro grid + connect rules, pdngen treats leaf macros as
# unrelated geometry and never welds parent power to leaf pins
# (PSM-0069). See tech/asap7/problems/A1_pdn_macro_grid.md.
define_pdn_grid -macro -name {macro_grid} -voltage_domains {CORE} \
    -halo {2.0 2.0 2.0 2.0} -default
add_pdn_connect -grid {macro_grid} -layers {M5 M6}
add_pdn_connect -grid {macro_grid} -layers {M6 M7}
