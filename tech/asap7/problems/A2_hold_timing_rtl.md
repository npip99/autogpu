# A2 — Hold-timing closure on compute_array (eliminate HOLD_SLACK_MARGIN workaround)

## Problem

`compute_array` cannot close hold timing on asap7 with the current
hierarchical-hardening flow. Symptoms:

- Hold WNS at end of CTS is ≈ −250 ps on cmd_unit → skew_lane and
  cmd_unit → mac_tmem_cell broadcast paths.
- `repair_timing` cannot close it — it inserts thousands of buffers
  without making progress, then errors out.
- Current workaround in `compute_array_tiny_bcast0.config.mk`:
  `export HOLD_SLACK_MARGIN = -200` tells repair_timing to terminate
  when hold violations are within 200 ps, accepting ~200 ps of negative
  hold slack permanently. On real silicon, broadcast paths would race
  ahead of the receiving clock edge and capture wrong data — chip would
  be functionally broken.

Why the gap is structural (from
`tech/asap7/DESIGN.md` "Hold-timing limitation of hierarchical hardening"):
- Each leaf is hardened standalone with its own internal CTS, which
  picks its own internal clock-insertion delay (~2–3 buffer stages,
  varying by leaf size).
- At the parent (compute_array) level, parent CTS sees the leaf's clock
  pin and adds its own latency to balance.
- Combinational delay between two adjacent macros on the broadcast bus
  is ~50 ps (short wires, no logic in between).
- Inter-macro skew (difference between two adjacent leaves' total clock
  arrival) can be 200+ ps because their internal CTS depths differ.
- Hold slack = comb_delay − skew → deeply negative when skew > comb.

Prior failed attempts (do not repeat):
- **USE_ASAP7_CLK_COMPENSATION ifdef in compute_array.sv** — explicit
  BUFx2 on mac_tmem_cell clock to compensate for shallower CTS. Gave
  ~12 ps improvement on a ~250 ps problem. Polluted RTL. Reverted.
- **Useful-skew SDC** — `set_clock_latency` on destination macros.
  Direction was wrong in initial attempt; even after correction,
  effectively zero improvement. Reverted.
- **BCAST_PIPE parameter** — half-formed RTL pipeline scaffolding;
  reverted (`compute_array_bcast{1,2,3}.config.mk` variants exist in
  staged tree as exploration but the parameter doesn't actually
  pipeline correctly).

## Acceptance criteria

1. `compute_array_tiny_bcast0` reaches `6_final` with
   `HOLD_SLACK_MARGIN` removed (or set to 0) and hold WNS ≥ 0 reported
   in `6_report.log`.
2. Final timing report (`report_check_types -hold`) shows zero hold
   violations on all paths.
3. Setup timing must NOT regress: setup WNS at 1 GHz must remain ≥ 0.
   If your fix adds pipeline stages, setup may improve (good); if it
   adds combinational delay, setup must still close.
4. Functional correctness preserved: a simulation run with your
   modified RTL must produce the same outputs as pre-change for a known
   test vector. Use the sky130 sim infrastructure (`tech/sky130/`) or a
   minimal verilator/iverilog testbench in `compute_array/test/`.

## Constraints

- **Do NOT modify leaf modules** (`mac_tmem_cell.sv`, `skew_lane_a.sv`,
  `skew_lane_b.sv`, `cmd_unit.sv`). They're hardened; touching them
  invalidates the abstract LEFs and forces a re-harden of all leaves
  (hours of compute, and disrupts other Pool-A/B work).
- **Don't change leaf hardening flow** (per-leaf .config.mk / .sdc).
  Same reason.
- **Parameterized for tiny + full.** Whatever you change must work for
  both `compute_array_tiny_bcast0` (4×4) and the full 32×32
  `compute_array.config.mk`. No new MMA_M/N/K-dependent special cases.
- **Latency budget.** Adding pipeline stages delays compute issue by
  1 cycle per stage. cmd_unit issues to compute_array once per matmul,
  so a few cycles of latency is cheap, but document the impact.
- **Don't add gate-level clock-tree primitives in RTL.** No
  `BUFx2_ASAP7_75t_R` direct instantiation. The CTS tool should own all
  clock-tree buffering. (This is what USE_ASAP7_CLK_COMPENSATION did
  and why it was reverted.)
- **Don't use `HOLD_SLACK_MARGIN > 0`, `SKIP_CTS_REPAIR_TIMING`, or
  similar repair_timing escape hatches.** The user has explicitly
  rejected both. The fix must be a real fix.
- **Functional verification before re-hardening.** Don't burn an
  ORFS run cycle without first confirming the RTL still simulates
  correctly. Failed simulation is much cheaper to debug than failed
  silicon.

## Inputs / references

- RTL to edit: `compute_array/compute_array.sv`
- Sub-RTL (read-only): `compute_array/mac_tmem_cell.sv`,
  `compute_array/skew_lane_a.sv`, `compute_array/skew_lane_b.sv`,
  `cmd_unit/cmd_unit.sv` (or wherever cmd_unit lives — confirm path)
- Tiny config: `tech/asap7/orfs/compute_array_tiny_bcast0.config.mk`
  (currently sets HOLD_SLACK_MARGIN=-200; you should be able to remove
  that line and have the design close cleanly)
- DESIGN context: `tech/asap7/DESIGN.md` sections
  "Hold-timing limitation of hierarchical hardening" and
  "Known issues / TODO toward tape-out"
- Sim infra: `tech/sky130/Makefile` (look for `sim`, `verilator`,
  `iverilog` targets)

## Out of scope

- PDN connectivity (A1)
- LVS/antenna/IR sign-off (A3/A4/A5)
- chip_top integration (A6)
- Per-block hardening issues (Pool B)
- Changing the systolic-array compute model
