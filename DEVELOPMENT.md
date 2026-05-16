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
```

If you skip activation, you'll get `cocotb-config: command not found` or `ModuleNotFoundError: No module named 'cocotb_tools'` (system Python 3.14 picked up instead of the venv's 3.12).

### RTL conventions discovered during Phase 4 agent runs

These started as ad-hoc agent decisions and are now project conventions:

- **Reset port on every SV module.** Pymodels don't model `reset` (they construct fresh classes), but the cocotb `tb_utils.reset()` helper drives a `reset` port. Every SV module exposes one: dominant (clears registered outputs + pending state, preserves memory contents). The TB adapter pops `reset` from the kwargs before calling `pymodel.tick()`.
- **Byte-packing for SV↔Python bytes.** SV byte vectors and Python `bytes` round-trip as little-endian: byte 0 in the low 8 bits of the int. SV side: `wr_data[7:0]` is byte 0. Python side: `int.from_bytes(blob, "little")`.
- **TMEM tile packing.** Documented in `pymodel/tmem.py` §"RTL TILE PACKING CONVENTION". Row-major, fp32 LSB-first per word, element `[i][j]` at bit `(i*MMA_N+j)*32`.
- **Write-then-drain forwarding.** Memory modules (gmem/smem/tmem) commit writes BEFORE draining the prior-cycle pending read. So a same-cycle wr + drain-of-pending-rd at the same addr returns the NEW data. Requires byte/element-level write-forwarding muxes on drain paths.
- **Overlap granularity** is byte-range intersection: `[wr_addr, wr_addr+BEAT_BYTES) ∩ [rd_addr, rd_addr+READ_WIDTH) ≠ ∅`. Pymodel asserts on this; the cocotb random test must filter same-cycle wr+rd that would overlap.

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
