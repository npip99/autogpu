# compute_array tile-abutment spec (issue #32)

The full 32×32 `compute_array` is currently P&R'd as one monolithic
job over 1089 macros — channel-routed, channel-PDN'd, with a parent
clock tree reaching every macro CLK pin. That setup is what's slow
(~8 h+ wall, fails #30 routability) and what required PR #27's
workarounds (I/O false-paths + 300 MHz).

This spec re-hardens `mac_tmem_cell` as an **abutment-ready** macro
so the parent compute_array becomes pure placement: 1024 macros on a
32×32 grid, with edges touching. Signals connect by edge overlap.
Power rails form a continuous grid by edge abutment. No parent routing,
no parent PDN gen, no inter-macro channels inside the array.

The mac_tmem_cell RTL does not change — only its hardening flow
(floorplan, pin placement, edge rails) changes. A new ORFS config
(`mac_tmem_cell_tile.config.mk`) lives alongside the existing one
during transition.

## Design decisions (agreed)

1. **Tile size = 1×1.** The "tile" is one `mac_tmem_cell` instance,
   re-hardened with abutment constraints. 1024 tiles in the 32×32
   grid. Not a 4×4 sub-block — the 4×4 only exists below as a
   validation harness (Phase B), not as a hierarchy level.

2. **`skew_lane_a` / `skew_lane_b` are deleted entirely.** After PR
   #34's chain restructure, they are functionally just live pass-
   throughs (`tap_index=0`). The chain in `compute_array.sv` already
   does the systolic delay job. Wiring `pa_chain[i]` → `cell[i][0].a_in`
   and `pb_chain[j]` → `cell[0][j].b_in` directly removes 64 macros,
   ~23.8 K dead-but-clocked flops, and a hierarchy layer.

3. **`cmd_unit` stays a parent singleton.** Placed in a corner outside
   the tile grid.

4. **Edge rails on M5.** Matches what `mac_tmem_cell` already uses
   internally (asap7 `BLOCK_grid_strategy.tcl` exposes leaf PDN on M5).
   Same layer + matching pitch on both edges = clean abutment with
   zero gymnastics. M6/M7 stay for parent perimeter routing if needed.

5. **Clock to tiles uses today's CTS for #32.** Parent CTS reaches
   1024 tile CLK pins. Smaller insertion delay than today's monolithic
   1089-macro tree because the parent has nothing else to drive. When
   issue #33 (top-metal infrastructure clock tree on M8/M9) lands, the
   tile contract (one CLK pin, expect uniform clock) is unchanged —
   only the parent's *delivery* of clock to the pin gets better. Tile
   is forward-compatible by default.

6. **Drain bus = routed at parent.** The 1024-bit `drain_row_data` from
   the top row's tiles to the chip output port is wide-not-long; per-bit
   wires are short (top-row tiles already sit at the top edge). Routed
   at parent — no per-tile pipelining.

## Tile boundary (Phase A WIP)

Die size, edge rail pitch, edge pin tracks — TBD; will be filled in
as Phase A progresses. Skeleton headings:

- Die size + grid pitch
- Edge power rails (M5): layer, pitch, offset, width
- Edge signal pin tracks
  - West edge:  `a_in[7:0]`, `compute_in`, `slot_in[1:0]`, `accum_in`
  - East edge:  `a_out[7:0]`, `compute_out`, `slot_out[1:0]`, `accum_out`
  - South edge: `b_in[7:0]`, `drain_in[31:0]`
  - North edge: `b_out[7:0]`, `drain_out[31:0]`
  - North or West edge: `clk`, `reset`
  - Edge for parent broadcasts: `drain_en`, `drain_slot[1:0]`, `scrub_en`
- Internal CTS strategy (small, shallow tree to local flops)

## Open questions (to settle during Phase A)

- Do `drain_en` / `drain_slot` / `scrub_en` get an edge pin on every
  tile + parent relay (like push), or stay as a parent-driven broadcast
  fanning into every tile? (Today: parent broadcast; same wire-delay
  class as the push problem #31 fixed for `push_a/b`.)
- Where does `clk` enter the tile (which edge, which track)? Affects
  how parent CTS terminates.
- Should the tile carry its own slice of `pa_chain` / `pb_chain`
  internally (uniform tile, west-edge slice goes unused on interior
  tiles), or stay as parent-level chain at the perimeter (one tile
  type, chain regs at parent)?

## Phases (tracked in this PR)

See PR description.

## Related

- Issue #32 — this is the implementation.
- PR #27 — established the I/O false-paths + 300 MHz workarounds this
  ultimately retires.
- PR #34 — the broadcast chain whose RTL flow contract this PR builds
  on (and whose `skew_lane`s this PR deletes).
- Issue #35 — placement-constraint follow-up to PR #34, naturally
  subsumed by this PR (the chain registers either live inside the tile
  or at the parent perimeter, both abutment-aware).
- Issue #33 — top-metal clock infrastructure. Orthogonal; tile is
  forward-compatible.
- Issue #30 — full-32 routability. Dissolves by construction once the
  array is abutted (no channels = no channel congestion).
- Issue #28 — chip_top boundary timing closure. Mostly unblocked by
  #33's clock contract, not this PR — but smaller per-block insertion
  delay (fewer sinks under parent CTS) helps marginally.
