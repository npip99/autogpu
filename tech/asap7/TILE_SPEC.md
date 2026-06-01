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

   **Layer-stack contract (forward-compat with #33):** the tile must
   leave M8/M9 untouched — those layers are reserved for the
   chip-wide clock trunk #33 lays down. Edge rails on M5, internal
   routing M1–M6, abstract LEF references nothing above M6. #33 adds
   a build-time guard (in `run.sh`) that errors if a leaf abstract LEF
   references M8/M9; if Phase A drifts above M6 by accident, that guard
   will catch it.

6. **Drain bus = routed at parent.** The 1024-bit `drain_row_data` from
   the top row's tiles to the chip output port is wide-not-long; per-bit
   wires are short (top-row tiles already sit at the top edge). Routed
   at parent — no per-tile pipelining.

## Tile boundary

### Direction conventions (from compute_array.sv RTL)

The systolic flow already determines which signals enter/leave on which
edge:

| Signal | Direction | Reason from RTL |
|---|---|---|
| `a_in`, `compute_in`, `slot_in`, `accum_in` | enter from **WEST** | `a_in_w = (gj==0) ? edge_a : a_pipe[gi][gj-1]` — west neighbor's `a_out` |
| `a_out`, `compute_out`, `slot_out`, `accum_out` | leave to **EAST** | drives east neighbor's `a_in` |
| `b_in` | enter from **SOUTH** | `b_in_w = (gi==0) ? edge_b : b_pipe[gi-1][gj]` — south neighbor's `b_out` |
| `b_out` | leave to **NORTH** | drives north neighbor's `b_in` |
| `drain_in` | enter from **NORTH** | `drain_in_w = drain_pipe[gi+1][gj]` — gi+1 is at higher y |
| `drain_out` | leave to **SOUTH** | drives south neighbor's `drain_in` |

### Per-edge pin layout

Per-edge layer convention: edge pins are on the metal layer whose
preferred direction is *perpendicular* to the edge (so the stub
naturally extends from interior to boundary). asap7:
M2 H · M3 V · M4 H · M5 V · M6 H · M7 V.

- **WEST edge (M4 horizontal pins, at x=0):** `a_in[7:0]`, `compute_in`,
  `slot_in[1:0]`, `accum_in` (12 datapath pins) + `reset_w`, `drain_en_w`,
  `drain_slot_w[1:0]`, `scrub_en_w` (5 broadcast-feedthrough inputs) —
  **17 pins** total.
- **EAST edge (M4 horizontal pins, at x=tile_w):** `a_out[7:0]`,
  `compute_out`, `slot_out[1:0]`, `accum_out` (12 datapath pins) +
  `reset_e`, `drain_en_e`, `drain_slot_e[1:0]`, `scrub_en_e` (5
  broadcast-feedthrough outputs) — **17 pins** total.
- **SOUTH edge (M5 vertical pins, at y=0):** `b_in[7:0]`,
  `drain_out[31:0]` — **40 pins** total.
- **NORTH edge (M5 vertical pins, at y=tile_h):** `b_out[7:0]`,
  `drain_in[31:0]`, `clk` — **41 pins** total.

**Broadcast feedthrough rule (issue #32 root-cause fix):** the four
former parent-broadcast signals (`reset`, `drain_en`, `drain_slot[1:0]`,
`scrub_en`) are NOT N-only single pins anymore. They are abutment-
feedthrough pairs (`*_w` input on W, `*_e` output on E) with `assign
*_e = *_w` inside the tile. The parent drives ONLY the westernmost
column's `*_w` pins; abutment carries each broadcast east through every
row. This is the only way the parent never has to route a wire over the
abutted tile area (which is blocked M1..M6 by the tile LEF OBS).
`clk` is the lone exception — it stays N-only until #33 lands.

### Abutment invariants

The parent places tiles in a regular grid in **uniform orientation
(`R0` for all)**. No flips, no rotations. This makes edge mating
trivial: tile A's east edge at `(tile_w, *)` aligns with tile B's west
edge at `(tile_w + 0, *)` after B is placed at `(tile_w, 0)`. For
metal shapes to merge into one wire on contact:

- **W/E abutment** requires `a_in[k]` and `a_out[k]` to be at the
  **same Y** on opposite edges (same layer M4). When tile B is placed
  east of tile A, the M4 stubs from each side meet at `(tile_w, Y_k)`
  and merge. **Same Y-share rule applies to every broadcast feedthrough
  pair** (`reset_w/reset_e`, `drain_en_w/drain_en_e`, etc.) — that's
  what makes the parent's drive into column 0 propagate east through
  the array via abutment.
- **N/S abutment** requires `b_in[k]` and `b_out[k]` to be at the
  **same X** on opposite edges (same layer M5). Same for
  `drain_in[k]` and `drain_out[k]`. When tile B is placed north of
  tile A, the M5 stubs meet at `(X_k, tile_h)` and merge.

These symmetry requirements are enforced by the pin-placement TCL
emitted in Phase A.

### Broadcast feedthrough timing contract

The four broadcast feedthrough pairs (`reset_w/reset_e`,
`drain_en_w/drain_en_e`, `drain_slot_w/drain_slot_e[1:0]`,
`scrub_en_w/scrub_en_e`) are **combinational pass-throughs** inside the
tile (`assign *_e = *_w`). When tiles abut, the parent's drive into
column 0 ripples east across the row as one long M4 wire:

- M=N=4 (validated): 4 hops × ~35 µm = ~140 µm of M4 + 3 abutment vias.
- M=N=32: 32 hops × ~35 µm = ~1100 µm of M4 + 31 abutment vias.

**Three signals are quasi-static; one is a single-cycle snapshot pulse.
They have DIFFERENT timing contracts** — do not lump them together:

| Signal | Class | Active duration | Sampling cell | SDC treatment |
|---|---|---|---|---|
| `reset` | quasi-static | held ≥ N cycles by `reset_seq` (inactive ≥ N cycles before any compute begins) | every cell's `always_ff` | **`set_false_path`** |
| `drain_slot[1:0]` | quasi-static | constant for the entire drain op (`drain_saved_slot`, ≥ N cycles) | every cell during drain | **`set_false_path`** |
| `scrub_en` | quasi-static | one pulse per scrub gated by boot/reset (multi-cycle window) | every cell during scrub | **`set_false_path`** |
| `drain_en` | **single-cycle snapshot pulse** | **exactly 1 cycle** (`cmd_unit` `D_IDLE → D_PULSE` asserts; `D_PULSE → D_STREAM` clears) | **every cell on the SAME edge** to load `storage[drain_slot]` simultaneously; data then shifts north over MMA_M cycles | **MUST close as single-cycle path — no false_path, no multicycle** |

For the three quasi-static signals, the launch-to-sample slack is
multi-period and the combinational ripple resolves long before
sampling — false_path is correct.

`drain_en` is fundamentally different. If column j samples the snapshot
one cycle late, its `storage[slot]` goes into the wrong row of
`drain_pipe` and `drain_row_data[j*32 +: 32]` comes out column-
misaligned. The same-edge-capture requirement is **a real timing path
that STA must enforce**, not a path to relax. **Removing `drain_en` from
the false_path list is intentional**: at M=N=4 it closes easily (~140 µm
ripple), at M=N=32 it likely won't (~1100 µm ripple) — that's genuine
information #40 must act on. The snapshot cannot be pipelined the way
PR #34 pipelined `push_a/b` (a staggered wavefront breaks the same-edge
requirement); #40 will need a different solution — balanced low-skew
distribution, or a redesign that tolerates per-column phase offset
(e.g. each column gets its own `drain_row_valid` with matching delay).

**SDC files:** see `tech/asap7/orfs/mac_array_small_abut.sdc` and
`tech/asap7/orfs/compute_array_abut.sdc`.

**If you add a new broadcast feedthrough:** classify it first. If
producer holds it stable for the full row-traversal duration plus one
sampling cycle, you can `set_false_path`. If consumers must capture
the same edge, **time it for real** and accept that the path width
limits how wide the chain can grow.

### Edge power rails (ring per tile, abuts into a 2-D grid)

Each tile has a four-sided power ring:

- **Top + bottom (N/S edges):** M4 horizontal rails for VDD and VSS at
  `y=0` (bottom) and `y=tile_h` (top), the full tile width.
- **Left + right (W/E edges):** M5 vertical rails for VDD and VSS at
  `x=0` (left) and `x=tile_w` (right), the full tile height.

When tile B is placed north of tile A, A's top rail and B's bottom rail
sit at the same y on the same layer → merge into one continuous M4
stripe. Same for E/W with M5. After 1024 abutments, this forms a 2-D
PDN grid covering the entire array with no `pdngen` step needed for
the array interior.

Internal stripes (from `BLOCK_grid_strategy.tcl`) stay as they are —
they connect to the edge rings via existing M4–M5 vias.

### Die size

Target: **34.560 µm × 34.560 µm** (640 sites × 0.054 µm = 34.560,
exact on the asap7sc7p5t_rvt site grid). Slightly smaller than the
current 34.543 µm hardening (which itself is just below-grid; ORFS
rounded). Row pitch in current LEF is 0.27 µm → 128 rows × 0.27 =
34.56 µm exactly. So 34.56 × 34.56 lands on **both** the site x-pitch
*and* the row y-pitch.

Final value confirmed against `mac_tmem_cell` cell content area during
Phase A hardening — if 34.56 is too tight for the existing internal
content (the `mac_tmem_cell` includes one fp32_fma + tmem storage and
isn't trivially shrinkable), grow to **39.96 µm × 39.96 µm** (740
sites × 0.054, 148 rows × 0.27). The grid stays clean either way.

### Internal CTS

Local-only: parent (or the #33 mesh trunk) terminates at the tile's
single `clk` pin on the NORTH edge. From there, the tile's own CTS
runs a trivial shallow tree (~few buffer levels) to the local flop
fan-out (mac_tmem_cell's internal flops, ~100s of sinks). No
chip-wide tree depth contributed by the tile.

### Layer-stack constraint (#33 compat)

`mac_tmem_cell` internal routing stays **M1–M6** (current hardening
default). Edge rails on **M5**, edge pins on **M4** (W/E) and **M5**
(N/S). Abstract LEF references nothing above M6. Parent perimeter
routing stays **≤ M7**. This leaves **M8/M9 untouched** for #33's
chip-wide clock trunk — issue #33's `run.sh` LEF guard will not error
on the abutment-ready tile.

## Phase B findings (4×4 abutment validation, `mac_array_small_abut`)

Ran 10 iterations of a 4×4 abutted harness using `mac_array_small` as
the harness RTL. Result: **the abutment recipe is validated**, with one
caveat about test-vehicle suitability documented below.

What's validated:
1. **PDN abutment works.** `PDN-0001` clean for all 16 abutted tiles via
   a parent PDN that mirrors PR #5 (A1)'s pattern, except one layer up:
   tile exposes power pins on **M6** (not M5 as `BLOCK_grid_strategy.tcl`
   `-pins` says — confirmed by inspecting the generated tile LEF), so
   parent runs **M7 vertical stripes pitch 5.4 µm offset 1.5** across
   the die and the macro grid welds M6-M7 vias. The
   tech/asap7/orfs/mac_array_small_abut.pdn.tcl in this PR is the
   working template.
2. **Signal pin abutment works at the netlist level.** Directly checked
   the post-CTS ODB: tile (0,0)'s `a_out[0]` and tile (0,1)'s `a_in[0]`
   are both on net `a_pipe[0][0]`. The router treats them as one net
   even with zero-width edge-touch pins; no pin overlap extension
   needed.
3. **Tile placement holds the spec.** Tile bboxes verified abutting
   exactly at the boundary (tile (0,0) east = tile (0,1) west = 39.6 µm
   in chip coords), placed with R0 orientation everywhere, on the
   asap7 site/row grid.

What's not validated (and why it doesn't matter for the spec):
- **Full GRT routing of `mac_array_small_abut` failed** with M2/M3
  overflow ~100K on the routing channels. Diagnosed: the 1024-bit
  `drain_row_sel` mux (768 parent stdcells: 256 each of INVx1, NAND2x1,
  OA211x2) implementing the row select for `drain_row_data` is the
  congestion source. Top-fanout nets are the buffered select signals
  fanning to ~70 sinks each. Confirmed by per-net inspection of the
  ODB — abutted tiles themselves have ~zero parent route demand. The
  congestion is in the drain-mux + drain-bus region, structural to
  `mac_array_small`'s RTL and unrelated to abutment.
- **mac_array_small is not the right Phase B vehicle long-term.** Its
  `drain_row_sel` mux dominates parent routing and obscures abutment
  validation. `compute_array.sv` (the Phase C target) has very different
  parent structure — `cmd_unit` + perimeter chain registers + a simple
  drain bus from the top row (no row-select mux), so the same harness
  pattern should fit much cleaner at the Phase C scale.

Files in this PR for Phase B:
- `tech/asap7/orfs/mac_array_small_abut.config.mk`
- `tech/asap7/orfs/mac_array_small_abut.sdc` (3333 ps / 300 MHz, matching
  the existing `compute_array.sdc` to avoid timing-driven router pressure)
- `tech/asap7/orfs/mac_array_small_abut.pdn.tcl` (the M7-vertical /
  macro-grid pattern that validates above)

The macro placement TCL (`mac_array_small_abut.macro_placement.tcl`) is
gitignored per the repo convention. Re-emit with:
```
# 4×4 grid of 34.56 µm tiles, R0, on asap7 grid (site 0.054 H, row 0.27 V).
# CORE origin: (5.022, 5.13). Tile (i,j) at (5.022 + j*34.56, 5.13 + i*34.56).
for r in 0 1 2 3: for c in 0 1 2 3:
    place_macro -macro_name gen_row\[r\].gen_col\[c\].u_cell \
                -location {5.022 + c*34.56  5.13 + r*34.56} \
                -orientation R0
```

## Closed questions

The three open questions from the initial spec are settled:

- **`drain_en` / `drain_slot` / `scrub_en`:** edge pins on NORTH for
  Phase A (parent supplies them; same parent-broadcast pattern as
  today). If they become a wire-delay bottleneck at full 32×32, apply
  the same #31 relay-chain pattern at parent — but kept out of the
  initial tile spec to avoid bloating the boundary.
- **`clk` entry:** NORTH edge, M5 vertical. Parent CTS terminates here.
- **`pa_chain` / `pb_chain` location:** stays at **parent level**,
  placed at the W and S perimeters of the abutted array. Tile is one
  uniform type — no west-edge-special-tile variant. Chain reg at
  perimeter directly drives the leftmost-column / bottom-row tile's
  `a_in` / `b_in` edge pin (a single short routed segment from chain
  reg to tile boundary).

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
