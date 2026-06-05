# chip_top PDN — grid + macro_grid welding for hardened-leaf VDD/VSS pins.
#
# chip_top now has only 7 hardened leaf macros (compute_array_abut, store,
# smem, cmdproc, load, barrier, reset_seq) — all on M6 or M7 power pins.
# fakeram banks are no longer placed at chip_top (they live inside the
# hardened smem.lef now), so the old M4/M5-tight machinery is unnecessary.

add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDD$} -power
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSS$} -ground

set_voltage_domain -name {CORE} -power {VDD} -ground {VSS}

define_pdn_grid -name {top} -voltage_domains {CORE} -pins {M6 M7}

# M1 + M2 followpins for the ~3K stdcells at parent (CDC + observability glue).
add_pdn_stripe -grid {top} -layer {M1} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins
add_pdn_stripe -grid {top} -layer {M2} -width {0.018} -pitch {0.54} \
               -offset {0} -followpins

# Power ring on the perimeter — M6 horizontal, M7 vertical.
add_pdn_ring -grid {top} -layers {M6 M7} -widths {0.544 0.544} \
             -spacings {0.096} -core_offset {0.504}

# M5 / M6 / M7 stripes — moderate pitch (40 µm). The old fine 4-µm M5
# was for fakeram welding back when smem was 16 inlined fakerams. With
# smem now hardened as one macro, fine M5 is unnecessary AND was likely
# blocking macro_grid stripe insertion inside compute_array (PDN-0232 →
# PDN-0233 at first chip_top attempt).
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

# Macro PDN grids — split by macro type.
#
# Small leaves (store/smem/cmdproc/load/barrier/reset_seq) use the
# `-default` rule: ORFS replicates parent stripes within their halo.
# This works for small macros where the parent stripe pitch fits.
#
# compute_array (1300×1300) is too large for `-default`: ORFS can't add
# stripes inside an opaque hardened macro, so the macro_grid stays empty
# (PDN-0232 → PDN-0233). Use a connect-only grid: drops vias where
# parent stripes cross compute_array's M7 perimeter pins, but doesn't
# try to add internal stripes.
# compute_array: explicit instance-named grid, no -default (connect-only,
# vias drop where parent stripes cross its M7 perimeter pins).
define_pdn_grid -macro -name {macro_grid_compute_array} -voltage_domains {CORE} \
    -instances {u_compute_array} -halo {0 0 0 0}
add_pdn_connect -grid {macro_grid_compute_array} -layers {M6 M7}

# Default for all other macros (store/smem/cmdproc/load/barrier/reset_seq):
# -default catches everything not bound by name above.
define_pdn_grid -macro -name {macro_grid_default} -voltage_domains {CORE} \
    -halo {2.0 2.0 2.0 2.0} -default
add_pdn_connect -grid {macro_grid_default} -layers {M5 M6}
add_pdn_connect -grid {macro_grid_default} -layers {M6 M7}
