#!/usr/bin/env python3
"""
Generate a stub GDS for a module: an empty cell with just the macro
outline drawn on a single marker layer. Sufficient for chip_top PnR
and streamout — chip_top will reference it as the macro's GDS, and at
its own streamout the stub gets nested into the chip GDS as an empty
black box at the right footprint.

Usage:
    gen_stub_gds.py <module_name> <repo_root>

Reads die dimensions via the same 3-tier fallback as gen_stub_lef.py.
Writes <repo_root>/build/sv2v/gds-stub/<module>.gds.

Marker layer is (235, 4) — sky130's "areaid.standardc" layer (the same
layer used by hardened macros for their cell outline annotation).
"""

import sys
from pathlib import Path

import klayout.db as pya

# Reuse the size resolution logic from the LEF generator.
sys.path.insert(0, str(Path(__file__).parent))
from gen_stub_lef import get_die_dimensions  # noqa: E402


# DBU = 1 nm (sky130 default). All coordinates below are integers in DBU.
DBU_PER_UM = 1000

# Layers we draw the macro outline on. sky130 "areaid.standardc" (235, 4)
# is the canonical boundary marker but renders faintly in KLayout. We also
# stripe the outline on met1..met5 so each stub shows up as a labeled
# region in floorplan PNGs.
BOUNDARY_LAYERS = [
    (235, 4),   # areaid.standardc — boundary marker
    (68, 20),   # met1 — visible, will color the macro footprint
    (69, 20),   # met2
    (70, 20),   # met3
    (71, 20),   # met4
    (72, 20),   # met5
]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.exit(f"usage: {argv[0]} <module_name> <repo_root>")
    module = argv[1]
    repo = Path(argv[2]).resolve()
    cfg_yaml = repo / "tech/sky130/submodules" / module / "config.yaml"
    out_path = repo / "build/sv2v/gds-stub" / f"{module}.gds"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    die_w_um, die_h_um = get_die_dimensions(cfg_yaml, module, repo)
    die_w = int(round(die_w_um * DBU_PER_UM))
    die_h = int(round(die_h_um * DBU_PER_UM))

    layout = pya.Layout()
    layout.dbu = 1.0 / DBU_PER_UM  # microns per DBU
    cell = layout.create_cell(module)
    box = pya.Box(0, 0, die_w, die_h)
    for layer_num, datatype in BOUNDARY_LAYERS:
        layer_idx = layout.layer(layer_num, datatype)
        cell.shapes(layer_idx).insert(box)
    layout.write(str(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
