# tech/ — process-specific tape-out files

Each subdirectory under `tech/` is one PDK / process target. Files here
SHADOW their generic counterparts at synth time — e.g.,
`tech/sky130/sram_1rw.sv` replaces `mem/sram_1rw.sv` when Verilator's
include path is configured for sky130. Nothing under `tech/` is consumed
by the cocotb simulation flow today.

This directory was created in Phase 7f as a placeholder for what Phase
7g (and beyond) will populate.

## Layout (Phase 7g and beyond)

```
tech/
├── README.md            <- you are here
├── sky130/              <- first PDK target
│   ├── sram_1rw.sv          (sky130 SRAM macro wrapper)
│   ├── pads/                (sky130 I/O pad cells)
│   ├── tech.lib             (timing / process lib references)
│   └── openlane/            (OpenLane config + macros)
├── xilinx/              <- (later) FPGA dev-board integration
│   ├── axi_to_mc.sv         (AXI4-Lite shim for mc_* bus)
│   └── ...                  (MIG DDR controller wrappers)
└── intel/               <- (later) Intel/Altera variant
```

## sky130

First-target PDK. Files we expect to land in 7g:

- **`sram_1rw.sv`** — wraps the OpenRAM / SkyWater SRAM macro for our
  banked SMEM. Same port surface as `mem/sram_1rw.sv` so `smem.sv` does
  not need conditional instantiation.
- **Pad cells** — I/O cell macros for `clk`, `reset_in`, the `mc_*` bus
  (~50 signals, mostly the 128-bit beat data), the chip status pins,
  and the instruction push interface.
- **`tech.lib`** — process timing references (consumed by STA, not
  Verilator).
- **OpenLane config** — flow configuration: clock period, target area,
  power grid spec, macro placement hints.

## Why a separate directory?

Three reasons:

1. **Tape-out files are PDK-licensed.** sky130 is permissive, but
   internal-cell macros are still process-specific and don't belong in
   the generic `mem/` / `common/` paths.
2. **Multiple PDK targets.** As we add Xilinx / Intel FPGA bring-up or
   a second silicon process, each gets its own subdirectory with a
   parallel file structure.
3. **Clear synth path.** The synthesis flow's source list is
   `<generic_files> + <tech/<target>/*>`; the swap-in is mechanical.
