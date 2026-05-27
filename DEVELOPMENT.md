# Development guide

How we work in this repo. Read this before writing pymodel or RTL.

## Build philosophy

**Pymodel-first, bottom-up.** Three artifacts per submodule, written in this order:

1. **Spec** — docstring at the top of `pymodel/<sub>.py`. Single source of truth for behavior.
2. **Pymodel impl** — Python class. Validated by `pymodel/tests/test_<sub>.py` against the spec.
3. **RTL impl** — `<sub>/<sub>.sv`. Validated by `<sub>/tb/test_<sub>.py` against the pymodel as golden reference.

The pymodel is permanent: even after RTL exists, it remains the cycle-by-cycle reference for every RTL testbench. If pymodel and SV disagree, that's a real bug — never silence it.

**The order matters.** If you find pymodel-vs-SV divergence:
- Wrong SV → fix SV.
- Wrong pymodel → fix pymodel AND update the spec docstring.
- Wrong spec → fix spec first, then bring pymodel and SV into line.

Never have two of (spec, pymodel, SV) say different things.

## Pymodel tick semantics (two-phase)

Each module exposes a `tick()` method representing one rising clock edge.

```
inside tick():
    1. read inputs (passed as arguments)
    2. compute new internal state
    3. commit: update self.<output_attrs> to the new registered values
```

After `tick()` returns, the module's output attributes reflect the **just-latched** register state. Other modules see the new values on their *next* tick. This matches Verilog `always_ff @(posedge clk)` exactly — no combinational paths cross module boundaries.

The `Sim` harness in `pymodel/sim.py` enforces tick order so every module sees registered values, never same-cycle in-progress state.

## Testing

### Two suites

| Suite | Where | What it tests | Tool |
|-------|-------|---------------|------|
| Pymodel unit | `pymodel/tests/test_<sub>.py` | Python class matches spec | pytest |
| RTL vs pymodel | `<sub>/tb/test_<sub>.py` | Verilog matches Python pymodel cycle-by-cycle | cocotb + Verilator |

Both require the venv to be activated. Cocotb's `Makefile.sim` invokes `cocotb-config` and `python3` via subshells, and Make's `export PATH := ...` quirk means we can't auto-fix this from inside the Makefile — venv must be on PATH **before** `make` is invoked.

```bash
source .venv/bin/activate          # activate ONCE per shell session
pytest                             # all pymodel tests
cd <sub> && make                   # one RTL module's cocotb tests
cd top  && make                    # chip-level end-to-end suite (6 tests)
cd top  && make lint               # synthesizability gate (zero-warning lint)
```

If you skip activation, you'll get `cocotb-config: command not found` or `ModuleNotFoundError: No module named 'cocotb_tools'` (system Python 3.14 picked up instead of the venv's 3.12).

### Chip boundary (Phase 7f)

Phase 7f drew the die boundary. The synthesizable top of the chip is `top/chip_top.sv`; the cocotb testbench wrapper that drops it onto a behavioral DRAM (`gmem`) is `top/tb/chip_tb_top.sv`. The 6 end-to-end tests previously in `cmdproc/tb/test_cmdproc.py` now live in `top/tb/test_chip_top.py`. The old `cmdproc/cmdproc_tb_top.sv` and `cmdproc/Makefile` are deleted.

See `top/README.md` for the chip-boundary diagram, the `mc_*` memory-controller port contract, and the future AXI4-Lite shim plan. The synthesizability gate is `cd top && make lint` — zero `-Wno-*` flags on project RTL.

SMEM banks are now `sram_1rw` instances (`mem/sram_1rw.sv`). In Phase 7g, `tech/<process>/sram_1rw.sv` will replace this with the vendor SRAM macro at synth time.

### RTL conventions discovered during Phase 4 agent runs

These started as ad-hoc agent decisions and are now project conventions:

- **Reset port on every SV module.** Pymodels don't model `reset` (they construct fresh classes), but the cocotb `tb_utils.reset()` helper drives a `reset` port. Every SV module exposes one: dominant (clears registered outputs + pending state, preserves memory contents). The TB adapter pops `reset` from the kwargs before calling `pymodel.tick()`.
- **Byte-packing for SV↔Python bytes.** SV byte vectors and Python `bytes` round-trip as little-endian: byte 0 in the low 8 bits of the int. SV side: `wr_data[7:0]` is byte 0. Python side: `int.from_bytes(blob, "little")`.
- **TMEM tile packing.** Row-major, fp32 LSB-first per word, element `[i][j]` at bit `(i*MMA_N+j)*32`. Pre-Phase-7h this lived in a monolithic `tmem` module; today the equivalent storage is distributed across 1024 `mac_tmem_cell` leaves inside `compute_array`, and STORE consumes the drain *stream* — one element per cycle — instead of reading a full tile.
- **Write-then-drain forwarding.** Memory modules (gmem/smem/tmem) commit writes BEFORE draining the prior-cycle pending read. So a same-cycle wr + drain-of-pending-rd at the same addr returns the NEW data. Requires byte/element-level write-forwarding muxes on drain paths.
- **Overlap granularity** is byte-range intersection: `[wr_addr, wr_addr+BEAT_BYTES) ∩ [rd_addr, rd_addr+READ_WIDTH) ≠ ∅`. Pymodel asserts on this; the cocotb random test must filter same-cycle wr+rd that would overlap.
- **Cross-module registered-handoff latency**: when an engine (MMA / LOAD / STORE) drives a registered port (e.g. `rd_en`) consumed by a registered memory (e.g. `gmem` / `smem` / `tmem`), the round-trip takes **3 cycles end-to-end**, not the producer's documented 1: posedge T (engine drives `rd_en<=1`), T+1 (memory captures pending), T+2 (memory commits `rd_data<=`, `rd_valid<=1`), T+3 (engine's `always_ff` observes new `rd_data`). The pymodel uses back-door access (zero latency), so any pymodel spec saying "N cycles" becomes "N + a few" in RTL.
  - **TB consequence**: validate the RESULT (final TMEM tile, final GMEM bytes), not exact cycle counts. STORE/MMA/LOAD all match pymodel on contract signals (accept/busy/done/barrier pulses) but data movement lags by a few cycles.
  - **CMDPROC consequence**: wait on `done` pulses to advance state — do not count cycles. The pymodel's exact-latency assertions don't transfer to RTL.
  - Combinational port drives could shave a cycle, but registered drives are the project pattern (see `store/store.sv`).

### Canonical cocotb compare-loop

Every Phase 4 RTL testbench follows this exact recipe:

```python
from common.tb_utils import start_clock, reset, step_and_compare

@cocotb.test()
async def test_random_vs_pymodel(dut):
    await start_clock(dut)
    await reset(dut)
    py = PymodelClass()

    for _ in range(N):
        inputs  = { "en": rng.randint(0,1), "a": rng.randint(0,255), ... }
        outputs = ["sum", "valid", ...]
        await step_and_compare(dut, py, inputs, outputs)
```

`step_and_compare` does: drive inputs → `RisingEdge` → `py.tick(**inputs)` → `ReadOnly` → assert each output matches → `NextTimeStep`.

### Why NextTimeStep matters (the ReadOnly gotcha)

`await ReadOnly()` lands in the simulator's **postponed region** — all NBAs have committed, values are "final" for this timestep. Writing a signal here would force re-evaluation and break determinism. cocotb 1.x silently allowed it; **cocotb 2.0 raises `RuntimeError`**.

```python
await ReadOnly()
assert int(dut.x.value) == ...    # ok, reads allowed
dut.y.value = ...                 # ERROR: Attempting settings during ReadOnly
```

The fix: `await NextTimeStep()` after sampling, before the next iteration writes. `step_and_compare` does this for you. If you bypass the helper, remember the rule.

### Debugging pymodel-vs-RTL mismatch

1. **Reproduce** with the same RNG seed. Add `--trace` (already in the experiment Makefile) and inspect the VCD with GTKWave.
2. **Locate** the first divergence cycle. cocotb's assertion message includes the cycle number and inputs.
3. **Classify**: spec bug, pymodel bug, or SV bug? Use the order rule above — never silently change pymodel to match SV without updating the spec.

## Naming conventions

- Module names: `<sub>` (single word, lowercase). Files: `pymodel/<sub>.py`, `<sub>/<sub>.sv`, `<sub>/tb/test_<sub>.py`.
- Port names match **exactly** between pymodel and SV. This lets `step_and_compare` use string-keyed access generically.
- Parameter constants live in `config.py` (Python-readable) and a generated `common/config.svh` (Verilog `define) — never hardcode `32`, `16`, etc. in module code.
- pymodel output attributes are named after the SV port (e.g. `dut.sum` ↔ `py.sum`). Drives the same uniformity.

## Path conventions

- **Never hardcode absolute repo paths.** Scripts, configs, and TCL must resolve the repo root from their own location (e.g. `Path(__file__).resolve().parents[N]` in Python, `cd "$(dirname "${BASH_SOURCE[0]}")/.."` in shell, `[file dirname [info script]]` in TCL). The repo is sometimes checked out as parallel worktrees on the same host; a hardcoded absolute path silently makes the script useless in any other worktree (or worse — quietly writes outputs into the wrong worktree).
- **The only acceptable absolute paths** are docker container mount points (`/work`, `/OpenROAD-flow-scripts/...`, `~/.volare/...`) and tool installation paths (`/usr/local/bin/sv2v`). These are not repo-relative.
- **ORFS `config.mk` files reference `/work/...`** because the docker mount maps `$REPO_ROOT:/work` — that's a container path, not a host path, so it stays the same across worktrees. Don't replace these with host paths.
- **Test before committing a new script** that you can run it from a different worktree without it touching the original worktree's tree. The check: `cd ../gpuN && /path/to/your/script.py` should write into `../gpuN`'s build tree, not the repo where the script lives.

## Toolchain notes

- **Python**: `uv sync` installs from `pyproject.toml`. Target Python 3.12 (3.14 not yet supported by cocotb 2.0).
- **Verilator**: 5.x via `brew install verilator`.
- **cocotb 2.0** breaking changes vs 1.x: `unit=` replaces `units=`; `MODULE` is deprecated for `COCOTB_TEST_MODULES`; ReadOnly is strict (above).
- VCD waveforms land in `sim_build/dump.vcd` when `--trace` is set in the Makefile.

## Spec format (per-submodule docstring)

Every `pymodel/<sub>.py` opens with a docstring in this shape — see `pymodel/gmem.py` for the worked example.

```
<name> — <one-line purpose>

INPUTS (sampled at tick start)
    <name> : <width/type> — <meaning>

OUTPUTS (valid after tick, registered)
    <name> : <width/type> — <meaning>

INTERNAL STATE
    <name> : <type> = <init> — <meaning>

BEHAVIOR (per tick, two-phase)
    sample phase: <what gets captured>
    commit phase:
        1. <rule>
        2. <rule>

INVARIANTS
    - <statement that always holds>

HANDSHAKE
    <start/done pulse semantics>

TEST CASES (in pymodel/tests/test_<sub>.py)
    1. <scenario> → <expected outcome>
```

If a module's BEHAVIOR section grows past ~10 rules, it's doing too much — split it.

## Phase 7h notes — compute_array refactor

Phase 7h replaced the old monolithic `mma` + `tmem` pair with two new
modules:

- `mac_tmem_cell/mac_tmem_cell.sv` — single leaf cell: one fp32 FMA +
  per-(i, j) TMEM micro-storage for `TMEM_SLOTS` slots. The full TMEM
  is now distributed across 1024 of these (`MMA_M × MMA_N`), one per
  MAC position.
- `compute_array/compute_array.sv` — wraps the 1024 leaves with the
  K-loop, A/B broadcast network (one SMEM row + one SMEM column per
  cycle), and the drain stream out to STORE.

**Module hierarchy:**

```
chip_top
├── cmdproc
├── smem
├── barrier
├── reset_seq
├── load
├── compute_array          ← Phase 7h-2 (replaces mma + tmem)
│   └── mac_tmem_cell × 1024  ← Phase 7h-1 (leaf)
└── store
```

**Why we moved away from `mma` + `tmem`.** The old design had `tmem`
expose a single wide RMW port (`MMA_M × MMA_N × 32 = 32768 bits`) to
`mma`. That interface didn't synthesize at sky130 — every net was
shared across all 1024 cells, so the place-and-route fan-out blew up
on AREA-3 with no realistic timing path. Pushing the FMA *and* its
per-position storage into a single leaf eliminates that interface
entirely; the wide bus dissolves into 1024 local cell-internal nets,
and the per-cell module is small enough that synth converges per
hierarchy instance and ABC only optimizes the leaf once.

**Drain stream contract (compute_array → store).** STORE no longer
reads a 32k-bit tile from TMEM in one shot. The new contract: STORE
asserts `drain_start` with a slot id; `compute_array` then walks the
1024 cells one element per cycle, producing
`drain_valid + drain_data[31:0]`. STORE collects the elements into
its outbound BEAT_BYTES write packets. See `store/store.sv` and
`compute_array/compute_array.sv` for the exact handshake.

**Signal naming.** `chip_top` still exposes the legacy `mma_*` /
`tmem_*` port names (`mma_busy`, `mma_done`, `TMEM_SLOTS`, …) — they
are the *public contract* of the chip, not a reference to the old
modules. The cmdproc instruction class `Mma` is also unchanged — it's
the ISA-level matmul instruction, not the dead module.

## fpnew reset audit (Phase 7e)

Phase 7e replaced the simulation-only `initial begin` zero-init blocks in
`smem.sv` and the (since-removed in Phase 7h) `tmem.sv` with a real
`reset_seq` module that scrubs on-chip memories before deasserting
`chip_in_reset`. As part of that work we audited the vendored CVFPU
(`common/fpnew/*`) for FFs that lack reset paths.

**Result: no live FFs in our integration.**

Reasoning:

- We instantiate `fpnew_fma` from `common/fp32_fma.sv` with `NumPipeRegs=0`.
- All FFs in `fpnew_fma.sv` live inside three `for (genvar i = 0; i < NUM_*_REGS; i++)`
  generate blocks (`gen_input_pipeline`, `gen_mid_pipeline`,
  `gen_output_pipeline`). With `NumPipeRegs=0`, these iterate zero times
  and no FFs are instantiated.
- `fp32_fma.sv` ties `clk_i = 1'b0` and `rst_ni = 1'b1` for exactly this
  reason — there are no clocked elements to drive.
- The remaining vendored files (`fpnew_classifier.sv`, `fpnew_rounding.sv`,
  `lzc.sv`, `cf_math_pkg.sv`) are pure combinational logic (`always_comb`
  only).
- The macros in `common_cells/registers.svh` (`FF`, `FFL`, `FFAR`, …) DO
  use asynchronous active-low reset (`rst_ni`) — these would have been
  fine for our active-high synchronous reset convention if any were live,
  but none are.

**Status flagged for synthesis hardening:**

If a future phase ever bumps `NumPipeRegs > 0` (to register inputs / mids /
outputs of the FMA), every pipeline register would come up in X-state on
power-on with no reset path back to a defined value. At that point
`reset_seq` would also need to gate `clk_i` of fpnew or drive `rst_ni`
synchronously, and `fp32_fma.sv` would have to surface `clk_i`/`rst_ni`
as actual ports rather than tied-off constants.

Acceptable for sim today. Flagged for synthesis hardening when fpnew
pipeline registers come online.
