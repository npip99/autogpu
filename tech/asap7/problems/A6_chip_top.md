# A6 — chip_top integration with stubs

## Problem

`chip_top` does not yet exist as a hardened design on asap7. The RTL
is at `top/chip_top.sv` and an sv2v'd flat netlist exists at
`build/sv2v/chip_top.v`, but there is no:
- `chip_top.config.mk`
- `chip_top.sdc`
- `chip_top.pdn.tcl`
- `chip_top.macro_placement.tcl` (or a generator script for it)
- IO ring / pad placement
- Run output (no `build/orfs/results/asap7/chip_top/` directory)

chip_top must integrate (at minimum):
- `compute_array` (1 instance) — the systolic-array tile
- `smem` — shared memory (some count; check chip_top.sv for exact
  instantiation)
- `tile_buf_8row`, `cmdproc`, `load`, `store`, `barrier`, `reset_seq`,
  `cmd_unit` — confirm exact set from chip_top.sv
- Possibly: RISC-V core, peripheral controllers (check the RTL)

Hardened leaf LEFs/LIBs already exist for each. compute_array hardens
hierarchically into its own LEF/LIB which chip_top consumes as
black-box (same pattern as compute_array → mac_tmem_cell).

Goal of this task: produce a chip_top run that completes end-to-end
through `6_final`, even if it inherits known bugs (PSM-0069 from A1,
hold-timing issues from A2). The point is to surface chip_top-level
integration issues — clock distribution across the chip, top-level
pin/port layout, macro arrangement, congestion in inter-block channels
— **early**, in parallel with A1 and A2 work, not after.

## Acceptance criteria

1. `./tech/asap7/orfs/run.sh chip_top` completes through `6_final`
   without hard errors (PSM-0069 acceptable for now; document it).
2. `tech/asap7/orfs/scripts/verify_macro_power.tcl` runs on the result
   ODB and produces a number (pass or fail — both acceptable for first
   pass, but a number is required).
3. `chip_top.config.mk`, `chip_top.sdc`, `chip_top.pdn.tcl`, and
   `scripts/gen_chip_top_floorplan.py` all exist and are documented.
4. A KLayout PNG render of chip_top exists at
   `build/render/chip_top_asap7.png` showing all hardened blocks placed
   in plausible relative positions.
5. The chip_top floorplan is "reasonable": no obvious wasted area
   (utilization > 40 % is fine for early integration), inter-block
   channels wide enough for inter-block routing, IO ports on the die
   perimeter.
6. A `chip_top` entry exists in
   `tech/asap7/DESIGN.md` "File map" section.

## Constraints

- **Use current leaf abstract LEFs as-is.** Do NOT block on A1/A2.
  PSM-0069 and hold-timing issues will recur at chip_top — accept them
  for the first pass and document. Once A1/A2 land, chip_top will be
  re-run to validate the fixes propagate.
- **Don't modify leaf RTL or hardening artifacts.** Same rule as A1/A2.
- **Don't modify `compute_array.sv`** — A2 owns it. If chip_top needs
  parameter values that aren't currently exposed, file a follow-up.
- **Don't bypass PSM-0069 silently.** It is expected to fail until A1
  lands; the failure should be visible in the run log and documented in
  the result.
- **Don't add IO pads in this task.** ORFS asap7 doesn't ship pad
  cells. Top-level ports are direct die-edge pins for now. Pad ring is
  a separate follow-up (called out in A6_status.md).
- **For now: `HOLD_SLACK_MARGIN=-200` at chip_top is acceptable** (same
  workaround as compute_array). Remove once A2 lands.

## Inputs / references

- RTL: `top/chip_top.sv` (the source); `build/sv2v/chip_top.v` (the
  sv2v'd flat netlist for synthesis input)
- sky130 template: `tech/sky130/chip_top_floorplan.yaml` shows the
  block list and approximate dimensions used in the sky130 flow —
  excellent starting point for asap7 floorplan ratios
- Hardened leaves available now (LEFs in
  `build/orfs/results/asap7/<block>/base/<block>.lef`):
  - cmd_unit, mac_tmem_cell, skew_lane_a, skew_lane_b (compute_array's
    leaves — only needed at chip_top if it instantiates them directly,
    not via compute_array)
  - smem, tile_buf_8row (likely chip_top-level)
  - cmdproc, load, store, barrier, reset_seq, mac_array_small
  - compute_array (after current full-32×32 run completes; or use the
    tiny variant for first pass)
- Templates to copy/adapt:
  - `tech/asap7/orfs/compute_array.config.mk`
  - `tech/asap7/orfs/compute_array.pdn.tcl`
  - `tech/asap7/orfs/scripts/gen_compute_array_floorplan.py`
- `tech/asap7/orfs/run.sh` driver (no changes needed; already accepts
  `chip_top` as a module name)
- DESIGN context: `tech/asap7/DESIGN.md` "Hierarchy and layer
  discipline" + "Known issues / TODO toward tape-out"

## Out of scope

- PDN fix (A1), hold timing (A2), LVS (A3), antenna (A4), IR (A5)
- Pad ring / IO cells (separate follow-up)
- Re-running compute_array after A1/A2 fixes (Pool C)
- Per-block hardening fixes (Pool B)
