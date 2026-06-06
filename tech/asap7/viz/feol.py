"""
Render logic-cell FEOL (the real transistors of the gates) from a design's GDS — fill cells
excluded. Flattens the GDS, keeps only FEOL-layer polygons whose centroid falls inside a LOGIC
cell footprint (from logic_cells), unions per layer, and extrudes at each layer's process z.

Usage:
  python feol.py [out.glb]
  VIZ_DEF=<macro.def> VIZ_GDS=<macro.gds> VIZ_WINDOW=0,0,50,50 python feol.py out/cmd_feol.glb
"""
import gdstk, json, os, sys, time, numpy as np, trimesh
from collections import defaultdict
from shapely.geometry import Polygon, box as sbox, Point
from shapely.ops import unary_union
from shapely.strtree import STRtree
from def_wires import logic_cells
from config import DEF_FILE, GDS_FILE, WINDOW

HERE=os.path.dirname(os.path.abspath(__file__))
DEF=os.environ.get("VIZ_DEF", DEF_FILE); GDS=os.environ.get("VIZ_GDS", GDS_FILE)
X0,Y0,X1,Y1=WINDOW
OUT=sys.argv[1] if len(sys.argv)>1 else os.path.join(HERE,"out","feol_logic.glb")
layers=json.load(open(f"{HERE}/asap7/layers.json")); user=json.load(open(f"{HERE}/asap7/user.json"))
csz=json.load(open(f"{HERE}/cell_sizes.json"))
FEOL=["well","fin","active","nselect","pselect","gcut","gate","lig","lisd","v0"]
gname={layers[n]["layer"]:n for n in layers}
z=0.0; zinfo={}
for n in layers: h=user["layers"][n].get("height",0.1); zinfo[n]=(z,h); z+=h

MACROS={"mac_tmem_cell","skew_lane_a","skew_lane_b","cmd_unit"}   # rendered as their own tiles -> prune
t=time.time()
boxes=[sbox(a,b,c,d) for (m,a,b,c,d) in logic_cells(DEF,X0,Y0,X1,Y1,csz)]
tree=STRtree(boxes)
print(f"{len(boxes)} logic footprints; flattening GDS (macros pruned, window only)...", flush=True)
lib=gdstk.read_gds(GDS); src=lib.top_level()[0]
top=gdstk.Library().new_cell("feol")
for r in src.references:
    if r.cell.name in MACROS: continue
    ox,oy=r.origin
    if X0-6<=ox<=X1+6 and Y0-6<=oy<=Y1+6:
        top.add(gdstk.Reference(r.cell,origin=r.origin,rotation=r.rotation,x_reflection=r.x_reflection))
for q in src.polygons:
    (a,b),(c,d)=q.bounding_box()
    if c>=X0 and a<=X1 and d>=Y0 and b<=Y1: top.add(q)
top.flatten()
bylayer=defaultdict(list); scanned=0
for q in top.polygons:
    nm=gname.get(q.layer)
    if nm not in FEOL or q.datatype!=0: continue
    c=q.points.mean(axis=0); p=Point(c[0],c[1])
    if any(boxes[i].covers(p) for i in tree.query(p)): bylayer[nm].append(Polygon(q.points))
print(f"logic FEOL polys: "+", ".join(f"{k}={len(v)}" for k,v in bylayer.items())+f" ({time.time()-t:.0f}s)", flush=True)

parts=[]; fails=0
for nm,polys in bylayer.items():
    z0,h=zinfo[nm]; col=np.array(user["layers"][nm]["color"],dtype=np.uint8)   # already RGBA
    u=unary_union(polys); n=0
    for g in ([u] if u.geom_type=="Polygon" else list(getattr(u,"geoms",[]))):
        if g.is_empty or g.area<1e-7: continue
        try:
            m=trimesh.creation.extrude_polygon(g,height=h,engine="earcut"); m.apply_translation([0,0,z0])
            m.visual.face_colors=col; parts.append(m); n+=1
        except Exception as e:
            fails+=1
            if fails<=3: print(f"  extrude FAIL {nm}: {e!r}", flush=True)
    print(f"  {nm}: {n} prisms ({time.time()-t:.0f}s)", flush=True)
if fails: print(f"WARNING: {fails} extrude failures", flush=True)
chip=trimesh.util.concatenate(parts); os.makedirs(os.path.dirname(OUT), exist_ok=True); chip.export(OUT)
print(f"{OUT}: {len(chip.faces)} faces, {os.path.getsize(OUT)//1024}KB, {time.time()-t:.0f}s")
