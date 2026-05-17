---
name: feedback-verilator-trace-archive-limit
description: Disable Verilator tracing for the full-pipeline (cmdproc) build on macOS; the 32-bit ar archive offset overflows with 1024 fp32_fma instances + per-module trace files.
metadata:
  type: feedback
---

For Verilator builds that include the full pipeline (cmdproc + mma with the 1024-element fp32_fma grid + the 1024-element fp8_encode grid), DO NOT pass `--trace`/`--trace-structs` on macOS. The generated per-module Trace .o files balloon `Vtop__ALL.a` past Apple `ar`/`ranlib`'s 32-bit archive offset limit, producing `malformed archive, member exceeds file size`.

**Why:** macOS `ar` (Xcode CLT) only supports classic-format archives with 32-bit member offsets. Verilator emits one Trace*.o per generated module instance, so wide combinational FMA grids generate thousands of objects (`Vtop__Trace__N.o` up to N=90+ in our cmdproc build), each containing trace metadata that pushes the total archive over 4 GiB.

**How to apply:**
  - `cmdproc/Makefile` already omits `--trace --trace-structs` (with a header comment explaining why).
  - For smaller harnesses (`mma`, `store`, `barrier`, `gmem`, `smem`, `tmem`, `load`), tracing is still fine.
  - If you ever need traces for a cmdproc-style build, either switch to `llvm-ar` (`AR=llvm-ar make`) or split the design into multiple linked artifacts.

Related: [[project-fpnew-vendor]].
