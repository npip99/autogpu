---
name: project_phase7h3_drain_stream
description: Phase 7h-3 store↔compute_array drain-stream interface replaces the old 32k-bit one-shot tile path
metadata:
  type: project
---

Phase 7h-3 (completed 2026-05-18) replaced the `mma + tmem` pair in
`chip_top.sv` with a single `compute_array` (1024 mac_tmem_cell leaves +
K-loop + per-row drain mux). STORE's TMEM interface was rewritten from
"capture 32k-bit tile in one cycle" to "consume drain stream row-by-row":
- STORE drives `drain_issue` + `drain_slot` to compute_array on issue.
- compute_array returns one row (MMA_N × 32 = 1024 bits) per cycle, for
  MMA_M cycles, on `drain_row_valid` + `drain_row_data` + `drain_row_idx`,
  with `drain_last` marking the final row and `drain_done` one cycle later.
- STORE's S_GATHER state collects rows into `tile_buf` (32k bits internal),
  then S_FORMAT packs to fp32 or fp8 bytes, then S_DRAIN flushes
  BEAT_BYTES/cycle to GMEM.

**Why:** the old 32k-bit one-shot interface couldn't be physically placed
on a real macro perimeter (sky130 synthesis). The drain-stream lets the
compute_array drive its own internal mux narrow on one side.

**How to apply:** any module that needs accumulator data now goes through
compute_array's drain interface (or its back-door `get_tile()` in pymodel).
TMEM as a separate module is now unused in chip_top — its functionality
lives inside mac_tmem_cell.storage per (i, j) position. tmem/ and mma/
directories still exist but are not wired into chip_top; their Makefiles
exist but test the OLD interface (expected to fail). Phase 7h-4 will
delete them.

**chip_top wiring notes:**
- compute_array.mma_slot and drain_slot are `$clog2(N_SLOTS)` bits; cmdproc
  emits 32-bit so chip_top slices: `.mma_slot (cp_mma_d[$clog2(TMEM_SLOTS)-1:0])`.
- issue_a_stride/issue_b_stride are constants from chip_top (= MMA_M / MMA_N),
  since cmdproc doesn't emit them. Matches the old hardcoded mma.sv.
- compute_array.scrub_en wires to reset_seq.tmem_scrub_en (one-cycle pulse).

**Cycle counts shifted slightly vs pre-7h-3** but result-tile correctness
holds bit-exactly. e2e test went from ~456 → 520 cycles; k_loop_matmul
1219 cycles (pymodel ref 919); all match golden numpy reference.

**STORE_tb_top.sv had to instantiate full compute_array** for the drain
side; this brings 1024 fp32 FMA cells into the store-only TB. Trace was
disabled (same reason as top/Makefile — macOS ar 32-bit offset limit).
