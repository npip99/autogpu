# top/ — chip boundary and end-to-end harness

This directory draws the **die boundary** for the toy fp8-matmul GPU.

- `chip_top.sv` — **synthesizable** top of what lives on silicon.
- `tb/chip_tb_top.sv` — **non-synthesizable** testbench wrapper: drops `chip_top` onto a behavioral DRAM (`gmem`) so cocotb has something to drive.
- `tb/test_chip_top.py` — the 6-test cocotb suite that the e2e flow runs through.
- `Makefile` — `make` runs the suite; `make lint` is the synthesizability gate.

The split was introduced in Phase 7f. Before it, `cmdproc/cmdproc_tb_top.sv` mixed RTL with TB-only signals; that file is gone.

## Chip boundary diagram

```
        ┌──────────────────────────────────────────────────────────────┐
        │                          chip_top                            │
        │                                                              │
        │   ┌──────────┐                  ┌─────────────────────┐      │
        │   │ reset_seq│                  │       cmdproc       │      │
        │   └──────────┘                  └─────────────────────┘      │
        │       │                          │         │       │         │
        │  chip_in_reset    init/wait    mma_start  load   store       │
        │       │                          │         │       │         │
        │       ▼                          ▼         ▼       ▼         │
        │   ┌─────────────┐         ┌──────┐   ┌──────┐  ┌──────┐      │
        │   │  smem       │◀────────│ mma  │   │ load │  │store │      │
        │   │ (32 sram_1rw│         │      │   │      │  │      │      │
        │   │   banks)    │─────────│      │   │      │  │      │      │
        │   └─────────────┘         └──────┘   └──────┘  └──────┘      │
        │                            │  │         │       │            │
        │                            ▼  ▼         │       │            │
        │                          ┌────────┐     │       │            │
        │                          │  tmem  │◀────┘       │            │
        │                          │ (flops)│             │            │
        │                          └────────┘             │            │
        │                                                 │            │
        │   ┌─────────────────── mc_* memory-controller bus ──┐        │
        └───┤  mc_rd_en/addr/data/valid     mc_wr_en/addr/data │────────┘
            └──────────────────────┬──────────────────────────┘
                                   │  (off-chip / pads)
                                   ▼
                              ┌──────────┐
                              │   gmem   │   ← behavioral DRAM (testbench),
                              │ (DRAM)   │     replaced by real DDR + AXI
                              └──────────┘     shim at tape-out time.
```

## mc_* memory-controller port contract

A minimal two-channel bus for off-chip DRAM. No bursts, no IDs, no
back-pressure — STORE drives writes the same cycle they're committed,
LOAD pulses `mc_rd_en` one beat at a time and waits for `mc_rd_valid`.

### Read channel (chip → DRAM, DRAM → chip)

| Direction        | Signal           | Width     | Meaning                                  |
|------------------|------------------|-----------|------------------------------------------|
| chip → DRAM      | `mc_rd_en`       | 1         | Pulse high for one cycle per beat        |
| chip → DRAM      | `mc_rd_addr`     | 32        | Byte address (BEAT_BYTES-aligned)        |
| DRAM → chip      | `mc_rd_data`     | BEAT*8    | Beat data; valid only when `mc_rd_valid` |
| DRAM → chip      | `mc_rd_valid`    | 1         | One-cycle valid pulse, 1+ cycles after en|

Today's `gmem.sv` has 1-cycle read latency, but LOAD models the effective
round-trip as 3 cycles to accommodate the registered handoffs (see
`DEVELOPMENT.md` §Cross-module registered-handoff latency).

### Write channel (chip → DRAM)

| Direction        | Signal          | Width    | Meaning                                  |
|------------------|-----------------|----------|------------------------------------------|
| chip → DRAM      | `mc_wr_en`      | 1        | Pulse high for one cycle per beat        |
| chip → DRAM      | `mc_wr_addr`    | 32       | Byte address (BEAT_BYTES-aligned)        |
| chip → DRAM      | `mc_wr_data`    | BEAT*8   | Beat data                                |

Writes are "fire and forget" from the chip side — no completion
response. The behavioral DRAM commits in the same cycle.

## Reset and clock contract

- `clk` is a free-running clock. No clock gating today.
- `reset_in` is an external, active-high, synchronous reset. Internally,
  `chip_top`'s `reset_seq` re-times this and walks every on-chip memory
  through a scrub cycle (zeroes all SMEM banks and TMEM cells) before
  releasing `chip_in_reset`. Pipeline modules see `chip_in_reset`, not
  `reset_in` directly.
- `chip_in_reset` and `scrub_done` are exposed as outputs so the TB can
  wait for the chip to be ready (`common.tb_utils.wait_until_chip_ready`).
- `sys_idle` indicates `cmdproc.idle && !load_busy && !mma_busy &&
  !store_busy`. The TB uses this for end-of-program detection.

## Synthesizability gate: `make lint`

`make lint` runs `verilator --lint-only -Wall --top-module chip_top` over
the entire chip and fails on any warning. Phase 7f removed every
`-Wno-*` flag that affected project RTL.

Vendored CVFPU code (`common/fpnew/*`) still trips Verilator lints
(WIDTHTRUNC, WIDTHEXPAND, ASCRANGE, GENUNNAMED, plus housekeeping
UNUSEDPARAM/UNUSEDSIGNAL). Those warning types are passed through with
narrow `-Wno-*` flags listed as `LINT_FPNEW_WAIVERS` in the Makefile.
They are scoped to fpnew in practice because **none of those warning
types fire anywhere in project RTL** — verified by removing the waivers
and re-running `make lint`; only fpnew warnings reappear.

## Future plan: AXI4-Lite shim

The `mc_*` bus is deliberately bare-metal — not because we want to keep
it forever, but because there's no reason to drag in AXI4 complexity
during pre-silicon bring-up. At integration time we expect to wrap it.

Why **AXI4-Lite**, not full AXI4: we never burst (every transaction is a
single BEAT_BYTES beat), we don't reorder, and we don't have multiple
outstanding requests per channel. AXI4-Lite gives us exactly that
behavior, plus AR/AW/W/B/R handshakes that off-the-shelf DDR controllers
expect. AXI4 (full) adds burst length / ID / cache attributes that
require a multi-beat aware bus master on our side — we'd be doing more
work to translate, for no gain.

When and where:

- **FPGA dev boards** (Xilinx ZynqMP, Intel Cyclone, etc.). DDR IP cores
  speak AXI4 / AXI4-Lite. We'll wrap `mc_*` in
  `tech/xilinx/axi_to_mc.sv` (or `tech/intel/axi_to_mc.sv`) which sits
  between our pads and the vendor's MIG controller.
- **sky130 tape-out.** The first silicon test chip exposes `mc_*` on
  pads directly; an external FPGA / MCU emulates DRAM at low speed
  during bring-up. The AXI shim is not needed on-die until we integrate
  with a real DDR controller on a follow-up chip (or move the controller
  on-die, which would require AXI4 full because the DDR controller
  itself wants bursts internally).

Long-term, the bus shape (single-beat, no reorder) is also a reasonable
fit for OpenRAM-class on-die SRAM L2 if we ever add one — same shim,
different mapping.
