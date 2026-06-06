"""Hierarchical GDS -> instanced 3D, from the SINGLE parent GDS (no DEF). The top cell is
flattened to std/via cells + macro cells; we extract each unique cell's metal+vias ONCE as a
master mesh and emit per-cell placement transforms, plus the top-cell's own metal as 'parent'.
The viewer instances masters at the transforms. One GDS file in, a full instanced scene out.

Usage: python build_gds_instances.py [X0 Y0 X1 Y1]   (default = config WINDOW)
Out:   out/gds_inst/<cell>.glb (masters), out/gds_inst/parent.glb, out/gds_inst/instances.json
"""
import gdstk, json, os, sys, time, math, numpy as np, trimesh
from config import GDS_FILE, WINDOW
t=time.time(); HERE=os.path.dirname(os.path.abspath(__file__))
X0,Y0,X1,Y1 = ([float(v) for v in sys.argv[1:5]] if len(sys.argv)>4 else WINDOW)
OUTD=os.path.join(HERE,"out","gds_inst"); os.makedirs(OUTD,exist_ok=True)
METAL={19:"m1",20:"m2",30:"m3",40:"m4",50:"m5",60:"m6",70:"m7"}
VIA={21:("m1","m2"),25:("m2","m3"),35:("m3","m4"),45:("m4","m5"),55:("m5","m6"),65:("m6","m7")}
layers=json.load(open(f"{HERE}/asap7/layers.json")); user=json.load(open(f"{HERE}/asap7/user.json"))
z=0.0; zinfo={}
for n,c in layers.items():
    h=user["layers"][n].get("height",0.1); zinfo[n]=(z,h); z+=h
PAL={"m1":(255,59,48),"m2":(255,149,0),"m3":(255,214,10),"m4":(52,199,89),
     "m5":(50,173,230),"m6":(94,92,230),"m7":(255,45,146)}
VIA_W=0.016; VCOL=(150,150,164)
# unit cube + faces, for vectorized box generation (trimesh.creation.box per-poly is ~100x slower)
_UV=np.array([[-.5,-.5,-.5],[.5,-.5,-.5],[.5,.5,-.5],[-.5,.5,-.5],[-.5,-.5,.5],[.5,-.5,.5],[.5,.5,.5],[-.5,.5,.5]],dtype=np.float64)
_UF=np.array([[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]],dtype=np.int64)

def build_mesh(polys):
    cen=[]; ext=[]; col=[]
    for q in polys:
        if q.datatype!=0: continue
        (a,b),(c,d)=q.bounding_box()
        if q.layer in METAL:
            nm=METAL[q.layer]; z0,hh=zinfo[nm]
            if c-a<1e-4 or d-b<1e-4: continue
            cen.append(((a+c)/2,(b+d)/2,z0+hh/2)); ext.append((c-a,d-b,hh)); col.append(PAL[nm])
        elif q.layer in VIA:
            lo,hi=VIA[q.layer]; zb=zinfo[lo][0]; zt=zinfo[hi][0]+zinfo[hi][1]
            if zt<=zb: continue
            cen.append(((a+c)/2,(b+d)/2,(zb+zt)/2)); ext.append((VIA_W,VIA_W,zt-zb)); col.append(VCOL)
    if not cen: return None
    cen=np.asarray(cen); ext=np.asarray(ext); col=np.asarray(col,dtype=np.uint8); N=len(cen)
    V=(_UV[None]*ext[:,None,:]+cen[:,None,:]).reshape(-1,3)
    F=(_UF[None]+(np.arange(N)*8)[:,None,None]).reshape(-1,3)
    VC=np.concatenate([np.repeat(col,8,axis=0),np.full((N*8,1),255,np.uint8)],axis=1)
    m=trimesh.Trimesh(vertices=V,faces=F,process=False); m.visual.vertex_colors=VC; return m

lib=gdstk.read_gds(GDS_FILE); src=lib.top_level()[0]
cells={c.name:c for c in lib.cells}
# 1) top-cell own metal -> parent.glb
pm=build_mesh(src.polygons)
if pm: pm.export(os.path.join(OUTD,"parent.glb")); print(f"parent.glb: {len(pm.faces)} faces ({time.time()-t:.0f}s)",flush=True)
# 2) references in window, grouped by cell
inst={}
for r in src.references:
    ox,oy=r.origin
    if not (X0<=ox<=X1 and Y0<=oy<=Y1): continue
    inst.setdefault(r.cell.name,[]).append([round(ox,4),round(oy,4),round(math.degrees(r.rotation),3),1 if r.x_reflection else 0])
print(f"{len(inst)} cell types in window, {sum(len(v) for v in inst.values())} instances ({time.time()-t:.0f}s)",flush=True)
# 3) master mesh per cell type. Macros (hierarchical) reuse the already-built out/macros/<n>.glb
#    interiors; the new connection/channel metal comes from the flat std/via cells we build here.
MACRO_DIR=os.path.join(HERE,"out","macros")
meta={}
for nm,placements in sorted(inst.items(), key=lambda kv:-len(kv[1])):
    reuse=os.path.join(MACRO_DIR,nm+".glb")
    if cells[nm].references and os.path.exists(reuse):     # macro: reuse existing interior mesh
        meta[nm]={"glb":"../out/macros/"+nm+".glb","n":len(placements),"src":"macro"}
        print(f"  {nm}: {len(placements)}x -> reuse out/macros/{nm}.glb ({time.time()-t:.0f}s)",flush=True); continue
    m=build_mesh(cells[nm].polygons); nf=len(m.faces) if m else 0
    if m: m.export(os.path.join(OUTD,nm+".glb"))
    meta[nm]={"glb":"../out/gds_inst/"+nm+".glb","faces":nf,"n":len(placements),"src":"gds"}
    print(f"  {nm}: {len(placements)}x -> {nf} faces ({time.time()-t:.0f}s)",flush=True)
json.dump({"window":[X0,Y0,X1,Y1],"parent":"../out/gds_inst/parent.glb","instances":inst,"meta":meta},
          open(os.path.join(OUTD,"instances.json"),"w"))
print(f"done -> {OUTD}/instances.json ({time.time()-t:.0f}s)")
