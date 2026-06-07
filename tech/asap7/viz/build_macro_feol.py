"""Build per-macro FEOL meshes (the real transistors) by running feol.py on each macro's OWN
GDS — the same tool/flow that produces parent_feol_logic.glb. Output: out/<type>_feol.glb
(gitignored, regenerated). The viewer loads these under each macro so the macros have a real
device layer (like the parent) instead of the abstract gate boxes.

Usage: python build_macro_feol.py [type ...]   (default: all four macro types)
"""
import os, sys, re, subprocess
from config import macro_def
HERE = os.path.dirname(os.path.abspath(__file__))

def diearea(deff):
    for line in open(deff):
        m = re.match(r"\s*DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)", line)
        if m:
            return [int(t)/1000.0 for t in m.groups()]
    raise SystemExit(f"no DIEAREA in {deff}")

types = sys.argv[1:] or ["cmd_unit", "mac_tmem_cell", "skew_lane_a", "skew_lane_b"]
for t in types:
    deff = macro_def(t); gds = deff.replace("6_final.def", "6_final.gds")
    if not os.path.exists(gds): print(f"  SKIP {t}: no GDS at {gds}"); continue
    x0, y0, x1, y1 = diearea(deff)
    out = os.path.join(HERE, "out", f"{t}_feol.glb")
    env = {**os.environ, "VIZ_DEF": deff, "VIZ_GDS": gds, "VIZ_WINDOW": f"{x0},{y0},{x1},{y1}"}
    print(f"=== {t}  die={x0},{y0},{x1},{y1}µm -> {out}", flush=True)
    subprocess.run([sys.executable, os.path.join(HERE, "feol.py"), out], env=env, check=True)
print("done")
