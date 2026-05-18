# Vendored fpnew (CVFPU) + common_cells subset

This directory contains a minimal vendored subset of two upstream
projects, used to provide a synthesizable IEEE-754 fp32 fused
multiply-add (used by `common/fp32_fma.sv` and consumed by
`mac_tmem_cell/mac_tmem_cell.sv` — one FMA per (i, j) compute cell,
1024 cells per `compute_array`).

## Sources

| File                                  | Upstream path                                            |
|---------------------------------------|----------------------------------------------------------|
| `fpnew_pkg.sv`                        | `cvfpu/src/fpnew_pkg.sv`                                 |
| `fpnew_fma.sv`                        | `cvfpu/src/fpnew_fma.sv`                                 |
| `fpnew_classifier.sv`                 | `cvfpu/src/fpnew_classifier.sv`                          |
| `fpnew_rounding.sv`                   | `cvfpu/src/fpnew_rounding.sv`                            |
| `lzc.sv`                              | `common_cells/src/lzc.sv` (leading-zero counter, needed by `fpnew_fma`) |
| `common_cells/registers.svh`          | `common_cells/include/common_cells/registers.svh`        |

The `common_cells/` subdirectory mirrors the upstream include path so
`include "common_cells/registers.svh"` in `fpnew_fma.sv` resolves
when `+incdir+common/fpnew` is on the include path.

## Vendored commits

- pulp-platform/cvfpu          @ `106251e502747f17d931a20db0bdbab9e1a6c2ff`
- pulp-platform/common_cells   @ `63e1b679a70eca3a1d60d686bc1fa170ec08e1ab`

## License

Both upstreams are Solderpad Hardware License v0.51
(`SPDX-License-Identifier: SHL-0.51`), a permissive license similar to
Apache 2.0. All original copyright headers are preserved in the
vendored files.

## Modifications

None — files are copied verbatim from upstream.
