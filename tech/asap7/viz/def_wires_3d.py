import json, numpy as np, trimesh, time, os, math, sys
from def_wires import extract, extract_via_placements, extract_via_geom, extract_rects, WIDTH
from config import DEF_FILE, WIRES_GLB, LOD_DIR, WINDOW, MACRO_DIR
t=time.time()
HERE=os.path.dirname(os.path.abspath(__file__))
DEF=DEF_FILE
X0,Y0,X1,Y1=WINDOW
ZEXAG = float(sys.argv[1]) if len(sys.argv)>1 else 1.0   # vertical exaggeration (1=real)
OUT   = sys.argv[2] if len(sys.argv)>2 else WIRES_GLB
MACROS_ON = (len(sys.argv)<4 or sys.argv[3]=="1")        # draw translucent macro blocks?
PDN_ON    = (len(sys.argv)<5 or sys.argv[4]=="1")        # draw PDN power straps/rails?
os.makedirs(os.path.dirname(OUT), exist_ok=True)
layers=json.load(open(os.path.join(HERE,"asap7/layers.json"))); user=json.load(open(os.path.join(HERE,"asap7/user.json")))
z=0.0; zinfo={}
for n,c in layers.items():
    h=user["layers"][n].get("height",0.1); zinfo[n]=(z*ZEXAG, h*ZEXAG); z+=h
PAL={"m1":(255,59,48),"m2":(255,149,0),"m3":(255,214,10),"m4":(52,199,89),
     "m5":(50,173,230),"m6":(94,92,230),"m7":(255,45,146)}

segs=extract(DEF,X0,Y0,X1,Y1)
parts=[]
for lay,x1,y1,x2,y2,w in segs:
    if lay not in zinfo: continue
    if (w is not None) and not PDN_ON: continue   # skip PDN straps/rails in signal-only mode
    # clip the segment to the window so long wires/straps don't run off across the chip
    x1=min(max(x1,X0),X1); y1=min(max(y1,Y0),Y1); x2=min(max(x2,X0),X1); y2=min(max(y2,Y0),Y1)
    z0,h=zinfo[lay]; dx=x2-x1; dy=y2-y1; L=math.hypot(dx,dy)
    if L<1e-4: continue
    ww = WIDTH.get(lay,0.018) if (w is None or w<=0) else w   # REAL width per layer / strap
    hh=h                                                       # REAL layer thickness
    b=trimesh.creation.box(extents=[L+ww, ww, hh])
    R=trimesh.transformations.rotation_matrix(math.atan2(dy,dx),[0,0,1])
    b.apply_transform(R)
    b.apply_translation([(x1+x2)/2,(y1+y2)/2, z0+hh/2])
    b.visual.face_colors=np.array(PAL[lay]+(255,),dtype=np.uint8)
    parts.append(b)
print(f"{len(parts)} wire tubes ({time.time()-t:.0f}s)",flush=True)

# vias: vertical connector + the via's REAL metal enclosure on each layer (from the VIAS section).
# Enclosures wider than RAILMAX are die-wide power-rail via arrays already drawn as wires -> skip.
# Z-FIGHTING AVOIDANCE: the via is drawn (a) strictly THINNER than the min wire width so its side
# faces never coincide with a wire's, and (b) with its top/bottom caps RECESSED a fraction of the
# layer thickness into the metal, so the caps are buried inside the wire/pad rather than flush.
VIA_W=min(WIDTH.values())*0.66; PAD=0.05; RAILMAX=2.0; vcol=np.array([110,110,124,255],dtype=np.uint8)
vgeom=extract_via_geom(DEF)
nv=0; npad=0
for lo,hi,x,y,name in extract_via_placements(DEF,X0,Y0,X1,Y1):
    nlo=f"m{lo}"; nhi=f"m{hi}"
    if nlo not in zinfo or nhi not in zinfo: continue
    zb=zinfo[nlo][0]+0.15*zinfo[nlo][1]; zt=zinfo[nhi][0]+0.85*zinfo[nhi][1]   # recess caps into metal
    if zt<=zb: continue
    v=trimesh.creation.box(extents=[VIA_W,VIA_W,zt-zb]); v.apply_translation([x,y,(zb+zt)/2])
    v.visual.face_colors=vcol; parts.append(v); nv+=1
    rects=vgeom.get(name)
    if rects:                                              # real enclosure metal, clipped to window
        for lay,rx0,ry0,rx1,ry1 in rects:
            if lay not in zinfo or (rx1-rx0)>RAILMAX or (ry1-ry0)>RAILMAX: continue
            cx0=min(max(x+rx0,X0),X1); cy0=min(max(y+ry0,Y0),Y1)
            cx1=min(max(x+rx1,X0),X1); cy1=min(max(y+ry1,Y0),Y1)
            if cx1-cx0<1e-4 or cy1-cy0<1e-4: continue
            z0,h=zinfo[lay]; p=trimesh.creation.box(extents=[cx1-cx0,cy1-cy0,h]); p.apply_translation([(cx0+cx1)/2,(cy0+cy1)/2,z0+h/2])
            p.visual.face_colors=np.array(PAL[lay]+(255,),dtype=np.uint8); parts.append(p); npad+=1
    else:                                                  # via-rule single via not in VIAS -> small default pad
        for nm in (nlo,nhi):
            z0,h=zinfo[nm]; p=trimesh.creation.box(extents=[PAD,PAD,h]); p.apply_translation([x,y,z0+h/2])
            p.visual.face_colors=np.array(PAL[nm]+(255,),dtype=np.uint8); parts.append(p); npad+=1
print(f"{nv} via pillars + {npad} enclosure rects ({time.time()-t:.0f}s)",flush=True)

# RECT fill/enclosure patches (small metal rectangles on each layer)
nr=0
for lay,ax0,ay0,ax1,ay1 in extract_rects(DEF,X0,Y0,X1,Y1):
    if lay not in zinfo: continue
    cx0=min(max(ax0,X0),X1); cy0=min(max(ay0,Y0),Y1); cx1=min(max(ax1,X0),X1); cy1=min(max(ay1,Y0),Y1)
    w=cx1-cx0; h2=cy1-cy0
    if w<1e-4 or h2<1e-4: continue
    z0,h=zinfo[lay]
    r=trimesh.creation.box(extents=[w,h2,h]); r.apply_translation([(cx0+cx1)/2,(cy0+cy1)/2,z0+h/2])
    r.visual.face_colors=np.array(PAL[lay]+(255,),dtype=np.uint8); parts.append(r); nr+=1
print(f"{nr} RECT patches ({time.time()-t:.0f}s)",flush=True)

# macros: render the DETAILED committed mesh (macros/<type>.glb) where present, else a
# translucent footprint block (footprints.json). Placements + footprints live in MACRO_DIR.
if MACROS_ON:
    placements=json.load(open(os.path.join(MACRO_DIR,"placements.json")))
    footprints=json.load(open(os.path.join(MACRO_DIR,"footprints.json")))
    def xf(p):
        T=np.eye(4)
        if p["mir"]: T[1,1]=-1
        T=trimesh.transformations.rotation_matrix(p.get("rot",0),[0,0,1])@T
        return trimesh.transformations.translation_matrix([p["x"],p["y"],0])@T
    BH=2.7*ZEXAG; mesh_cache={}; nmesh=nblk=0
    for p in placements:
        if p["x"]>X1 or p["y"]>Y1: continue
        tn=p["t"]; mpath=os.path.join(MACRO_DIR,tn+".glb")
        if os.path.exists(mpath):                              # detailed mesh -> place it
            if tn not in mesh_cache: mesh_cache[tn]=trimesh.load(mpath,force='mesh')
            mm=mesh_cache[tn].copy(); mm.apply_transform(xf(p))
            if ZEXAG!=1.0: mm.vertices[:,2]*=ZEXAG
            parts.append(mm); nmesh+=1
        else:                                                  # footprint block
            (lx,ly),(hx,hy)=footprints[tn]
            blk=trimesh.creation.box(extents=[hx-lx,hy-ly,BH])
            blk.apply_translation([(lx+hx)/2,(ly+hy)/2,BH/2]); blk.apply_transform(xf(p))
            blk.visual.face_colors=np.array([70,80,100,55],dtype=np.uint8)
            parts.append(blk); nblk+=1
    print(f"macros: {nmesh} detailed meshes + {nblk} footprint blocks ({time.time()-t:.0f}s)",flush=True)

slab=trimesh.creation.box(extents=[X1,Y1,0.6]); slab.apply_translation([X1/2,Y1/2,-0.5])
slab.visual.face_colors=np.array([24,27,34,255],dtype=np.uint8); parts.append(slab)

chip=trimesh.util.concatenate(parts)
chip.export(OUT)
print(f"{OUT}: {len(chip.faces)} faces, {os.path.getsize(OUT)//1024}KB, ZEXAG={ZEXAG}, macros={MACROS_ON}, {time.time()-t:.0f}s")
