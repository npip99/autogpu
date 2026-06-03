"""
Regenerate full-res macro routing meshes from each macro's own 6_final.def, using the
verified def_wires pipeline (every wire + via + enclosure, to scale, NO decimation).
Output goes to MACRO_MESH_DIR (out/macros/, gitignored). The parent build (def_wires_3d.py)
places these at each macro's location; types without a mesh fall back to a footprint box.

Usage:  python build_macros.py [type ...]      (default: cmd_unit)
        python build_macros.py cmd_unit skew_lane_a skew_lane_b mac_tmem_cell
"""
import os, sys, re, subprocess
from config import macro_def, MACRO_MESH_DIR
HERE=os.path.dirname(os.path.abspath(__file__))

def diearea_um(deff):
    for line in open(deff):
        m=re.match(r"\s*DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)", line)
        if m:
            v=[int(t)/1000.0 for t in m.groups()]; return v[0],v[1],v[2],v[3]
    raise SystemExit(f"no DIEAREA in {deff}")

types = sys.argv[1:] or ["cmd_unit"]
os.makedirs(MACRO_MESH_DIR, exist_ok=True)
for tn in types:
    deff=macro_def(tn)
    if not os.path.exists(deff): print(f"  SKIP {tn}: no DEF at {deff}"); continue
    x0,y0,x1,y1=diearea_um(deff)
    out=os.path.join(MACRO_MESH_DIR, f"{tn}.glb")
    env={**os.environ, "VIZ_DEF":deff, "VIZ_WINDOW":f"{x0},{y0},{x1},{y1}", "VIZ_SLAB":"0"}
    print(f"=== {tn}  DEF={deff}  die={x0},{y0},{x1},{y1}µm -> {out}", flush=True)
    # def_wires_3d args: <zexag> <out> <macros 0|1> <pdn 0|1>;  macros=0 (leaf), pdn=1
    subprocess.run([sys.executable, os.path.join(HERE,"def_wires_3d.py"), "1", out, "0", "1"],
                   env=env, check=True)
print("done")
