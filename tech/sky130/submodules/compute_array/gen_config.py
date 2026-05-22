#!/usr/bin/env python3
"""Generate compute_array/config.yaml.

Reads the latest hardened runs of mac_tmem_cell, skew_lane_a, skew_lane_b,
cmd_unit; computes a tight floorplan with the topology:

    +---------+--------------------------------+
    | cmd_unit|       b-skew row (32, N)       |
    +---------+--------------------------------+
    | a-skew  |                                |
    | col     |     32x32 mac_tmem_cell        |
    | (32, N) |     grid (N orientation)       |
    +---------+--------------------------------+

Every macro is placed at orientation N — skew_lane is hardened twice with
two pin orientations (skew_lane_a: W/E, skew_lane_b: S/N) so we never
rotate. Rotating a macro flips its PG-pin axes onto the same layer as the
parent's straps, defeating OpenROAD's via-based PDN connect path
(PSM-0069). Native N orient everywhere preserves orthogonal-layer crossings.

Pin order discipline:
  - mac_tmem_cell : W in, E out (a-bus). N out, S in (b-bus + drain).
  - skew_lane_a   : W in, E out  (drives a from west into cell grid)
  - skew_lane_b   : S in, N out  (drives b from south into cell grid)
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
    f_cell    = latest_final("mac_tmem_cell")
    f_skew_a  = latest_final("skew_lane_a")
    f_skew_b  = latest_final("skew_lane_b")
    f_cmd     = latest_final("cmd_unit")

    cw, ch       = die_area(f_cell,   "mac_tmem_cell")
    saw, sah     = die_area(f_skew_a, "skew_lane_a")
    sbw, sbh     = die_area(f_skew_b, "skew_lane_b")
    uw, uh       = die_area(f_cmd,    "cmd_unit")

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

    # a-skew is placed natively (N orient) — its width is its parent-x span.
    # b-skew is placed natively (N orient) — its width is its parent-x span,
    # height is its parent-y span. The b-skew row needs vertical room equal
    # to sbh (not sbw); column-pitch must fit sbw across cw_p (which it does
    # since cw_p ≈ 500 > sbw).
    #
    # `+ CMD_HALO` widens the routing channels between cmd_unit and the
    # neighboring skew macros. Without it, cmd_unit (564×575) packs flush
    # against a-skew[0] / b-skew[0] with 25/36 µm gaps.
    #
    # `CMD_OFFSET` pushes cmd_unit off the die SW corner so its W and S
    # faces aren't flush with the die edge — gives space for chip-level
    # signals on cmd_unit's W (rd_a_*) and S (drain_*, clk, reset) faces
    # to escape to compute_array's W and S die-edge pin tracks without
    # crowding the macro perimeter.
    CMD_HALO     = 500
    CMD_OFFSET   = 800
    # NORTH_MARGIN: extra space between MAC array north edge and die N edge.
    # Gives room for chip-IO pin attach + fly-over routing toward N die edge.
    NORTH_MARGIN = 1000
    # EAST_MARGIN: same on east side.
    EAST_MARGIN  = 1000
    origin_x = snap(max(CMD_OFFSET + uw + CMD_HALO, saw))
    origin_y = snap(max(CMD_OFFSET + uh + CMD_HALO, sbh))

    die_w = origin_x + MMA * cw_p + EAST_MARGIN
    die_h = origin_y + MMA * ch_p + NORTH_MARGIN

    macros = {
        "mac_tmem_cell": macro_block(f_cell,   "mac_tmem_cell"),
        "skew_lane_a":   macro_block(f_skew_a, "skew_lane_a"),
        "skew_lane_b":   macro_block(f_skew_b, "skew_lane_b"),
        "cmd_unit":      macro_block(f_cmd,    "cmd_unit"),
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

    # ---- a-skew column (N orientation), one per row ----
    # Sit just left of cells (W edge of grid). W/E pins -> a-bus flows east
    # into cells. Snap origin to PDN grid.
    a_x = origin_x - snap(saw)
    skews_a = macros["skew_lane_a"]["instances"]
    for r in range(MMA):
        skews_a[f"gen_a_skew[{r}].u_a"] = {
            "location": [round(a_x, 3), round(origin_y + r * ch_p, 3)],
            "orientation": "N",
        }

    # ---- b-skew row (N orientation), one per col ----
    # Sit just below cells (S edge of grid). S/N pins -> b-bus flows north
    # into cells. No rotation: every macro stays at N orient so PDN
    # connects via orthogonal-layer vias (same recipe as a-skew + cells).
    b_y = origin_y - snap(sbh)
    skews_b = macros["skew_lane_b"]["instances"]
    for c in range(MMA):
        skews_b[f"gen_b_skew[{c}].u_b"] = {
            "location": [round(origin_x + c * cw_p, 3), round(b_y, 3)],
            "orientation": "N",
        }

    # ---- cmd_unit at SW corner, pushed in by CMD_OFFSET ----
    # Offset from (0,0) so the W and S faces aren't flush with the die edge,
    # giving room for chip-level signals on those faces to escape.
    macros["cmd_unit"]["instances"]["u_cmd"] = {
        "location": [float(CMD_OFFSET), float(CMD_OFFSET)],
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
        "RT_MAX_LAYER": "met5",
        "GRT_OVERFLOW_ITERS": 200,
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
    print(f"  skew_a  {saw}x{sah}")
    print(f"  skew_b  {sbw}x{sbh}")
    print(f"  cmd     {uw}x{uh}")
    print(f"  die     {die_w:.1f} x {die_h:.1f}")
    print(f"  origin  ({origin_x:.1f}, {origin_y:.1f})")


if __name__ == "__main__":
    main()
