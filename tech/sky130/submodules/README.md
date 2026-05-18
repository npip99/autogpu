# Per-submodule sky130 synthesis runs

Each directory under here synthesizes ONE module of chip_top in isolation —
producing its own GDS, area report, and timing report. Useful for:

- Bringing up the flow on a small module before debugging chip_top issues
- Per-module area / timing numbers (real, not synth-cell-count estimates)
- "Hardening" each module as a vendor macro for hierarchical synthesis
- Isolating which module (if any) is the routing bottleneck

## How it works

All submodule configs reuse the **same** sv2v-converted Verilog file at
`build/sv2v/chip_top.v`. OpenLane picks the chosen `DESIGN_NAME` from
within that file as the top module for that run.

Same speed flags as the chip_top config: `AREA 3` ABC strategy, no flatten,
no SHARE, no timing-driven placement. Macros and parameters are tuned per
module where needed (e.g., `smem` needs the SRAM macro; others don't).

## Run

From this directory:

```bash
./run.sh fp32_fma         # ~5-10 min, one of the simplest modules
./run.sh store            # similar
./run.sh mac_tmem_cell    # tiny leaf: 1 FMA + per-cell TMEM micro-storage
./run.sh compute_array    # the big one: 1024 mac_tmem_cell leaves + K-loop + drain
./run.sh load             # ~15-30 min (large FIFO arrays)
./run.sh cmdproc          # ~15-30 min (large imem)
./run.sh smem             # needs SRAM macro; ~10-20 min
```

Phase 7h replaced the old `mma` + `tmem` pair with `mac_tmem_cell` (the
single-cell leaf) wrapped 1024× by `compute_array`. The old monolithic
modules were dropped because their wide-port TMEM↔MMA interface
(32k-bit RMW) did not synthesize; the new architecture pushes
storage and the FMA into a per-(i, j) cell so each leaf is small and
the wide interface dissolves into local nets.

Or via Make:

```bash
make fp32_fma        # alias for ./run.sh fp32_fma
make all             # run every submodule in sequence
```

## Layout

```
submodules/
├── README.md       <- you are here
├── Makefile        <- per-module + all targets
├── run.sh          <- shared runner
└── <module>/
    ├── config.yaml <- OpenLane config; differs only on DESIGN_NAME + macros
    └── runs/       <- per-attempt outputs (gitignored)
```
