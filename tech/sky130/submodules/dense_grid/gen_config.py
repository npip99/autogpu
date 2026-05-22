#!/usr/bin/env python3
"""dense_grid: 32x32 mac_tmem_cell, tight pitch, with std cell legalization room."""
import re
from pathlib import Path
import yaml

SUB = Path(__file__).resolve().parent.parent

N = 32
PITCH_X = 450
PITCH_Y = 450
# Margin must accommodate CTS buffers + filler/tap cells around the macro grid.
# 90% macro density inside, but the outer margin gives std cells a place to live.
MARGIN = 500

origin_x = MARGIN
origin_y = MARGIN
def snap(v): return ((int(v) + 49) // 50) * 50
die_w = snap(origin_x + N * PITCH_X + MARGIN)
die_h = snap(origin_y + N * PITCH_Y + MARGIN)

cell_runs = sorted((SUB / "mac_tmem_cell" / "runs").glob("RUN_*"))
cell_run_name = cell_runs[-1].name
prefix = f"dir::../mac_tmem_cell/runs/{cell_run_name}/final"

instances = {}
for r in range(N):
    for c in range(N):
        name = f"gen_row[{r}].gen_col[{c}].u_cell"
        x = origin_x + c * PITCH_X
        y = origin_y + r * PITCH_Y
        instances[name] = {"location": [float(x), float(y)], "orientation": "N"}

config = {
    "meta": {"version": 2},
    "DESIGN_NAME": "dense_grid",
    "VERILOG_FILES": ["dir::../../../../build/sv2v/dense_grid.v"],
    "CLOCK_PORT": "clk",
    "CLOCK_PERIOD": 100,
    "RUN_LINTER": False,
    "SYNTH_STRATEGY": "AREA 3",
    "SYNTH_HIERARCHY_MODE": "keep",
    "PL_TIME_DRIVEN": False,
    "FP_IO_HLAYER": "met3",
    "FP_IO_VLAYER": "met2",
    "FP_PIN_ORDER_CFG": "dir::dense_grid.pin_order.cfg",
    "FP_SIZING": "absolute",
    "DIE_AREA": [0, 0, die_w, die_h],
    # PDN: pitch=50 matches macro internal PDN. HALO=0 allows straps at macro
    # boundaries (don't push them away from edges).
    "FP_PDN_VPITCH": 50,
    "FP_PDN_HPITCH": 50,
    "FP_PDN_VOFFSET": 0,
    "FP_PDN_HOFFSET": 0,
    "FP_PDN_HORIZONTAL_HALO": 0,
    "FP_PDN_VERTICAL_HALO": 0,
    # Skip PSM check — macros provide internal PDN, parent connects at boundaries
    "RT_MAX_LAYER": "met5",
    "MACROS": {
        "mac_tmem_cell": {
            "gds": [f"{prefix}/gds/mac_tmem_cell.gds"],
            "lef": [f"{prefix}/lef/mac_tmem_cell.lef"],
            "lib": {"*": [f"{prefix}/lib/nom_tt_025C_1v80/mac_tmem_cell__nom_tt_025C_1v80.lib"]},
            "instances": instances,
        }
    },
}

with open(SUB / "dense_grid" / "config.yaml", "w") as f:
    yaml.safe_dump(config, f, default_flow_style=None, sort_keys=False)

print(f"dense_grid: {N}x{N} mac_tmem_cell at pitch ({PITCH_X},{PITCH_Y}), MARGIN={MARGIN}")
print(f"die: {die_w} x {die_h} = {die_w*die_h/1e6:.1f} mm²")
macro_area = N*N*426.55*437.27
print(f"macros: {N*N} × 186.4k = {macro_area/1e6:.1f} mm² ({macro_area/(die_w*die_h)*100:.1f}% of die)")
