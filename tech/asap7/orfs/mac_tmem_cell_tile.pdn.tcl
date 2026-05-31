# Abutment-ready PDN for mac_tmem_cell_tile (issue #32, Phase A).
#
# Four-sided power ring per tile:
#   - Top + bottom edges: M4 horizontal VDD/VSS rails spanning tile width.
#   - Left + right edges: M5 vertical   VDD/VSS rails spanning tile height.
#
# When tile B is placed north of tile A (uniform R0 orientation), B's
# bottom-edge M4 rail and A's top-edge M4 rail land at the same Y on the
# same layer → metal shapes merge into one continuous stripe. Same logic
# for E/W abutment via M5. After 1024 abutments the rings form a 2-D PDN
# grid covering the entire array with NO parent pdngen step needed for
# the array interior — parent only lays a perimeter ring + connects the
# core ring to the chip-top trunk.
#
# Internal stripes from BLOCK_grid_strategy.tcl are inherited (M1/M2
# followpins + M5 stripes), connecting via M4/M5 to the edge rings.

add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDD$} -power
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSS$} -ground

set_voltage_domain -name {CORE} -power {VDD} -ground {VSS}

# Single top grid: M1/M2 followpins + M5 internal stripes + edge ring.
define_pdn_grid -name {top} -voltage_domains {CORE} -pins {M4 M5}

# M1/M2 followpins for stdcell rows.
add_pdn_stripe -grid {top} -layer {M1} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins
add_pdn_stripe -grid {top} -layer {M2} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins

# Edge ring on M5 (vertical, left+right edges) and M4 (horizontal, top+
# bottom edges). core_offset {0} places the ring directly at the die
# boundary so the rails are at x=0/x=TILE_W (M5) and y=0/y=TILE_H (M4).
add_pdn_ring -grid {top} -layers {M5 M4} -widths {0.288 0.288} \
             -spacings {0.096} -core_offset {0.0}

# Internal M5 stripes — preserves the connectivity the macro had before,
# so the M1/M2 followpins reach the edge ring via M5. Pitch 2.976 µm
# matches the asap7 BLOCK_grid_strategy.tcl default for leaves.
add_pdn_stripe -grid {top} -layer {M5} -width {0.12} -spacing {0.072} \
               -pitch {2.976} -offset {1.488} -extend_to_core_ring

# Via stack from stdcell followpins up to ring + internal stripes.
add_pdn_connect -grid {top} -layers {M1 M2}
add_pdn_connect -grid {top} -layers {M2 M5}
add_pdn_connect -grid {top} -layers {M4 M5}
