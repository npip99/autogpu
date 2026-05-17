---
name: project-reset-seq-scrub
description: Phase 7e reset sequencer + on-chip memory scrub design choices and pitfalls
metadata:
  type: project
---

Phase 7e (May 2026) added a synthesis-ready reset_seq module that replaces
the simulation-only `initial begin` zero-init in smem.sv / tmem.sv. SMEM
scrub walks 128 per-bank word indices in parallel across all 32 banks
(SMEM_BYTES=16384 / 32 banks / 4 bytes = 128 cycles). TMEM uses a
one-cycle parallel scrub since it's FFs.

**Why:** GMEM is off-chip and stays out of the scrub path (its
testbench-only `initial begin` is preserved). The brief made this
explicit; don't ever try to scrub gmem from reset_seq.

**How to apply:** 
- reset_seq is only instantiated in cmdproc/cmdproc_tb_top.sv (full-system).
  Per-module TB wrappers (mma_tb_top, store_tb_top, load_tb_top) tie
  smem.scrub_en=0 / tmem.tmem_scrub_en=0 and back-door zero
  bank_mem/cells from Python at TB start.
- common.tb_utils.wait_until_chip_ready(dut) polls dut.chip_in_reset
  until it drops; call it after `reset()` in every cmdproc TB test.
- cmdproc_tb_top exposes chip_in_reset and scrub_done as top-level
  outputs so wait_until_chip_ready can sample them combinationally.

**Pitfalls hit during implementation:**
- The first attempt put the FSM transition to S_RUN on the *same* cycle
  as the final scrub addr write — that cuts off the last write. Correct
  timing: drive scrub_addr=depth-1 on cycle N, transition to RUN on
  cycle N+1.
- Verilator 5.x flags `if (scrub_addr_q >= SCRUB_DEPTH - 1)` as a width
  mismatch (LHS=7 bits, RHS=32). Use explicit width cast:
  `if (scrub_addr_q == ($clog2(SCRUB_DEPTH))'(SCRUB_DEPTH - 1))`.
- Removing `initial begin` from smem.sv / tmem.sv means any TB that
  doesn't drive the scrub port must back-door zero bank_mem/cells from
  Python before the first cycle that reads them. The mma_tb_top wrapper
  was the one tripped — `_zero_on_chip_mem(dut)` after reset fixes it.
