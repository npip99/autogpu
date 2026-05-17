---
name: project-fpnew-vendor
description: Project vendors fpnew (CVFPU) + minimal common_cells subset for synthesizable fp32 FMA in mma/store; lives at common/fpnew/.
metadata:
  type: project
---

The project vendors a SHL-0.51-licensed subset of pulp-platform/cvfpu (CVFPU) and pulp-platform/common_cells into `common/fpnew/`, then layers two tiny wrappers on top (`common/fp32_fma.sv`, `common/fp8_decode.sv`, `common/fp8_encode.sv`) to produce a fully synthesizable replacement for the simulation-only `real`/`shortreal` arithmetic that used to live inside `mma/mma.sv` and `store/store.sv`.

**Why:** The earlier MMA/STORE engines used `real` for fp8*fp8->fp32 accumulation and fp32->fp8 encoding (LUT-based argmin). Neither is synthesizable; the new path is.

**How to apply:**
  - `mma/mma.sv` now uses one combinational `fp32_fma` per MAC and a per-row/col `fp8_decode`. With `MMA_M=MMA_N=32` that's 1024 FMAs + 64 decoders.
  - `store/store.sv` instantiates `MMA_M*MMA_N` parallel `fp8_encode`s and selects between the fp32 raw bits and the encoded bytes via a packing function.
  - Cycle behaviour is unchanged from the prior pymodel-equivalent design (still validated at atol=1e-5).
  - For any TB that includes the full pipeline (cmdproc): set `-Wno-UNOPTFLAT -Wno-WIDTHTRUNC -Wno-ASCRANGE -Wno-WIDTHEXPAND -Wno-SPLITVAR` and add `+incdir+<path>/common/fpnew` so `include "common_cells/registers.svh"` resolves.

Related: [[feedback-verilator-trace-archive-limit]].
