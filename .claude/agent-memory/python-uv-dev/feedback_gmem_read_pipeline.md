---
name: feedback-gmem-read-pipeline
description: gmem rd_valid is registered with 1-cycle latency — sample TWO RisingEdges after driving rd_en, not one.
metadata:
  type: feedback
---

When reading gmem from a cocotb testbench (e.g. to verify STORE output): driving `rd_en=1` then `await RisingEdge(clk)` once is NOT enough — `rd_valid` won't be high yet at that ReadOnly point.

**Why:** gmem's `rd_valid` is a registered output. The sequence is: cycle T capture rd_en → cycle T+1 drain pending read → rd_valid high after cycle T+1's rising edge. From a cocotb TB, that means you need TWO `RisingEdge(clk)` after the initial drive before sampling.

**How to apply:** Drive rd_en for cycle T, await RisingEdge (now at start of T+1), then await ANOTHER RisingEdge (now at start of T+2) before `await ReadOnly()` to sample rd_data/rd_valid. For pipelined multi-beat reads, drive beat-(i+1)'s rd_en while waiting for beat-i's data — see `store/tb/test_store.py::_read_gmem` for the working pattern. Same logic applies to any 1-cycle-latency registered-valid memory port (tmem.STORE_RD, smem etc.).
