---
name: drain-pipeline-two-stage
description: compute_array's drain mux needs 2 pipeline stages (s1, s2) because cell.drain_data is a registered output, not combinational
metadata:
  type: feedback
---

When wrapping `mac_tmem_cell` (or any leaf with a registered drain output) with a drain mux that streams row-by-row, the wrapper must add TWO pipeline stages before assembling the row data — not one.

**Why:** mac_tmem_cell has a 1-cycle drain latency: drive `drain_en=1` at cycle T → `cell.drain_data` valid at cycle T+1. The wrapper wants to register `drain_row_data` (a wide packed vector) — but a same-cycle NBA cannot read the post-edge value of another NBA. So:
  - Cycle T: drive `drain_en` to cell.
  - Cycle T+1: cell.drain_pending captured, but cell.drain_data still old (commits at next edge).
  - Cycle T+2: cell.drain_data has committed storage[slot]; combinationally readable.

The naive "1-stage pipeline" (track only "drain_en was issued last cycle, sample drain_data now") registers ZEROS, because cell.drain_data is still the old value when sampled. You see this as `drain_row_data == 0` even though `drain_row_valid == 1`.

**How to apply:** In compute_array.sv (and any equivalent leaf-cluster module), use registered `s1_valid/s1_row/s1_last` and `s2_valid/s2_row/s2_last`. Stage advance: `s2 <= s1; s1 <= (drain_issue_now ? row : 0)`. Drive `drain_row_*` COMBINATIONALLY from s2_* and cell.drain_data — not registered — because cell.drain_data is already a registered output (so the combinational output still changes synchronously). The pymodel must mirror this 2-stage pipeline EXACTLY for cycle-by-cycle lockstep.

Related: [[rtl-cross-module-pipeline-latency]] — same root cause (NBAs can't read each other's post-edge values within one edge).
