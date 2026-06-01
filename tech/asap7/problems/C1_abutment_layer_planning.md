# C1 — Layer planning for abutment-ready tiles (GRT-0116 + DRT-0199)

> **Status: RESOLVED.** Fixed in issue #32 Phase B by (a) converting all
> non-clock parent broadcasts into W↔E abutment-feedthrough pin pairs
> and (b) adding M4 to the LEF OBS strip list in `tech/asap7/orfs/run.sh`.
> Post-fix the 4×4 `mac_array_small_abut` harness routes cleanly:
> GRT 0 overflow, DRT 0 violations, 6_final.gds emits in ~5.5 min.

## Problem

Hardening the 4×4 abutted-tile harness (`mac_array_small_abut`) failed
in two distinct ways across 12+ iterations:

1. **GRT-0116 with 46K M2/M3 overflow but only 5% layer usage.** Router
   couldn't drive overflow to zero even with 30 extra iterations. Final
   congestion report showed total demand 5% of resource — but `Max H=5`
   / `Max V=6` per gcell, i.e. localized 0-track hotspots.
2. After (1) was fixed: **DRT-0199 reports 37–45 boundary shorts** that
   wouldn't converge — DRT iterated all 64 attempts and still left
   tiny M4 shorts at every tile-to-tile boundary, one per pin pair the
   parent had to wire across.

Both failure modes occurred at the **abutted-tile boundary**, not in
the perimeter strip or tile interiors. ~70% of the die was empty.

## Root cause (observed, not hypothesized)

The abutment-ready `mac_tmem_cell_tile` exposes a finished macro LEF
via `write_abstract_lef -bloat_occupied_layers`. That tool marks every
routing layer the macro used as a single OBS rectangle covering the
full 34.56 × 34.56 µm face. Pre-fix the post-processed LEF retained
OBS on **M3 and M4**.

### Failure (1) — GRT-0116 broadcast over abutted area

Five parent broadcasts (`drain_en`, `drain_slot[0..1]`, `scrub_en`,
`reset`) were N-only single pins. Parent had to fan from one die-edge
input port to all 16 tile northern pins. With:
- M1..M6 blocked over the tile by OBS
- `MAX_ROUTING_LAYER = M7` (vertical only)
- M7 also carrying the power grid

…the parent had **no horizontal routing layer over the array**. A
broadcast needs both H and V to fan to 16 sinks via a Steiner tree;
without a horizontal layer it can only drop per-column verticals. The
router squeezed the missing horizontal pieces into 0-track M2/M3 gcells
at tile edges → 46K total overflow.

### Failure (2) — DRT-0199 M4 boundary shorts

After fixing (1), the parent still has W/E abutment pin pairs that
must be wired together. The abutment-ready pin RECTs sit at exactly
the tile boundary (e.g. `a_in[0]` at x=0..0.084 inside col1, abutting
`a_out[0]` at x=34.476..34.56 inside col0). When tiles abut at chip-x
= 39.582, the two pin RECTs share a single edge — touching, not
overlapping. DRT doesn't see them as a merged polygon and tries to
draw a tiny M4 stub to bridge them. The stub overlaps the macro's
full-face M4 OBS → DRC short between the macro's pin metal and the
parent's bridging wire.

Net effect: one short per abutted pin pair × ~12 pin pairs/edge ×
~6 vertical abutment edges in a 4×4 = ~37–45 shorts. DRT can't fix
them because the underlying conflict (OBS vs pin-access) is geometric,
not routing-effort.

## Fix

Two-part, both required:

### Part A — Broadcasts go through tiles, not over them

Add W↔E abutment-feedthrough pin pairs for every parent broadcast that
isn't `clk`. In `mac_tmem_cell.sv`:

```sv
input  logic  reset_w,
output logic  reset_e,
// ...
assign reset_e      = reset_w;
assign drain_en_e   = drain_en_w;
assign drain_slot_e = drain_slot_w;
assign scrub_en_e   = scrub_en_w;
```

Internal cell logic consumes the `*_w` version. Parent (e.g.
`mac_array_small.sv`) drives col 0's `*_w` from the chip input port;
col j>0 drives `*_w` from col (j-1)'s `*_e`. The tile is a pure wire
on M4 between the W and E pins — abutment carries the signal east
without any parent routing over a macro.

`clk` is the lone broadcast that stays an N-only single pin (it needs
a real clock tree; that's issue #33's job).

### Part B — Don't OBS-bloat the pin layer

In the LEF post-process (`tech/asap7/orfs/run.sh`), add the pin layer
to the strip list:

```bash
python3 .../strip_lef_obs_layers.py "$LEF"  M1 M2 M4 M5 M6 M7
#                                            ^^ added M4
```

Effect: post-processed LEF has OBS only on M3 + RVTN/RVTP. The pin
layer (M4) is no longer marked obstructed, so DRT can land pin-access
vias right at the abutment seam without conflict.

This is safe because:
- The macro's internal M4 usage is sparse (mostly just pin stubs near
  the W/E edges). Parent over-tile M4 routing is unlikely to conflict.
- M3 OBS still blocks broad parent routing across the tile.
- The PG ring on M5/M6/M7 still has its access through the stripped
  layers.

## Validation

Pre-fix Phase B (12 iterations attempted):

```
GRT M2 overflow: 19,746     GRT M3 overflow: 21,082
Total overflow:  45,973     Routed nets:     1102
DRT violations:  37–45 (never converged)
6_final.gds:     never emitted
```

Post-fix Phase B (single iteration):

```
GRT all layers:  0 overflow   (max H=0, max V=0)
DRT iterations:  190 → 61 → 40 → 4 → 4 → 0
DRT violations:  0
6_final.gds:     26.5 MB, emitted in 5m 31s
```

## Diagnostic patterns to remember

1. **GRT-0116 with low total usage + high per-gcell Max H/V** = localized
   hotspot from a layer made unavailable by macro OBS. Check macro LEF
   OBS list vs your routing layers and broadcast routing requirements.
2. **DRT iter count is a correctness signal.** Converging design clears
   in 4–6 iterations (~2 min for 4×4 abutted). Non-converging design
   wastes 30+ min churning through 64 max iterations. Iter 0 > 1000 or
   iter 3 > 100? Abort early — the input is geometrically broken,
   re-diagnose at the spec level.
3. **At abutment seams, the pin layer should NOT be in the macro's OBS.**
   Otherwise DRT can't land pin-access vias without DRC shorts.

## Files touched

- `mac_tmem_cell/mac_tmem_cell.sv` — added W↔E feedthrough ports
- `mac_array_small/mac_array_small.sv` — wired feedthrough chain
- `compute_array/compute_array.sv`, `dense_grid/dense_grid.sv` —
  updated instantiations to new port names
- `tech/asap7/orfs/scripts/mac_tmem_cell_tile.pins.tcl` — added 5
  W/E broadcast feedthrough pin pairs on M4
- `tech/asap7/TILE_SPEC.md` — documented new pin layout
- `tech/asap7/orfs/run.sh` — added M4 to the LEF OBS strip list
- `tech/asap7/orfs/mac_tmem_cell_tile.config.mk` — `CORE_AREA` inset
  1µm → 2µm (defense in depth; not the load-bearing fix)
- `tech/asap7/orfs/mac_array_small_abut.config.mk` — point at
  post-processed LEF (was raw `mac_tmem_cell.lef`)

## Cross-references

- `tech/FAILURES.md` § GRT-0116, § DRT-0199 — short error-code lookup
- `tech/asap7/TILE_SPEC.md` — boundary contract being implemented
- issue #32, PR #36 — full PR history
