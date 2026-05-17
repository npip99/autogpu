---
name: cross-module-drain
description: After sys_idle high, wait a few extra clock cycles before reading SMEM/TMEM/GMEM in cocotb tests
metadata:
  type: feedback
---

When a cocotb test for a program that ends with `LOAD; WAIT` (no STORE), `sys_idle` going high does NOT guarantee that the engine's SMEM/TMEM writes have committed. Add a small fixed drain loop (~10 RisingEdge cycles) before inspecting memory.

**Why:** `sys_idle = cmdproc.idle && !l_busy && !m_busy && !st_busy` can go high transiently between cmdproc dispatching an engine command and the engine raising busy (1-cycle registered handoff). Even at the point a real wait_done fires, an engine's NBA writes for the final beat may be in-flight. The pattern surfaced when `test_load_reg_off` and `test_alu_addi_loop` saw `sys_idle=1` after only 3 cycles and read stale SMEM bytes from the prior test. Adding `for _ in range(10): await RisingEdge(dut.clk)` before the data check resolved it.

**How to apply:** Apply this for any cocotb test that ends with a LOAD/WAIT pattern and reads SMEM/TMEM directly. Tests ending in STORE don't need the drain — STORE's busy/done stall holds cmdproc until writes have committed, so `sys_idle` is accurate. See DEVELOPMENT.md §"Cross-module registered-handoff latency".
