"""
Assemble out/wires.glb (the full to-scale model) from its parts, reproducibly:

  1. routing + vias + RECT + macros (def_wires_3d.py, VIZ_CELLS=0).
     Each macro renders as its full-res routing+device mesh (out/macros/<type>.glb) if present,
     else a footprint block. That per-macro mesh already carries the macro's own device detail,
     so NO separate per-macro FEOL is merged (doing so double-stacks it).
  2. parent-channel logic FEOL (out/parent_feol_logic.glb) — the only standalone FEOL, for the
     std-cell channels between macros; already in window coords.

Prereqs (regenerate if missing; all gitignored):
  python build_macros.py cmd_unit skew_lane_a skew_lane_b        # -> out/macros/<type>.glb
  python feol.py out/parent_feol_logic.glb                       # parent channels (default DEF/GDS/WINDOW)

Usage: python build_wires.py [out.glb]
"""
import os, sys, subprocess, time, trimesh
HERE=os.path.dirname(os.path.abspath(__file__))
OUT=sys.argv[1] if len(sys.argv)>1 else os.path.join(HERE,"out","wires.glb")
ROUT=os.path.join(HERE,"out","_routing.glb")
t=time.time()

# 1. routing + macros (macro meshes already include their device detail)
env=dict(os.environ, VIZ_CELLS="0", VIZ_SLAB="1")
subprocess.run([sys.executable,"def_wires_3d.py","1.0",ROUT,"1","1"],cwd=HERE,env=env,check=True)
parts=[trimesh.load(ROUT,force='mesh')]
print(f"routing+macros: {len(parts[0].faces)} faces ({time.time()-t:.0f}s)",flush=True)

# 2. parent channel FEOL, already in window coords
parts.append(trimesh.load(os.path.join(HERE,"out","parent_feol_logic.glb"),force='mesh'))

merged=trimesh.util.concatenate(parts); merged.export(OUT)
print(f"{OUT}: {len(merged.faces)} faces, {os.path.getsize(OUT)//1024}KB, {time.time()-t:.0f}s")
