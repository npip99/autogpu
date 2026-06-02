# B2 — Hold-timing closure on smem (RESOLVED 2026-05-28 via SDC alignment)

## Problem

`smem` cannot close hold timing on asap7 with the current hierarchical-
hardening flow. Same root cause as A2 on compute_array, recurring on a
different macro hierarchy.

Symptoms (smem ORFS run, `4_1_cts.tmp.log`):

- CTS `repair_timing` plateaus with hold WNS ≈ −340 ps and refuses to
  converge — runs for 1500+ iterations, inserting buffers without
  making progress on WNS (TNS continues to creep, slack does not).
- Current workaround in `tech/asap7/orfs/smem.config.mk`:
  `export HOLD_SLACK_MARGIN = -400` lets `repair_timing` exit when
  violations are within 400 ps. The slack remains in the design. On
  real silicon, paths between adjacent `smem_bank` macros under the
  parent's bank_rdata fan-in would race the receiving clock edge and
  capture wrong data — chip would be functionally broken.

Why this is structural (same mechanism as A2, scaled up):
- B1 hardens 16 `smem_bank` instances, each with its own internal CTS
  (depth ~2–3 buffer stages, varies by per-bank routing).
- At smem parent level, CTS sees each smem_bank's clock pin and adds
  parent latency. Inter-macro skew between adjacent banks under the
  parent tree can be 200+ ps.
- The narrow `MACRO_PLACE_CHANNEL = 10 10` (intentional, to keep
  bank_rdata wires short — B1 fix) means inter-bank combinational
  paths are very short — ~30 ps. Hold slack = comb_delay − skew goes
  deeply negative.

The compute_array A2 fix (2.5 ns SDC; the BCAST_PIPE half of the
original A2 fix was later deleted in #45 once B6's per-skew abutment
chain absorbed the broadcast pipeline) doesn't directly apply: smem's
hot paths are not a broadcast bus but rather the per-bank rd_a/rd_b
address path (parent → bank) and the per-bank output path (bank →
consumer). Pipelining either adds a cycle of read latency, which would
propagate to chip_top's compute_array→smem read loop and require
pymodel + cocotb updates.

## Resolution (2026-05-28)

**Resolved by SDC alignment — no RTL change needed.** The root cause was
not the hierarchical-CTS skew per se; it was that `smem.sdc` targeted
1 GHz (1000 ps) while the chip-wide target is 2500 ps (compute_array) /
4000 ps (chip_top). At 1 GHz:
- setup was −253 ps (the `wr_addr`→bank address-decode arc), and
- the resizer could not fix hold, because every hold-delay buffer it
  added pushed an already-tight setup path into violation. So hold
  stayed at −217 ps and only the −400 ps margin let CTS exit.

Relaxing `smem.sdc` to 2500 ps (matching `compute_array.sdc`) closes
**both**, verified on a full ORFS run (was the `smem_slow` experiment,
now folded into the canonical `smem.config.mk`/`smem.sdc`):

| | setup WNS | hold WNS | fmax | DRC |
|---|---|---|---|---|
| 1 GHz (old smem.sdc) | −253 ps | −217 ps (−400 margin) | — | 0 |
| 2500 ps (current) | **+219 ps** | **+58 ps** | 438 MHz | 0 |

(Numbers re-validated on the corrected 8 KB / 128-word build — the
earlier figures were measured at the mis-sized 16384/256-word config;
hold is identical at +58 ps, setup and slew improved slightly with the
smaller address-decode logic.)

Mechanism for hold: hold-check arithmetic is period-independent, but the
*ability to repair* hold is not. At 2500 ps setup has hundreds of ps of
slack, so post-route `repair_design`/`repair_timing` freely inserts the
hold buffers that the 1 GHz run couldn't afford. Final hold = +58 ps.

`HOLD_SLACK_MARGIN=-400` is **kept** in `smem.config.mk`, but only as a
CTS-stage convergence aid (lets CTS exit promptly instead of churning on
the still-skewed CTS-stage hold paths). It does **not** appear in final
slack. Removing it entirely is untested — may reintroduce CTS churn — so
it stays as a fast-exit aid, not a slack compromise. The
candidate "BCAST_PIPE-style" RTL fix below is therefore **no longer
needed** for hold closure; kept for reference only. (Note: the
compute_array-side BCAST_PIPE itself was deleted in #45 — see
`compute_array.sv` header for the rationale.)

### Residual: max-slew (139 violations) — minor, smem-internal

The 2500 ps run leaves **139 max-slew (max-transition) violations** vs
the asap7 320 ps library limit (worst ~417 ps), on `/B` inputs of
`AND3x1` cells in the bank address/enable-decode logic. The 1 GHz run
had 46; the relaxed clock made it *worse* because the resizer, flush
with setup slack, buffers less aggressively. These are **smem-internal
nets** — once smem is a hardened macro, chip_top's STA sees only the
boundary-pin `.lib` arcs (black box), so internal slew does not
propagate to chip_top timing. Low priority. Fix path: a slew-targeted
`repair_design` pass on the routed ODB (incremental, ~10–15 min, no full
rerun). Do NOT relax the 320 ps limit — it's the real library
`max_transition`, not an ORFS knob.

## Candidate fixes (historical — superseded by the SDC fix above)

1. **Pipeline the bank address path inside `smem.sv`.** Add a 1-cycle
   register on `rd_a_addr` / `rd_b_addr` between the smem parent and
   the smem_bank instances (and matching 1-cycle delay on the response
   side so consumers see the same total latency). This was the direct
   analog of compute_array's historical BCAST_PIPE=1 (deleted in #45
   when B6's per-skew abutment chain made parent flops redundant).
   Needs pymodel update (`pymodel/smem.py`) so cocotb cycle-lockstep
   tests still pass.
2. **Relax SDC.** smem currently targets the same period as
   compute_array. If A2's resolution (400 MHz) is the chip-wide target,
   smem's SDC should match. May or may not be enough on its own —
   historically compute_array was thought to need both BCAST_PIPE and a
   relaxed SDC; #45 showed the relaxed SDC alone carries closure.
3. **Skew-aware CTS at smem parent.** `CTS_CLUSTER_DIAMETER`/`SIZE`
   retunes, or hierarchical CTS methodology. A2 found these
   ineffective on compute_array; smem has a smaller macro count so
   results may differ — worth a quick experiment before committing to
   the RTL change in #1.
4. **Useful-skew SDC on smem_bank instances.** Apply
   `set_clock_latency` to push receiving banks' clock arrival later
   so hold closes. A2 found this gave ~0 improvement on compute_array
   but the structure is different here (fewer macros, simpler topology)
   — also worth a quick experiment.

## Things NOT to try

Same list as A2:
- USE_ASAP7_CLK_COMPENSATION-style RTL clock buffers — pollutes RTL,
  CTS tool should own clock buffering.
- Reversed useful-skew SDC (kept at
  `compute_array_tiny.useful_skew_rev.sdc`) — A2 found no gain.
- `-macro_clustering_size 32 -macro_clustering_max_diameter 2000`
  passed via CTS_ARGS — A2 crashed OpenROAD CTS.

## Acceptance criteria

1. `smem` reaches `6_final` with hold WNS ≥ 0 reported in
   `6_report.log`. RESOLVED via the 2500 ps SDC alignment (see above),
   not by touching `HOLD_SLACK_MARGIN`. `HOLD_SLACK_MARGIN=-400` is
   **kept** in `smem.config.mk` purely as a CTS-stage fast-exit aid; it
   does not appear in final hold slack (final hold = +58 ps with it
   present). Removing it is untested and may reintroduce CTS churn.
2. Final timing report (`report_check_types -hold`) shows zero hold
   violations on all paths.
3. Setup timing must NOT regress at the chip-wide target period.
4. Functional correctness preserved: cocotb `smem/tb/test_smem.py`
   and `pymodel/tests/test_smem.py` must still pass with any pymodel
   latency update applied to match.
5. chip_top re-run with the new smem.lef must also close hold cleanly
   (the workaround currently propagates upward; closing at smem level
   should close chip_top automatically since the rest already meets).

## Constraints

- **Do NOT modify `smem_bank.sv`** without re-hardening the bank LEF.
  Each smem_bank re-harden adds ~10 min wall.
- **Region-partition decode must be preserved.** B1's direct
  bank[d].rd_a_out[d] → rd_a_data wires depend on the
  `bank_of(addr) = {addr[12], addr[4:2]}` partition. Pipelining
  shouldn't disturb this.
- **2-region invariant.** Pymodel + RTL + cocotb tests all assume
  RD_A → region 0, RD_B → region 1, LOAD-of-A → region 0,
  LOAD-of-B → region 1. Don't widen.

## Inputs / references

- RTL to edit: `smem/smem.sv` (and possibly `pymodel/smem.py` to match)
- Sub-RTL (read-only, hardened): `smem/smem_bank.sv`
- Config: `tech/asap7/orfs/smem.config.mk`
  (`HOLD_SLACK_MARGIN=-400` kept as a CTS-stage fast-exit aid — see
  acceptance criterion 1; not removed)
- A2 resolution writeup: `tech/asap7/problems/A2_hold_timing_rtl.md`
  — read this first; the technique transfers directly.
- DESIGN context: `tech/asap7/DESIGN.md` sections
  "Hold-timing limitation of hierarchical hardening" and
  "Known issues / TODO toward tape-out".

## Out of scope

- PDN connectivity (A1, B1)
- chip_top integration (A6)
- Other smem-flow issues (B1 — closed)
