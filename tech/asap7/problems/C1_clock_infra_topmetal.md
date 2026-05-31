# C1 — Top-metal clock infrastructure (chip-wide low-insertion clock)

Implementation plan for issue #33. Status: **plan / not yet built.** This
document is the proposal under review; no flow files are changed yet.

## Problem

ORFS's default CTS builds one deep H-tree to every sink. On `compute_array`
at full 32×32 (1950 µm die, 1089 macro CLK pins + 564 register sinks) the
measured tree is **27–28 buffer levels deep** with ~1500 µm average sink
wire — yielding an estimated **~3 ns clock insertion delay** across the die.
That ~3 ns is the root cause of an entire class of timing failures:

- **#25 I/O hold runaway** — inputs constrained at 500 ps vs a ~3 ns capture
  clock → ~1586 I/O hold violations → 95205 hold buffers → `RSZ-0060` build
  death. (Masked in PR #27 by I/O false-paths; the skew is still there.)
- **#27 broadcast setup** — insertion eats most of the period. (Masked by
  relaxing to 300 MHz.)
- **#28 chip_top boundary** — `compute_array`'s `.lib` reports clk-relative
  timing folding in the ~3 ns; chip_top must absorb it as boundary skew.

It is not physics — light crosses 2 mm in ~22 ps. It is accumulated
buffer + thin-metal wire RC in a deep tree. Every block that adds sinks
makes it worse: the methodology does not scale.

### Why not a true clock mesh

Real high-performance parts use a **clock mesh** (a shorted top-metal grid
driven from many points → near-zero skew). **OpenROAD cannot build one:**
TritonCTS assumes a single tree root, OpenSTA cannot time a multi-driver
shorted net, and TritonRoute will not route one. See
[OpenROAD#2202](https://github.com/The-OpenROAD-Project/OpenROAD/issues/2202)
— an open, unbuilt feature request; the OpenROAD lead confirms in-thread
that mesh delay calc "is not currently supported in sta." A research
prototype (Guthaus/UCSC) exists but is unreleased. A literal mesh is out of
scope; if ever required it is a commercial-tool (CCOpt/ICC2) move.

## Solution

Build the achievable equivalent — a **fast, balanced clock tree whose trunk
runs on the thick top-metal layers (M8/M9)** — and make it reusable across
every block. A tree (single root) is fully supported by CTS/STA/route, so it
signs off cleanly.

Four levers:

1. **Trunk on M8/M9.** These layers are empty today (`MAX_ROUTING_LAYER = M7`
   platform-wide; leaf macros use only M3–M6). M8 (horizontal) + M9 (vertical)
   are the thick, low-RC top pair. Two layers are required: each routing layer
   has one preferred direction, so distributing to 2-D-scattered sinks needs an
   orthogonal pair (M9 vertical runs + M8 horizontal runs, stitched by V9).
2. **Wide clock wires** via a non-default routing rule (`-apply_ndr full`).
3. **Balanced tree** (`-balance_levels`, static top levels) — equal depth to
   every sink → low skew.
4. **Recurse at chip_top** + budget the residual per-block internal insertion
   with `set_clock_latency` on macro CLK pins (useful skew).

Targets: insertion **~3 ns → ≤1 ns**, skew **→ ~100 ps**.

The contract: clock becomes **infrastructure** (like the PDN). Designed once
at the top; every block exposes one CLK pin and runs a trivial internal tree.
No block designs a chip-wide clock again. Adding a block adds a small local
tree, not depth to a global one — that is what makes the chip scale.

## File-level changes

New shared scripts under `tech/asap7/orfs/scripts/` (mirrors the existing
`PDN_TCL` / `MACRO_PLACEMENT_TCL` per-block-hook convention):

- **`clock_infra.setrc.tcl`** — adds `set_layer_rc` for **M8, M9, V9**.
  asap7's `setRC.tcl` only defines M1–M7 / V1–V8; **without this, parasitic
  estimation uses defaults for M8/M9 and every insertion/skew number is
  garbage.** Sourced via `SET_RC_TCL` (or appended in the PRE_CTS hook before
  `estimate_parasitics`). Values extrapolated from the M6/M7 trend.
- **`clock_infra.fastroute.tcl`** — wired via `FASTROUTE_TCL`. Raises the
  clock routing range to reach top metal and reserves it from signals:
  `set_routing_layers -signal M2-M7 -clock M5-M9`.
- **`clock_infra.tcl`** — sourced via `PRE_CTS_TCL`. Optional `create_ndr`
  for the clock net; reporting hooks. Driven by env knobs so blocks tune
  without copy-paste.

Per-block knobs (in each tapping block's `*.config.mk`, starting with
`compute_array.config.mk`):

```
export MAX_ROUTING_LAYER = M9
export PRE_CTS_TCL   = /work/tech/asap7/orfs/scripts/clock_infra.tcl
export FASTROUTE_TCL = /work/tech/asap7/orfs/scripts/clock_infra.fastroute.tcl
export SET_RC_TCL    = /work/tech/asap7/orfs/scripts/clock_infra.setrc.tcl
export CTS_ARGS = -sink_clustering_enable -repair_clock_nets \
                  -num_static_layers 3 -apply_ndr full -balance_levels
```
(`CTS_ARGS` overrides the arg list wholesale in `cts.tcl`; tuned during
validation.)

chip_top contract:

- **`chip_top.sdc`** — per-macro-instance `set_clock_latency` budgeting the
  residual internal insertion (prototype already exists in
  `compute_array_tiny.useful_skew_rev.sdc`). This is where boundary skew is
  driven from ~3 ns to ~100 ps (closes #28).

Enforcement of the M8/M9 reservation invariant:

- **Reservation (parent side):** the `set_routing_layers -signal M2-M7`
  above keeps the parent's own signal router off M8/M9.
- **Guard (leaf side):** extend the LEF post-process in `run.sh` (alongside
  `strip_lef_obs_layers.py`) to **error if a leaf abstract LEF references
  M8/M9** (routing or OBS). Today the M7 cap makes this implicitly true
  (leaf LEFs use M3–M6 only); the guard makes a future `MAX_ROUTING_LAYER`
  bump a loud build failure instead of a silent clock-layer poison.
  Note: M8/M9 must be left **open and unused** over macros (so the trunk runs
  straight across), **not** obstructed — the opposite of the M6/M7 PDN case.

## Acceptance criteria

(from issue #33)

1. Parameterizable top-metal clock scripts usable by any asap7 design.
2. `compute_array` (full 32×32): measured insertion **≤ ~1 ns**, skew
   **≤ ~100 ps** (post-CTS report).
3. Per-block CTS report shows a shallow tap-and-fan, not a 27-level H-tree
   (`Number of static layers:` > 0; path depth collapses from ~27).
4. PR #27's I/O false-paths removed on a mesh-based build with timing still
   closing. (The 300 MHz → 400 MHz revert is **separate** — gated on the
   broadcast re-pipelining, not the clock; see Scope below.)
5. Documented "tap the infra" contract: any new block exposes a CLK pin and
   runs a trivial internal tree; no per-block clock-tree topology design.

## Validation

The clock knobs touch only CTS + route (not placement), so:

- **Iterate cheap:** reuse the existing placed ODB (`3_place.odb`) and re-run
  CTS only — ~6–10 min/spin. Read insertion/skew at the `cts final` report.
- **Confirm full:** full route + STA on `compute_array` (~2.5–4 hr; route
  time on this die is the main unknown — it has never routed to completion).
  Then chip_top with the `set_clock_latency` contract.

Exact readouts (post-CTS `4_1_cts.odb`):
- `report_clock_skew -include_internal_latency -digits 3` → skew.
- `report_checks -path_delay max -format full_clock_expanded` → absolute
  insertion to macro CLK pins.
- `report_cts -out_file <f>` and the `Number of static layers:` /
  `Path depth` lines in `4_1_cts.log` → tree structure.
- Confirm CTS no longer hits `RSZ-0060` and hold-buffer count collapses.

Generalize: confirm a second block (e.g. `smem`) taps the same scripts by
setting only the hook env vars, then document the methodology in DESIGN.md.

## Scope (what this does / does not close)

- **Closes:** #25 root cause (build-death), #28 (boundary skew). Lets the I/O
  false-paths be removed.
- **Does NOT close:** the 300 MHz cap — that is a long *data* broadcast wire
  (setup on a ~1.6 mm signal net), not the clock. 400 MHz needs the broadcast
  re-pipelining (issue #27 "option B"), tracked separately. A true mesh
  (#2202) — commercial tools only.

## Proposed decisions (open for review)

- **Compute budget:** iterate CTS-only on the placed ODB, then **one** full
  route+STA confirm (≈ a workday of compute), vs. full-route-every-iteration
  (multi-day, little extra signal). Recommend the former.
- **PR #27 band-aids:** remove the I/O false-paths in this work (justified by
  the lower insertion); keep 300 MHz (its revert is gated on the orthogonal
  broadcast fix).
