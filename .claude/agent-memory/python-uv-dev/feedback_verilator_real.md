---
name: feedback-verilator-real
description: Verilator does not support `shortreal`/`$bitstoshortreal`; for fp32 arithmetic in SV testbench code, decode bit patterns into `real` manually.
metadata:
  type: feedback
---

When writing SystemVerilog that needs IEEE 754 fp32 arithmetic in this repo (e.g. fp8 encode in `store.sv`, MMA in `mma/mma.sv`), do NOT use `shortreal` or `$bitstoshortreal` / `$shortrealtobits`. Verilator 5.x emits `Unsupported: shortreal being promoted to real (suggest use real instead)` plus WIDTHEXPAND/WIDTHTRUNC warnings around bit casts, and the cumulative warning count trips Verilator's exit-on-warnings threshold.

**Why:** Verilator only supports the 64-bit `real` type. `$bitstoreal` takes 64 bits and `$realtobits` returns 64 bits, so you can't directly bit-cast an fp32 word with the system tasks.

**How to apply:** Write helper SV functions that manually convert between fp32 bit patterns and `real`. For the decode direction, see `store/store.sv::fp32_bits_to_real()` and `mma/mma.sv::fp32_bits_to_real()`. For the encode direction (needed to pack a `real` accumulator back into an fp32 TMEM tile), see `mma/mma.sv::real_to_fp32_bits()` — it handles sign / exp normalization via doubling-halving loops, round-to-nearest-even via integer truncation + fractional remainder, and subnormal / overflow paths. The pymodel side (`golden/fp8.py`, `pymodel/mma.py`) uses np.float32 internally; using `real` (fp64) in SV is slightly higher precision than pymodel's fp32 accumulation, but the per-element answer matches numpy's fp32 matmul output well within the `atol=1e-5` tolerance used in tests.
