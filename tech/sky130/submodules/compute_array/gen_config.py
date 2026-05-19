#!/usr/bin/env python3
"""Generate compute_array/config.yaml.

Reads the latest hardened runs of mac_tmem_cell, skew_lane, cmd_unit;
computes a tight floorplan with the topology:

    +---------+--------------------------------+
    | cmd_unit|       b-skew row (32 R270)     |
    +---------+--------------------------------+
    | a-skew  |                                |
    | col     |     32x32 mac_tmem_cell        |
    | (32 R0) |     grid (north orientation)   |
    +---------+--------------------------------+

Pin order discipline:
  - mac_tmem_cell: W in, E out (a-bus). N out, S in (b-bus + drain).
  - skew_lane     : W in, E out  (R0  -> drives a from west)
                  : N in, S out  (R270 -> drives b from north)
  - cmd_unit      : W=rd_a, E=rd_b+push_b, N=ctrl, S=push_a+drain
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUB = ROOT.parent          # .../submodules
CFG = ROOT / "config.yaml"


def latest_final(macro: str) -> Path:
    runs = sorted((SUB / macro / "runs").glob("RUN_*"))
    for r in reversed(runs):
        if (r / "final").is_dir() and (r / "final" / "def" / f"{macro}.def").exists():
            return r / "final"
    raise SystemExit(f"no final/ for {macro}")


def die_area(final: Path, name: str):
    txt = (final / "def" / f"{name}.def").read_text()
    m = re.search(r"DIEAREA\s*\(\s*0\s+0\s*\)\s*\(\s*(\d+)\s+(\d+)\s*\)", txt)
    w_nm, h_nm = int(m.group(1)), int(m.group(2))
    return w_nm / 1000.0, h_nm / 1000.0   # nm -> um


def macro_block(final: Path, name: str) -> dict:
    return {
        "gds": [f"dir::../{name}/runs/{final.parent.name}/final/gds/{name}.gds"],
        "lef": [f"dir::../{name}/runs/{final.parent.name}/final/lef/{name}.lef"],
        "lib": {
            "*": [
                f"dir::../{name}/runs/{final.parent.name}/final/lib/"
                f"nom_tt_025C_1v80/{name}__nom_tt_025C_1v80.lib"
            ]
        },
        "instances": {},
    }


def main():
    f_cell = latest_final("mac_tmem_cell")
    f_skew = latest_final("skew_lane")
    f_cmd  = latest_final("cmd_unit")

    cw, ch = die_area(f_cell, "mac_tmem_cell")
    sw, sh = die_area(f_skew, "skew_lane")
    uw, uh = die_area(f_cmd,  "cmd_unit")

    MMA = 32
    # Lock everything to the mac_tmem_cell internal PDN pitch so chip-level
    # PDN rails fall directly on top of macro-internal PG rails — no
    # per-macro stitching work. Requires:
    #   - cell pitch a multiple of PDN_PITCH
    #   - all macro origins at multiples of PDN_PITCH
    PDN_PITCH = 50.0

    def snap(v):
        import math
        return math.ceil(v / PDN_PITCH) * PDN_PITCH

    cw_p = snap(cw + 60.0)   # ~73 um gap for glue std cells
    ch_p = snap(ch + 60.0)

    skew_a_w = sw
    skew_b_h = sw

    origin_x = snap(max(uw, skew_a_w))
    origin_y = snap(max(uh, skew_b_h))

    die_w = origin_x + MMA * cw_p + 50
    die_h = origin_y + MMA * ch_p + 50

    macros = {
        "mac_tmem_cell": macro_block(f_cell, "mac_tmem_cell"),
        "skew_lane":     macro_block(f_skew, "skew_lane"),
        "cmd_unit":      macro_block(f_cmd,  "cmd_unit"),
    }

    # ---- cell grid (N orientation) ----
    cells = macros["mac_tmem_cell"]["instances"]
    for r in range(MMA):
        for c in range(MMA):
            cells[f"gen_row[{r}].gen_col[{c}].u_cell"] = {
                "location": [
                    round(origin_x + c * cw_p, 3),
                    round(origin_y + r * ch_p, 3),
                ],
                "orientation": "N",
            }

    # ---- a-skew column (R0 = N orientation), one per row ----
    # Sit just left of cells (W edge of grid). Snap origin to PDN grid.
    a_x = origin_x - snap(skew_a_w)
    skews = macros["skew_lane"]["instances"]
    for r in range(MMA):
        skews[f"gen_a_skew[{r}].u_a"] = {
            "location": [round(a_x, 3), round(origin_y + r * ch_p, 3)],
            "orientation": "N",
        }

    # ---- b-skew row (R270 orientation), one per col ----
    # Sit just below cells (S edge from a placement-coord perspective).
    # R270: original (W,E,N,S) -> (N,S,E,W) after CCW270 rotation.
    b_y = origin_y - snap(skew_b_h)
    for c in range(MMA):
        skews[f"gen_b_skew[{c}].u_b"] = {
            "location": [round(origin_x + c * cw_p, 3), round(b_y, 3)],
            "orientation": "R270",
        }

    # ---- cmd_unit at NW corner ----
    macros["cmd_unit"]["instances"]["u_cmd"] = {
        "location": [0.0, 0.0],
        "orientation": "N",
    }

    cfg = {
        "meta": {"version": 2},
        "DESIGN_NAME": "compute_array",
        "VERILOG_FILES": ["dir::../../../../build/sv2v/chip_top.v"],
        "CLOCK_PORT": "clk",
        "CLOCK_PERIOD": 40,
        "RUN_LINTER": False,
        "SYNTH_STRATEGY": "AREA 3",
        "SYNTH_SHARE_RESOURCES": False,
        "SYNTH_ABC_BUFFERING": False,
        "SYNTH_HIERARCHY_MODE": "keep",
        "PL_TIME_DRIVEN": False,
        "FP_PIN_ORDER_CFG": "dir::compute_array.pin_order.cfg",
        "ERRORS_ON_UNMATCHED_IO": "none",
        "FP_IO_HLAYER": "met3",
        "FP_IO_VLAYER": "met4",
        "RT_MAX_LAYER": "met4",
        "FP_SIZING": "absolute",
        "DIE_AREA":  [0, 0, round(die_w, 1), round(die_h, 1)],
        "CORE_AREA": [50, 50, round(die_w - 50, 1), round(die_h - 50, 1)],
        "PL_TARGET_DENSITY_PCT": 60,
        # Match mac_tmem_cell internal PDN pitch so chip rails overlay
        # macro PG rails directly — no per-macro stitching for the 1024
        # cell instances. Offset 0 puts the first rail at the die origin,
        # which matches our snap(50)-aligned macro origins.
        "FP_PDN_VPITCH": 50,
        "FP_PDN_HPITCH": 50,
        "FP_PDN_VOFFSET": 0,
        "FP_PDN_HOFFSET": 0,
        "MACROS": macros,
    }

    # Use json for dict-friendly emit + YAML-compatible block style.
    import yaml
    CFG.write_text(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False))
    print(f"wrote {CFG}")
    print(f"  cell    {cw}x{ch}")
    print(f"  skew    {sw}x{sh}")
    print(f"  cmd     {uw}x{uh}")
    print(f"  die     {die_w:.1f} x {die_h:.1f}")
    print(f"  origin  ({origin_x:.1f}, {origin_y:.1f})")


if __name__ == "__main__":
    main()
