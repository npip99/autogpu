---
name: feedback-rtl-cross-module-latency
description: Multi-module RTL pipelines in this repo: registered-to-registered handoff costs 2 cycles, so SV engines run a few cycles longer than their pymodel; cocotb TBs validate result correctness, not exact cycle counts.
metadata:
  type: feedback
---

When wiring multiple SV modules together (e.g. mma <-> smem <-> tmem in `mma/mma_tb_top.sv`, or store <-> tmem <-> gmem in `store/store_tb_top.sv`), each "1-cycle registered read" port costs 2 cycles end-to-end:

  - Posedge T: consumer drives `rd_en<=1` via NBA.
  - Posedge T+1: producer (memory) samples `rd_en=1`, captures pending read via NBA.
  - Posedge T+2: producer commits `rd_data<=value`, `rd_valid<=1` via NBA.
  - Posedge T+3: consumer's `always_ff` finally observes the new `rd_data` on its RHS.

So a read issued at posedge T is consumed by the issuing module at posedge T+3, not T+1 (which is what the producer's spec advertises). The pymodel uses back-door memory access (zero latency), so a pymodel spec saying "MMA_K+1 cycles" naturally becomes "MMA_K+N for some small N" in RTL.

**Why:** This is fundamental to the synchronous-flop two-module pattern (NBA RHS samples pre-NBA values at each posedge). Combinational `assign`s of port drives could shave a cycle, but registered drives are the project pattern (see `store/store.sv`).

**How to apply:**
  - When writing the cocotb TB for a multi-module engine, do NOT compare cycle-by-cycle to the pymodel. Validate the RESULT (final TMEM tile, final GMEM bytes) against a golden reference (`golden.matmul_reference.generate`, decoded fp8 matmul, etc.).
  - When `done` is held back relative to the pymodel by a few cycles, that's expected, not a bug. The existing `store/tb/test_store.py` does the same — it just waits for `done` to pulse, then checks the data.
  - Note this latency divergence in the SV module header comment so future readers understand why the cycle count differs from the spec.
