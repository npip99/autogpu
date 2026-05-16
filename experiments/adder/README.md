# Adder experiment

Throwaway prototype that validates the full workflow we'll use for every GPU module:

```
spec docstring  ──→  pymodel.py  ──→  test_pymodel.py     (Phase 2/3 pattern)
                          │
                          │  (golden reference)
                          ↓
                       tb.py     ←──  adder.sv             (Phase 4 pattern)
```

## Files

| File | Role |
|------|------|
| `pymodel.py` | Spec docstring + Python class. Behavioral reference. |
| `test_pymodel.py` | pytest validating the pymodel matches the spec. |
| `adder.sv` | The Verilog implementation. Must match the pymodel. |
| `tb.py` | cocotb testbench. Drives random stim to both SV and pymodel, asserts outputs match every cycle. |
| `Makefile` | cocotb's standard Verilator-backed driver. |

## Running

```bash
# 1. Validate the pymodel against the spec
make pytest

# 2. Validate the SV against the pymodel (drives both in lockstep)
make
```

Both should pass. If `make` fails with "cocotb-config: command not found", install:

```bash
pip install cocotb
brew install verilator
```

## What success means

- `pytest` passes → pymodel is correct against spec.
- `make` passes → SV produces identical outputs to pymodel under 500 random cycles.

When both pass, we know:
1. macOS toolchain (Verilator + cocotb + Python) works end-to-end.
2. The pymodel two-phase tick model lines up with SV's registered semantics.
3. cocotb's `RisingEdge`/`ReadOnly` sampling matches `tick()` timing.
4. We can use this exact recipe for every real GPU module.

## What this experiment intentionally doesn't test

- No multi-module wiring (one module only).
- No async issue / barriers (combinational gate).
- No memory or wide data paths.
- No parameterization.

Those concerns surface naturally when we apply this recipe to the actual GPU modules.
