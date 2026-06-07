# AutoGPU — engineering status & map

The technical companion to [`README.md`](README.md). What the chip is, where the
build actually stands (honestly), and where every other doc lives.

`autogpu` is a minimal fp8 matmul accelerator in Verilog — three core
instructions (`LOAD`, `MMA`, `STORE`) plus barriers. Blackwell-style: tensor
memory (TMEM) for accumulators distributed across per-`(i, j)` MAC cells inside
`compute_array`, separate from SMEM operand storage. No general-purpose compute,
no branches. `MMA_M = MMA_N = MMA_K = 32`, fp8 inputs, fp32 accumulate (see
`config.py` — the canonical source for both Python and SV).

## Status

Bottom-up, pymodel-first build:

| Phase | What | Status |
|-------|------|--------|
| 0 | Toolchain experiment (adder)        | done |
| 1 | Architecture doc + scaffolding      | done |
| 2 | Python behavioral models (pymodel)  | done |
| 3 | pymodel end-to-end test passes      | done |
| 4 | RTL per submodule vs pymodel        | done |
| 5 | Full RTL integration + e2e matmul   | done |
| 6 | Scale up (K-loop, multi-tile)       | done |
| 7 | Chip boundary + synthesis           | done (sky130 + asap7) |
| 8 | asap7 hardening: leaves → 32×32 array → chip_top | in progress |

**Full pymodel + cocotb suites green. A 32×32×32 fp8 matmul runs end-to-end
through real Verilog hardware simulation, bit-exact against numpy.**

On the physical side: every leaf macro and the full **32×32 `compute_array`
(1089 macros)** harden to a clean `6_final` GDS on the asap7 7nm predictive PDK
via OpenROAD/ORFS — 0 DRC, timing closed, ~40 min wall — using tile abutment
and a source-synchronous "traveling clock" instead of a chip-wide clock tree.
`chip_top` (7 hardened macros integrated) produces a first full LEF but does
**not** yet close timing at chip scale (see below).

### Known issues (asap7 hardening / chip_top)

The asap7 flow currently produces a `chip_top` LEF (block-level methodology
proven end-to-end across 7 hardened leaf macros), but **does NOT close timing
or DRC at chip_top scale**. Specifically:

- `chip_top.config.mk` uses `HOLD_SLACK_MARGIN = -2000` (2 ns on a 4 ns period —
  half the clock) to mask **final-slack hold violations** that would otherwise
  prevent DRT from converging. Hold violations on silicon are functional-failure
  class (data captured before valid). The masked LEF is not foundry-sign-off
  acceptable.
- `SKIP_INCREMENTAL_REPAIR = 1` accepts a non-converged DRT result.
- First chip_top close: −205 ps setup slack (237 MHz vs 250 MHz target), 1 DRC
  short on M3 at the cmdproc macro edge.

**Root cause:** hardened macros' `.lib` clock-tree characterization (from
`write_timing_model`) doesn't match parent CTS, producing ~1008 ps STA skew the
resizer can't bridge. Tracked in [#52](https://github.com/npip99/gpu/issues/52);
the `compute_array_abut.sdc` multicycle workaround is a symptom of the same
problem.

Fix paths: [#52](https://github.com/npip99/gpu/issues/52) (proper `.lib`
characterization), [#50](https://github.com/npip99/gpu/issues/50) (chip-level
traveling clock — eliminates parent CTS), [#45](https://github.com/npip99/gpu/issues/45)
(BCAST_PIPE absorption — reduces parent CTS endpoint count). Until at least one
lands, the chip_top output is **methodology validation only**, not silicon.

## Docs

| File | Purpose |
|------|---------|
| `ISA.md`                       | Instruction set reference |
| `ARCHITECTURE.md`              | System block diagram, module map, spec format |
| `DEVELOPMENT.md`               | **Read before writing code.** Workflow, TB conventions, debugging, tribal knowledge. |
| `tech/README.md`               | Tape-out flow. Start with `tech/sky130/smoke/` to validate your synthesis toolchain. |
| `tech/FAILURES.md`             | **Debug lookup.** OpenROAD/OpenLane error codes hit, with root causes + fixes. Grep before re-debugging. |
| `tech/RCA_DISCIPLINE.md`       | **Never-guess diagnosis process.** Read FIRST for any new failure not in FAILURES.md. Every causal claim cites evidence on the same line. |
| `tech/INVARIANTS.md`           | **Build-system + RTL goals** that must always hold (idempotent builds, abutment-only parents, …). Each has a "how to check" line. |
| `tech/asap7/problems/`         | Long-form postmortems for asap7 P&R issues (PDN, hold, LVS, antenna, IR, abutment, …). |
| `tech/asap7/PDK_GAPS.md`       | asap7 PDK limitations that block sign-off (antenna, RC extraction, …). |
| `tech/asap7/DESIGN.md`         | ORFS design constraints, layer-stack decisions, PDN strategy. |
| `tech/asap7/TILE_SPEC.md`      | Boundary contract for abutment-ready tiles (issue #32). |
| `tech/asap7/CHIP_TOP_VIEWER.md`| Render the chip GDS into a Google-Maps-style web viewer (KLayout → Leaflet tile pyramid). |
| `tech/asap7/viz/README.md`     | 3D routing visualization + extraction-vs-OpenROAD verification. |

## Layout

```
gpu/
├── README.md, ENGINEERING.md, ISA.md, ARCHITECTURE.md, DEVELOPMENT.md
├── config.py                            # canonical M/N/K, SMEM/TMEM sizes (read by Py + SV)
├── pyproject.toml                       # deps managed by uv
├── golden/                              # numpy reference (fp8 + matmul) — ground truth
├── pymodel/                             # cycle-stepped Python behavioral models
│   ├── *.py                             # one file per submodule
│   └── tests/                           # pytest, validates pymodel against spec
├── common/                              # Python TB helpers (tb_utils.py) + vendored fpnew
├── gmem/, smem/, barrier/, load/,       # one folder per RTL submodule
│  store/, reset_seq/, cmdproc/,         #   each: <sub>.sv [+ <sub>_tb_top.sv]
│  mac_tmem_cell/, skew_lane/,           #         + tb/test_<sub>.py + Makefile
│  compute_array/, …
├── top/                                 # chip boundary: chip_top.sv + tb + e2e suite
├── tech/sky130/                         # OpenLane synth flow (per-module + chip_top)
├── tech/asap7/                          # ORFS hardening flow, sign-off tooling, viz
└── experiments/                         # adder experiment (one-time toolchain validator)
```

## Design at a glance

```
   instruction stream
         |
         v
  +---------------+         +-----------+
  | command proc  |<------->| barriers  |
  +-+-----+--------+        +-----+-----+
    |     |       |               ^
    v     v       v               | arrive / tx +/-
  +----+ +-------------+ +-----+  |
  |LOAD| |compute_array| |STORE|--+
  +-+--+ +------+------+ +--+--+
    |       |  ^drain       |
    v       v  │ stream     v
  [GMEM] [SMEM]┴────────►[GMEM]
              (per-cell TMEM)
```

`LOAD`: GMEM → SMEM. `compute_array`: SMEM × SMEM → per-cell TMEM (1024
`mac_tmem_cell` leaves, one fp32 FMA + per-`(i, j)` slot storage each). `STORE`:
drains TMEM stream → GMEM. `WAIT` stalls cmdproc on an mbarrier flip. See
`ISA.md` for the instruction encodings and `ARCHITECTURE.md` for the dataflow.

## Sign-off tooling (asap7)

One-command sign-off wrappers under `tech/asap7/orfs/`, each with the same
exit-code contract (0 = clean, non-zero = per-tool meaning):

| Tool | Checks |
|------|--------|
| `drc.sh`               | KLayout DRC vs the asap7 deck, with an auditable waiver list |
| `lvs.sh`               | layout-vs-schematic |
| `ir_drop.sh`           | static IR-drop vs budget |
| `antenna_check.sh`     | antenna ratios (predictive overlay; PDK has no native rules) |
| `density_check.sh`     | metal density bands |
| `verify_macro_power.tcl` | every macro VDD/VSS pin welded to the parent grid |

See `tech/asap7/PDK_GAPS.md` for what the predictive PDK can and can't sign off.
