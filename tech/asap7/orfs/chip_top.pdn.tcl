# chip_top PDN — simple grid plus M1/M2 followpins, plus A1's macro_grid
# welding rules so the parent stripes connect to every hardened-leaf
# VDD/VSS pin.
#
# chip_top has only ~21 macros (1 compute_array + 4 sub-block macros +
# 16 fakeram banks), so ORFS's stock BLOCKS_grid_strategy.tcl O(stripes ×
# macros) cost is fine here (unlike compute_array's 1089 macros). But
# the asap7 default `BLOCKS_grid_strategy.tcl` config caused PDN-0006
# blocking errors during early experiments, so we ship our own minimal
# PDN here for predictability.

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

# Vertical M5 stripes at a FINE pitch + M6/M7 at a coarse pitch.
#
# The M5 verticals do the macro-welding work: chip_top's macro mix has
# tiny instances whose power pins a coarse parent pitch steps right over,
# leaving them unwelded (PDN-0232 → PDN-0233). A vertical stripe is
# guaranteed to cross a power-pin window of width W only when pitch ≤ W.
# The binding constraint is reset_seq, whose M6 pin spans just x[2.05,
# 6.32] = 4.27 µm inside its 8.35 µm cell (the fakeram M4 rails are wider
# at 8.26 µm, barrier/cmdproc/load wider still). So M5 runs at 4 µm
# (≤ 4.27 µm) — every macro's pin window gets at least one M5 vertical.
# The fakeram VDD/VSS pins are horizontal M4 rails, so an M5 vertical
# overlaps them; the macro_grid M4/M5 connect (below) drops the vias.
# M6/M7 stay coarse (60 µm) — they form the global grid and the M5
# verticals tie into them die-wide.
add_pdn_stripe -grid {top} -layer {M5} -width {0.120} -spacing {0.096} \
               -pitch {4.0} -offset {2.0} -extend_to_core_ring
add_pdn_stripe -grid {top} -layer {M6} -width {0.288} -spacing {0.096} \
               -pitch {60.0} -offset {10.0} -extend_to_core_ring
add_pdn_stripe -grid {top} -layer {M7} -width {0.288} -spacing {0.096} \
               -pitch {60.0} -offset {10.0} -extend_to_core_ring

add_pdn_connect -grid {top} -layers {M1 M2}
add_pdn_connect -grid {top} -layers {M2 M5}
add_pdn_connect -grid {top} -layers {M5 M6}
add_pdn_connect -grid {top} -layers {M6 M7}

# Macro PDN grid — welds parent stripes to hardened-leaf VDD/VSS pins.
# Same A1 fix applied at compute_array.pdn.tcl. Without this, pdngen
# treats each leaf macro as unrelated geometry and never bridges the
# parent grid onto leaf pins (PSM-0069). Halo matches MACRO_PLACE_HALO
# in chip_top.config.mk so vias land cleanly in the inter-macro gap.
# See tech/asap7/problems/A1_pdn_macro_grid.md.
#
# chip_top's macro pins live on different layers per family:
#   - fakeram7_256x32 (smem banks): M4 only
#   - cmdproc / load / barrier / reset_seq: M6
#   - compute_array: M6 + M7
# So the weld needs the full M4→M5→M6→M7 connect ladder, not just the
# M5/M6 + M6/M7 that compute_array's homogeneous M6-pin leaves needed.
# The M4/M5 rung is what reaches the fakeram pins.
define_pdn_grid -macro -name {macro_grid} -voltage_domains {CORE} \
    -halo {2.0 2.0 2.0 2.0} -default
add_pdn_connect -grid {macro_grid} -layers {M4 M5}
add_pdn_connect -grid {macro_grid} -layers {M5 M6}
add_pdn_connect -grid {macro_grid} -layers {M6 M7}
