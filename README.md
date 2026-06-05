# gpu — minimal fp8 matmul accelerator

A toy fp8 matmul GPU in Verilog. Three core instructions (LOAD, MMA, STORE) plus barriers. Blackwell-style architecture: tensor memory (TMEM) for accumulators distributed across per-(i, j) MAC cells inside `compute_array`, separate from SMEM operand storage. No general-purpose compute, no branches.

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
| 7 | Chip boundary + sky130 synthesis    | in progress (7h complete: compute_array refactor for synthesizability) |

**Full pymodel + cocotb suites green. A 32×32×32 fp8 matmul runs end-to-end through real Verilog hardware simulation, bit-exact against numpy.**

### Known issues (asap7 hardening / chip_top)

The asap7 hardening flow currently produces a chip_top LEF (block-level methodology proven end-to-end across 7 hardened leaf macros), but **does NOT close timing or DRC at chip_top scale**. Specifically:

- chip_top.config.mk uses `HOLD_SLACK_MARGIN = -2000` (2 ns on a 4 ns period — half the clock) to mask **final-slack hold violations** that would otherwise prevent DRT from converging. Hold violations on silicon are functional-failure class (data captured before valid). The masked LEF is not foundry-sign-off acceptable.
- `SKIP_INCREMENTAL_REPAIR = 1` accepts a non-converged DRT result.
- First chip_top close: −205 ps setup slack (237 MHz vs 250 MHz target), 1 DRC short on M3 at the cmdproc macro edge.

**Root cause:** hardened macros' `.lib` clock-tree characterization (from `write_timing_model`) doesn't match parent CTS, producing ~1008 ps STA skew that the resizer can't bridge. Tracked in [#52](https://github.com/npip99/gpu/issues/52). The compute_array_abut.sdc multicycle workaround is the symptom of the same problem.

Fix paths: [#52](https://github.com/npip99/gpu/issues/52) (proper .lib characterization), [#50](https://github.com/npip99/gpu/issues/50) (chip-level traveling clock — eliminates parent CTS), [#45](https://github.com/npip99/gpu/issues/45) (BCAST_PIPE absorption — reduces parent CTS endpoint count). Until at least one of these lands, the hardening flow's chip_top output is for *methodology validation only*, not silicon.

## Quickstart

```bash
brew install verilator               # 5.x
uv sync                              # creates .venv, installs deps
source .venv/bin/activate

# Headline: real fp8 matmul through full RTL hardware simulation
cd top && make
# → 6 chip_top e2e tests PASS — random fp8 A,B → fp32 C, exact vs numpy reference

# Python behavioral version (faster, no Verilog):
uv run pytest pymodel/tests/test_e2e.py -v

# Everything:
uv run pytest pymodel/tests/                              # all pymodel tests
for d in gmem smem barrier mac_tmem_cell compute_array load store reset_seq cmdproc; do (cd $d && make); done
```

## Docs

| File | Purpose |
|------|---------|
| `ISA.md`                       | Instruction set reference |
| `ARCHITECTURE.md`              | System block diagram, module map, spec format |
| `DEVELOPMENT.md`               | **Read before writing code.** Workflow, TB conventions, debugging, tribal knowledge. |
| `tech/README.md`               | Tape-out flow. Start with `tech/sky130/smoke/` to validate your synthesis toolchain. |
| `tech/FAILURES.md`             | **Debug lookup.** OpenROAD/OpenLane error codes we've hit, with root causes and fixes. Grep this before re-debugging. |
| `tech/RCA_DISCIPLINE.md`       | **Never-guess diagnosis process.** Read FIRST for any new failure not in FAILURES.md. Every causal claim must cite evidence on the same line. |
| `tech/INVARIANTS.md`           | **High-level build-system + RTL goals** that must always hold (idempotent builds, abutment-only parents, etc.). Each invariant has a "how to check" verifiable line. |
| `tech/asap7/problems/`         | Long-form postmortems for asap7 P&R issues (one file per issue: PDN, hold, LVS, antenna, IR, abutment, …). |
| `tech/asap7/PDK_GAPS.md`       | asap7 PDK limitations that block sign-off (antenna, RC extraction, …). |
| `tech/asap7/DESIGN.md`         | ORFS design constraints, layer-stack decisions, PDN strategy. |
| `tech/asap7/TILE_SPEC.md`      | Boundary contract for abutment-ready tiles (issue #32). |
| `tech/asap7/CHIP_TOP_VIEWER.md`| How to render the `chip_top` GDS into a Google-Maps-style web viewer (KLayout → Leaflet tile pyramid). |

## Layout

```
gpu/
├── ISA.md, ARCHITECTURE.md, DEVELOPMENT.md, README.md
├── config.py                            # canonical M/N/K, SMEM/TMEM sizes (read by Py + SV)
├── pyproject.toml                       # deps managed by uv
├── golden/                              # numpy reference (fp8 + matmul) — ground truth
├── pymodel/                             # cycle-stepped Python behavioral models
│   ├── *.py                             # one file per submodule
│   └── tests/                           # pytest, validates pymodel against spec
├── common/                              # Python TB helpers (tb_utils.py) + vendored fpnew
├── gmem/, smem/, barrier/, load/,       # one folder per RTL submodule
│  store/, reset_seq/, cmdproc/,         #   each contains: <sub>.sv [+ <sub>_tb_top.sv]
│  mac_tmem_cell/, compute_array/        #                  + tb/test_<sub>.py + Makefile
├── top/                                 # chip boundary: chip_top.sv + tb + e2e suite
├── tech/sky130/                         # OpenLane synth flow (per-module + chip_top)
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

LOAD: GMEM → SMEM. `compute_array`: SMEM × SMEM → per-cell TMEM (1024 `mac_tmem_cell` leaves, one fp32 FMA + per-(i, j) slot storage each). STORE: drains TMEM stream → GMEM. WAIT stalls cmdproc on an mbarrier flip. See `ISA.md` for the instruction encodings and `ARCHITECTURE.md` for the dataflow.
