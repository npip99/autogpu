# gpu — minimal fp8 matmul accelerator

A toy fp8 matmul GPU in Verilog. Three core instructions (LOAD, MMA, STORE) plus barriers. Blackwell-style architecture: dedicated tensor memory (TMEM) for accumulators, separate from SMEM operand storage. No general-purpose compute, no branches.

## Status

Bottom-up, pymodel-first build:

| Phase | What | Status |
|-------|------|--------|
| 0 | Toolchain experiment (adder)        | done |
| 1 | Architecture doc + scaffolding      | done |
| 2 | Python behavioral models (pymodel)  | in progress |
| 3 | pymodel end-to-end test passes      | pending |
| 4 | RTL per submodule vs pymodel        | pending |
| 5 | Full RTL integration                | pending |
| 6 | Scale up (K-loop, multi-tile)       | pending |

## Quickstart

```bash
brew install verilator               # 5.x
uv sync                              # creates .venv, installs deps
source .venv/bin/activate

# Toolchain sanity check:
cd experiments/adder
pytest test_pymodel.py               # pymodel correctness
make                                 # cocotb-vs-Verilog comparison
```

Both should pass. Workflow validated.

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
├── common/                    # shared SV (Phase 4) + Python TB helpers
├── smem/, tmem/, mma/,        # one folder per RTL submodule (Phase 4)
│  load/, store/, barrier/,    #   each contains: <sub>.sv + tb/test_<sub>.py
│  cmdproc/
├── top/                       # full RTL integration (Phase 5)
└── experiments/               # throwaway experiments (toolchain, etc)
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
