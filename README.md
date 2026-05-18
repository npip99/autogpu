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
| `ISA.md`           | Instruction set reference |
| `ARCHITECTURE.md`  | System block diagram, module map, spec format |
| `DEVELOPMENT.md`   | **Read before writing code.** Workflow, TB conventions, debugging, tribal knowledge. |
| `tech/README.md`   | Tape-out flow. Start with `tech/sky130/smoke/` to validate your synthesis toolchain. |

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
