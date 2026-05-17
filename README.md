# gpu — minimal fp8 matmul accelerator

A toy fp8 matmul GPU in Verilog. Three core instructions (LOAD, MMA, STORE) plus barriers. Blackwell-style architecture: dedicated tensor memory (TMEM) for accumulators, separate from SMEM operand storage. No general-purpose compute, no branches.

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
| 6 | Scale up (K-loop, multi-tile)       | pending |

**64 pymodel tests + 18 cocotb tests, all green. A 32×32×32 fp8 matmul runs end-to-end through real Verilog hardware simulation in 424 simulated clock cycles, bit-exact against numpy.**

## Quickstart

```bash
brew install verilator               # 5.x
uv sync                              # creates .venv, installs deps
source .venv/bin/activate

# Headline: real fp8 matmul through full RTL hardware simulation
cd cmdproc && make
# → test_e2e_matmul PASS — random fp8 A,B → fp32 C, exact vs numpy reference

# Python behavioral version (faster, no Verilog):
uv run pytest pymodel/tests/test_e2e.py -v

# Everything:
uv run pytest pymodel/tests/                              # 64 pymodel tests
for d in gmem smem tmem barrier mma load store cmdproc; do (cd $d && make); done   # all RTL
```

## Docs

| File | Purpose |
|------|---------|
| `ISA.md`           | Instruction set reference |
| `ARCHITECTURE.md`  | System block diagram, module map, spec format |
| `DEVELOPMENT.md`   | **Read before writing code.** Workflow, TB conventions, debugging, tribal knowledge. |

## Layout

```
gpu/
├── ISA.md, ARCHITECTURE.md, DEVELOPMENT.md, README.md
├── config.py                  # canonical M/N/K, SMEM/TMEM sizes (read by Py + SV)
├── pyproject.toml             # deps managed by uv
├── golden/                    # numpy reference (fp8 + matmul) — ground truth
├── pymodel/                   # cycle-stepped Python behavioral models
│   ├── *.py                   # one file per submodule
│   └── tests/                 # pytest, validates pymodel against spec
├── common/                    # Python TB helpers (tb_utils.py)
├── gmem/, smem/, tmem/,       # one folder per RTL submodule
│  mma/, load/, store/,        #   each contains: <sub>.sv [+ <sub>_tb_top.sv]
│  barrier/, cmdproc/          #                  + tb/test_<sub>.py + Makefile
└── experiments/               # adder experiment (one-time toolchain validator)
```

## Design at a glance

```
   instruction stream
         |
         v
  +---------------+      +-----------+
  | command proc  |<---->| barriers  |
  +-+-----+-----+-+      +-----+-----+
    |     |     |              ^
    v     v     v              | arrive / tx +/-
  +----+ +---+ +-----+         |
  |LOAD| |MMA| |STORE|---------+
  +-+--+ +-+-+ +--+--+
    |     |      |
    v     v      v
  [GMEM][SMEM][TMEM][GMEM]
```

LOAD: GMEM → SMEM. MMA: SMEM × SMEM → TMEM (accumulator slot). STORE: TMEM → GMEM. WAIT stalls cmdproc on an mbarrier flip. See `ISA.md` for the instruction encodings and `ARCHITECTURE.md` for the dataflow.
