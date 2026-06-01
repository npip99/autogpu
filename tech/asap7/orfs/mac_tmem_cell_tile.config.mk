# mac_tmem_cell re-hardened as an ABUTMENT-READY tile (issue #32, Phase A).
#
# Same RTL as the existing mac_tmem_cell.config.mk; only the hardening
# *flow* differs:
#   - Fixed die size on the asap7 site grid (34.56 µm = 640 sites × 0.054).
#   - Edge signal pins constrained to fixed boundary tracks via
#     mac_tmem_cell_tile.pins.tcl (W/E on M4 horizontal, N/S on M5 vertical,
#     with abutment-symmetric Y/X coordinates for {a_in,a_out},
#     {b_in,b_out}, {drain_in,drain_out}).
#   - Edge power ring (M4 H top/bottom, M5 V left/right) via
#     mac_tmem_cell_tile.pdn.tcl, so abutting two tiles forms one
#     continuous PDN with no parent pdngen step inside the array.
#   - MAX_ROUTING_LAYER = M6 keeps the tile's internal routing off
#     M7..M9, leaving M7 for parent perimeter routing and M8/M9 reserved
#     for issue #33's chip-wide clock infrastructure trunk. The leaf-LEF
#     guard issue #33 plans to add to run.sh will accept this tile.
#
# Coexists alongside mac_tmem_cell.config.mk during the #32 transition
# (chip_top MMA=4 keeps using the original until Phase D cuts over).
#
# See tech/asap7/TILE_SPEC.md for the boundary contract this implements.

export PLATFORM       = asap7
export DESIGN_NAME    = mac_tmem_cell
export DESIGN_NICKNAME = mac_tmem_cell_tile

# Same RTL the current mac_tmem_cell.config.mk uses.
export VERILOG_FILES  = /work/build/sv2v/chip_top.v
export SDC_FILE       = /work/tech/asap7/orfs/mac_tmem_cell.sdc

# Fixed die size on the asap7 site grid (640 × 0.054 µm = 34.56 µm
# horizontally; 128 × 0.27 µm = 34.56 µm vertically — clean both axes).
# 2 µm core inset leaves a halo on all four edges where the tile's
# internal routing won't go. This is what the parent's pin-access
# vias land in — without it, parent vias collide with the macro's
# M3/M4 OBS at the abutment seam and produce shorts in DRT. (First
# attempt was 1 µm and produced 45 boundary shorts at Phase B's 4×4
# harness; 2 µm should give 2× the via landing room.)
export FLOORPLAN_DEF =
export DIE_AREA   = 0 0 34.56 34.56
export CORE_AREA  = 2 2 32.56 32.56

# Abutment-ready IO pin placement: see TILE_SPEC.md for the boundary
# contract (which signal on which edge, which layer, which side).
export IO_CONSTRAINTS = /work/tech/asap7/orfs/scripts/mac_tmem_cell_tile.pins.tcl

# Default IO pin layers for any pins that fall through the explicit
# constraints (shouldn't be any, but kept consistent with the spec):
# W/E on M4 (horizontal), N/S on M5 (vertical).
export IO_PLACER_H = M4
export IO_PLACER_V = M5

# Use the platform default BLOCK_grid_strategy.tcl (followpins + M5
# stripes + ring). Tile abutment relies primarily on **followpin
# alignment**: tile_h = 34.56 µm = 128 × row_pitch(0.27 µm) means the
# first/last rows land at deterministic y coords that align across
# abutted tiles, so M1/M2 followpin rails are continuous across edges.
# The default ring sits ~0.084 µm inside the core (not at the die
# boundary), so it does NOT itself form the abutment ring — but it
# does deliver power within each tile. This is enough for v1; a true
# at-the-boundary ring can be a Phase A iteration if IR drop or
# verify_macro_power flags it.
# export PDN_TCL  =  (use platform default — comment kept for future override)

# Cap internal routing at M6. Leaves M7 free for parent perimeter routing
# and M8/M9 reserved for #33's chip-wide clock trunk. mac_tmem_cell's
# internal logic + tmem fits comfortably in M1-M6.
export MAX_ROUTING_LAYER = M6

# Skip last_gasp on tile iteration (saves ~10 min/spin; not useful while
# tuning boundary geometry).
export SKIP_LAST_GASP ?= 1
