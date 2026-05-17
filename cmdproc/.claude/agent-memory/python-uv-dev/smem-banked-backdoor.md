---
name: smem-banked-backdoor
description: After smem.sv was rewritten as 32 banks of [31:0], backdoor TB writes use bank_mem[bank][word], not mem[]
metadata:
  type: project
---

The structurally-banked `smem.sv` stores data in 32 separate `bank_mem[bank][word]` 32-bit arrays. A `mem[]` byte view is exposed as a *read-only* combinational alias of the banks for hierarchical reads — backdoor TB *writes* through `mem[]` no longer propagate.

**Why:** Verilator can't write through an `assign`/`always_comb` view; only `logic` storage accepts hierarchical writes from cocotb.

**How to apply:** In any cocotb TB that previously did `dut.u_smem.mem[i].value = byte`, switch to writing `dut.u_smem.bank_mem[bank][word].value` with bank/word/byte-in-dword decoded. Because cocotb hierarchical writes are scheduled (NBA-like), don't do read-modify-write of the same word in a loop — gather byte updates per `(bank, word)` in a Python dict first, then issue one write per word. See `mma/tb/test_mma.py::_seed_smem` and `smem/tb/test_smem.py::_backdoor_load_dut` for the pattern.

Backdoor TB *reads* still use `dut.u_smem.mem[byte_addr].value` — that works fine through the combinational alias.

Also: `reset` clears registered outputs but NOT `bank_mem[]` (preserves memory across resets, matches gmem/tmem convention). The `initial begin` block only zeros banks at sim startup; for tests that follow other tests in the same simulation, **explicitly zero `bank_mem`** to avoid stale data contaminating fresh pymodel comparison.
