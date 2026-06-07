"""Option 2: CELL-LEVEL FEOL instancing. Instead of one giant baked FEOL mesh per macro, extract
each unique std-cell type's FEOL geometry ONCE (local coords, union+extrude per layer like feol.py)
and emit per-cell placement transforms from the macro's GDS. The viewer GPU-instances the ~dozens of
unique cell meshes -> tiny download vs the baked blob.

Usage: python build_cell_feol.py <macro_type>     -> out/cellfeol/<type>/{<master>.glb, instances.json}
"""
import gdstk, json, os, sys, math, time, numpy as np, trimesh
from shapely.geometry import Polygon
from shapely.ops import unary_union
from config import macro_def
HERE=os.path.dirname(os.path.abspath(__file__)); t0=time.time()
T=sys.argv[1] if len(sys.argv)>1 else "cmd_unit"
gds=macro_def(T).replace("6_final.def","6_final.gds")
OUTD=os.path.join(HERE,"out","cellfeol",T); os.makedirs(OUTD,exist_ok=True)
layers=json.load(open(f"{HERE}/asap7/layers.json")); user=json.load(open(f"{HERE}/asap7/user.json"))
FEOL=["well","fin","active","nselect","pselect","gcut","gate","lig","lisd","v0"]
gnum={layers[n]["layer"]:n for n in layers if n in FEOL}   # GDS layer# -> feol layer name
z=0.0; zinfo={}
for n in layers: h=user["layers"][n].get("height",0.1); zinfo[n]=(z,h); z+=h

def cell_feol_mesh(cell):
    bylayer={}
    for q in cell.polygons:
        nm=gnum.get(q.layer)
        if nm is None or q.datatype!=0: continue
        bylayer.setdefault(nm,[]).append(Polygon(q.points))
    parts=[]
    for nm,polys in bylayer.items():
        z0,h=zinfo[nm]; col=np.array(user["layers"][nm]["color"],dtype=np.uint8)
        u=unary_union(polys)
        for g in ([u] if u.geom_type=="Polygon" else list(getattr(u,"geoms",[]))):
            if g.is_empty or g.area<1e-7: continue
            try:
                m=trimesh.creation.extrude_polygon(g,height=h,engine="earcut"); m.apply_translation([0,0,z0])
                m.visual.face_colors=col; parts.append(m)
            except Exception: pass
    return trimesh.util.concatenate(parts) if parts else None

lib=gdstk.read_gds(gds); src=lib.top_level()[0]; cells={c.name:c for c in lib.cells}
inst={}
for r in src.references:
    inst.setdefault(r.cell.name,[]).append([round(r.origin[0],4),round(r.origin[1],4),
                                            round(math.degrees(r.rotation),3),1 if r.x_reflection else 0])
print(f"{T}: {len(inst)} unique cell types, {sum(len(v) for v in inst.values())} placements",flush=True)
meta={}; tot_faces=0; tot_bytes=0
for nm,places in sorted(inst.items(),key=lambda kv:-len(kv[1])):
    m=cell_feol_mesh(cells[nm])
    if m is None: meta[nm]={"glb":None,"n":len(places),"faces":0}; continue
    fp=os.path.join(OUTD,nm+".glb"); m.export(fp); tot_faces+=len(m.faces); tot_bytes+=os.path.getsize(fp)
    meta[nm]={"glb":nm+".glb","n":len(places),"faces":len(m.faces)}
json.dump({"type":T,"instances":inst,"meta":meta},open(os.path.join(OUTD,"instances.json"),"w"))
print(f"UNIQUE geometry: {tot_faces} faces, {tot_bytes//1024}KB across {len(meta)} masters ({time.time()-t0:.0f}s)")
print(f"vs baked: out/{T}_feol.glb")
