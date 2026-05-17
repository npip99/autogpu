---
name: smem-bank-conflict
description: SMEM bank-decode addr[6:2] groups operand tiles at 128-byte stride into the same 8-bank group → A and B at default offsets conflict every cycle
metadata:
  type: project
---

The 32-bank smem decodes `bank_of(addr) = addr[6:2]`. With our alignment guarantees, conflict between two ports reduces to comparing the 8-bank-group index `addr[6:5]`.

**Surprise:** The canonical matmul layout `A_smem = SMEM_TILE_BASE (128)` and `B_smem = SMEM_TILE_BASE + 1024 (1152)` puts BOTH operand bases in 8-bank group 0 (`addr[6:5]=0` for both). When MMA drives `rd_a` and `rd_b` for the same K column, they always target the same 8-bank group → RD_B stalls every cycle.

**Why this matters:** With fixed priority LOAD_WR > RD_A > RD_B, MMA's two read ports cannot succeed on the same cycle for the canonical layout. The MMA engine has to use a stash/staging protocol (latch rd_a_data when it arrives, hold until rd_b_data also arrives, then accumulate) → ~3 cycles/K-iter instead of 1.

**How to apply:** To get conflict-free matmul, offset `B_smem` so that `B_smem[6:5] != A_smem[6:5]` — e.g., `B_smem = A_smem + 32` shifts B into group 1. But many test programs are constraint-pinned; see `cmdproc/tb/test_cmdproc.py::test_e2e_matmul` and `::test_k_loop_matmul` which compute `B_smem = SMEM_TILE_BASE + MMA_M*MMA_K` and aren't easily moved without changing cmdproc tests. The MMA RTL handles the conflict correctly (with cycle-count slowdown); reported K-loop matmul: 919 → 1313 (+43%).
