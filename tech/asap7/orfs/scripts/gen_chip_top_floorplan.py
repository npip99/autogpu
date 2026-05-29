#!/usr/bin/env python3
"""Generate the asap7 chip_top floorplan + macro placement TCL + preview PNG.

chip_top contains:
  - 1 × compute_array (hardened LEF; tiny 4×4 variant used as a first
    pass — see DESIGN.md and tech/asap7/problems/A6_chip_top.md).
  - 1 × cmdproc, load, barrier, reset_seq each (hardened LEFs).
  - smem inlined → 32 × fakeram7_256x32 macros from the asap7 platform.
  - store inlined → all FF-based, no macros.

Layout (lower-left origin, µm) — mirrors sky130's chip_top_floorplan.yaml
adjacency map (compute_array center, smem-west / load-NW, store-south,
cmdproc/barrier/reset_seq in north strip), scaled to the tiny dies.

  ┌──────────────────────────────────────────────────────────────┐
  │  cmdproc          barrier        reset_seq                   │ north strip
  ├──────────────────────────────────────────────────────────────┤
  │ ┌──────┐        ┌────────────────────┐                       │
  │ │ load │        │                    │                       │
  │ └──────┘        │   compute_array    │                       │
  │ ┌──────┐        │      400×400       │                       │
  │ │ smem │        │                    │                       │
  │ │ (32 ×│        │                    │                       │
  │ │ fake-│        │                    │                       │
  │ │ ram) │        │                    │                       │
  │ └──────┘        └────────────────────┘                       │
  │                  ┌──────────────────┐                        │
  │                  │      store       │                        │ (inlined,
  │                  │     (FF logic)   │                        │  no macros)
  │                  └──────────────────┘                        │
  └──────────────────────────────────────────────────────────────┘

Outputs into tech/asap7/orfs/:
  - chip_top.macro_placement.tcl   (place_macro lines for the 21 hard
                                    macros — compute_array, 4 submodule
                                    blocks, 16 fakeram banks)
  - chip_top.floorplan_preview.png (visual sanity check)

The DIE_AREA values used here should be pasted into chip_top.config.mk.
"""
import argparse
import os
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[4]
RESULTS = REPO / "build/orfs/results/asap7"
OUT_DIR = REPO / "tech/asap7/orfs"

# Channel widths between major blocks (µm).
CHANNEL_INNER = 40.0      # mac/smem/store/load gap to compute_array
CHANNEL_NORTH = 50.0      # gap between center cluster and north strip
CHANNEL_DIE   = 50.0      # die-edge margin

# Smem layout: 16 fakerams in an 8-col × 2-row grid (B1 region-partition
# dropped NUM_BANKS 32→16). Pitch matches the standalone smem.config.mk
# (40 µm horizontal channels, 30 µm vertical inter-bank channels).
# First-pass chip_top used tighter 28/60 µm pitch and DRT got stuck on
# ~7000 bank_rdata congestion violations in the middle horizontal channel
# — confirming what smem.config.mk's comment already documents about
# bank-output buses needing vertical escape room. Don't deviate.
SMEM_BANKS_X        = 8
SMEM_BANKS_Y        = 2
SMEM_FAKERAM_W      = 8.36
SMEM_FAKERAM_H      = 42.0
SMEM_BANK_PITCH_X   = 48.36   # 8.36 macro + 40 µm horizontal channel
SMEM_BANK_PITCH_Y   = 72.0    # 42 macro + 30 µm vertical channel


def lef_size(name: str) -> tuple[float, float]:
    """Return (W, H) in µm from the macro's hardened LEF SIZE line."""
    lef = RESULTS / name / "base" / f"{name}.lef"
    if not lef.exists():
        sys.exit(f"ERROR: {lef} missing — harden {name} first")
    m = re.search(r"\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", lef.read_text())
    if not m:
        sys.exit(f"ERROR: no SIZE line in {lef}")
    return float(m.group(1)), float(m.group(2))


def asap7_fakeram_size() -> tuple[float, float]:
    """ORFS-shipped fakeram7_256x32 dimensions (from asap7 platform LEF)."""
    return SMEM_FAKERAM_W, SMEM_FAKERAM_H


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--compute-array-name",
        default="compute_array_tiny_bcast0",
        help="Build dir name of the hardened compute_array variant to use as the LEF source.",
    )
    args = parser.parse_args()

    ca_w, ca_h = lef_size(args.compute_array_name)
    cp_w, cp_h = lef_size("cmdproc")
    ld_w, ld_h = lef_size("load")
    br_w, br_h = lef_size("barrier")
    rs_w, rs_h = lef_size("reset_seq")
    fr_w, fr_h = asap7_fakeram_size()

    print("Hardened macro sizes (µm):")
    print(f"  compute_array ({args.compute_array_name}): {ca_w} × {ca_h}")
    print(f"  cmdproc       : {cp_w} × {cp_h}")
    print(f"  load          : {ld_w} × {ld_h}")
    print(f"  barrier       : {br_w} × {br_h}")
    print(f"  reset_seq     : {rs_w} × {rs_h}")
    print(f"  fakeram7_256x32 (smem): {fr_w} × {fr_h} (×{SMEM_BANKS_X * SMEM_BANKS_Y})")

    # West column: smem (8x4 fakeram grid) + load (above smem)
    smem_block_w = (SMEM_BANKS_X - 1) * SMEM_BANK_PITCH_X + fr_w
    smem_block_h = (SMEM_BANKS_Y - 1) * SMEM_BANK_PITCH_Y + fr_h

    # Choose the wider of (smem, load) as west-column width.
    west_col_w = max(smem_block_w, ld_w)

    # X coordinates (lower-left origin).
    margin = CHANNEL_DIE
    smem_x0 = margin
    load_x  = margin
    ca_x    = margin + west_col_w + CHANNEL_INNER

    # Y stack on west column: smem at bottom, load above with CHANNEL_INNER gap.
    smem_y0 = margin
    load_y  = smem_y0 + smem_block_h + CHANNEL_INNER

    # Store (inlined, no macros) gets a placeholder rectangle below compute_array.
    store_w = max(180.0, ca_w * 0.6)
    store_h = 80.0
    store_x = ca_x + (ca_w - store_w) / 2
    store_y = margin

    # Compute_array sits above store on east side.
    ca_y = store_y + store_h + CHANNEL_INNER

    # North strip — cmdproc/barrier/reset_seq stacked left-to-right above
    # the load/compute_array region. y-position: above the tallest of
    # (load+smem column, compute_array).
    north_band_y = max(load_y + ld_h, ca_y + ca_h) + CHANNEL_NORTH
    cp_x = margin
    br_x = cp_x + cp_w + 40.0
    rs_x = br_x + br_w + 40.0

    # Die size — fit everything plus the east + north margins.
    die_w = max(rs_x + rs_w, ca_x + ca_w, store_x + store_w) + margin
    die_h = north_band_y + max(cp_h, br_h, rs_h) + margin
    # Round up to clean values for the DIE_AREA pin.
    die_w = ((int(die_w) // 50) + 1) * 50
    die_h = ((int(die_h) // 50) + 1) * 50
    print(f"\nDIE: {die_w} × {die_h} µm")

    # ---- Emit macro_placement.tcl ----------------------------------------
    tcl = ["# Auto-generated by tech/asap7/orfs/scripts/gen_chip_top_floorplan.py"]
    tcl.append(f"# compute_array variant: {args.compute_array_name}")
    tcl.append("")
    tcl.append(f"place_macro -macro_name u_compute_array      -location {{{ca_x:.2f} {ca_y:.2f}}}      -orientation R0")
    tcl.append(f"place_macro -macro_name u_cmdproc            -location {{{cp_x:.2f} {north_band_y:.2f}}} -orientation R0")
    tcl.append(f"place_macro -macro_name u_load               -location {{{load_x:.2f} {load_y:.2f}}}   -orientation R0")
    tcl.append(f"place_macro -macro_name u_barrier            -location {{{br_x:.2f} {north_band_y:.2f}}} -orientation R0")
    tcl.append(f"place_macro -macro_name u_reset_seq          -location {{{rs_x:.2f} {north_band_y:.2f}}} -orientation R0")
    tcl.append("")
    tcl.append("# smem fakeram7 banks — 16 instances in an 8-col × 2-row grid.")
    tcl.append("# Instance name format inside chip_top netlist:")
    tcl.append("#   u_smem.gen_banks[<n>].u_bank.u_sram.u_macro")
    tcl.append("# (smem.sv uses `for ... begin : gen_banks`; sram_1rw.sv")
    tcl.append("#  under USE_ASAP7_FAKERAM wraps fakeram7_256x32 as u_macro.)")
    for ridx in range(SMEM_BANKS_Y):
        for cidx in range(SMEM_BANKS_X):
            bank_idx = ridx * SMEM_BANKS_X + cidx
            bx = smem_x0 + cidx * SMEM_BANK_PITCH_X
            by = smem_y0 + ridx * SMEM_BANK_PITCH_Y
            tcl.append(
                f"place_macro -macro_name {{u_smem.gen_banks\\[{bank_idx}\\].u_bank.u_sram.u_macro}}"
                f" -location {{{bx:.2f} {by:.2f}}} -orientation R0"
            )

    tcl_path = OUT_DIR / "chip_top.macro_placement.tcl"
    tcl_path.write_text("\n".join(tcl) + "\n")
    print(f"\nWrote {tcl_path} ({len([l for l in tcl if l.startswith('place_macro')])} macros)")

    # ---- Render preview PNG ----------------------------------------------
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.set_xlim(-die_w * 0.05, die_w * 1.05)
    ax.set_ylim(-die_h * 0.05, die_h * 1.05)
    ax.set_aspect("equal")
    ax.set_title(f"chip_top asap7 floorplan — {die_w} × {die_h} µm")
    ax.set_xlabel("x (µm)")
    ax.set_ylabel("y (µm)")
    ax.add_patch(patches.Rectangle((0, 0), die_w, die_h, linewidth=2,
                                    edgecolor="black", facecolor="#fafafa"))

    def draw(x, y, w, h, color, label, ec="#333"):
        ax.add_patch(patches.Rectangle((x, y), w, h, linewidth=0.6,
                                        edgecolor=ec, facecolor=color, alpha=0.85))
        if label:
            ax.text(x + w / 2, y + h / 2, label, ha="center", va="center",
                    fontsize=8, color="black")

    draw(ca_x, ca_y, ca_w, ca_h, "#c7ecee", f"compute_array\n({args.compute_array_name})")
    draw(cp_x, north_band_y, cp_w, cp_h, "#ffd29b", "cmdproc")
    draw(br_x, north_band_y, br_w, br_h, "#a8d8ea", "barrier")
    draw(rs_x, north_band_y, rs_w, rs_h, "#aa96da", "reset_seq")
    draw(load_x, load_y, ld_w, ld_h, "#ffaaa5", "load")
    # smem block outline.
    draw(smem_x0 - 2, smem_y0 - 2, smem_block_w + 4, smem_block_h + 4,
         "#ffffff", "", ec="#888")
    for ridx in range(SMEM_BANKS_Y):
        for cidx in range(SMEM_BANKS_X):
            bx = smem_x0 + cidx * SMEM_BANK_PITCH_X
            by = smem_y0 + ridx * SMEM_BANK_PITCH_Y
            draw(bx, by, fr_w, fr_h, "#7fcdcd", "")
    ax.text(smem_x0 + smem_block_w / 2, smem_y0 + smem_block_h + 8,
            "smem (16 × fakeram7_256x32)", ha="center", va="bottom", fontsize=7)
    # store placeholder (no macros).
    draw(store_x, store_y, store_w, store_h, "#ffe082", "store\n(inlined, FF logic)")

    legend_patches = [
        patches.Patch(facecolor="#c7ecee", label="compute_array (hardened LEF)"),
        patches.Patch(facecolor="#ffd29b", label="cmdproc"),
        patches.Patch(facecolor="#ffaaa5", label="load"),
        patches.Patch(facecolor="#a8d8ea", label="barrier"),
        patches.Patch(facecolor="#aa96da", label="reset_seq"),
        patches.Patch(facecolor="#7fcdcd", label="fakeram7 (smem bank)"),
        patches.Patch(facecolor="#ffe082", label="store (inlined)"),
    ]
    ax.legend(handles=legend_patches, loc="upper right", fontsize=7)

    png_path = OUT_DIR / "chip_top.floorplan_preview.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    print(f"Wrote {png_path}")

    print(f"\nFor chip_top.config.mk:")
    print(f"  DIE_AREA  = 0 0 {die_w} {die_h}")
    print(f"  CORE_AREA = {CHANNEL_DIE/2} {CHANNEL_DIE/2} "
          f"{die_w - CHANNEL_DIE/2} {die_h - CHANNEL_DIE/2}")


if __name__ == "__main__":
    main()
