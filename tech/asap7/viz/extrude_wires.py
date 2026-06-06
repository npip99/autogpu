"""Extrude GDS metal + via geometry (klayout_wires.py output) to a 3D glb on the real layer
z-stack. Vias become pillars so the metal stack connects vertically. NO DEF involved.
Usage: python extrude_wires.py <wires.json> <out.glb> [X0 Y0 X1 Y1 for optional top-down png]"""
import os, sys, json, numpy as np, trimesh
HERE=os.path.dirname(os.path.abspath(__file__))
WJSON=sys.argv[1]; OUT=sys.argv[2]
layers=json.load(open(f"{HERE}/asap7/layers.json")); user=json.load(open(f"{HERE}/asap7/user.json"))
z=0.0; zinfo={}
for n,c in layers.items():
    h=user["layers"][n].get("height",0.1); zinfo[n]=(z,h); z+=h
PAL={"m1":(255,59,48),"m2":(255,149,0),"m3":(255,214,10),"m4":(52,199,89),
     "m5":(50,173,230),"m6":(94,92,230),"m7":(255,45,146)}
D=json.load(open(WJSON)); metal=D["metal"] if isinstance(D,dict) else D; vias=D.get("vias",[]) if isinstance(D,dict) else []
parts=[]
for lay,ax0,ay0,ax1,ay1 in metal:
    if lay not in zinfo: continue
    w=ax1-ax0; h2=ay1-ay0
    if w<1e-4 or h2<1e-4: continue
    z0,h=zinfo[lay]; b=trimesh.creation.box(extents=[w,h2,h]); b.apply_translation([(ax0+ax1)/2,(ay0+ay1)/2,z0+h/2])
    b.visual.face_colors=np.array(PAL[lay]+(255,),dtype=np.uint8); parts.append(b)
VIA_W=0.016; vcol=np.array([150,150,164,255],dtype=np.uint8); nv=0
for lo,hi,cx,cy in vias:
    if lo not in zinfo or hi not in zinfo: continue
    zb=zinfo[lo][0]; zt=zinfo[hi][0]+zinfo[hi][1]
    if zt<=zb: continue
    p=trimesh.creation.box(extents=[VIA_W,VIA_W,zt-zb]); p.apply_translation([cx,cy,(zb+zt)/2])
    p.visual.face_colors=vcol; parts.append(p); nv+=1
chip=trimesh.util.concatenate(parts); chip.export(OUT)
print(f"{OUT}: {len(chip.faces)} faces from {len(metal)} metal + {nv} via pillars")
if len(sys.argv)>6:
    os.environ["PYOPENGL_PLATFORM"]="egl"; import pyrender; from PIL import Image
    X0,Y0,X1,Y1=[float(v) for v in sys.argv[3:7]]; w=X1-X0; h=Y1-Y0
    sc=pyrender.Scene(bg_color=[0,0,0,1],ambient_light=[1,1,1]); sc.add(pyrender.Mesh.from_trimesh(chip,smooth=False))
    cam=pyrender.OrthographicCamera(xmag=w/2,ymag=h/2,znear=0.01,zfar=400)
    M=np.eye(4); M[:3,3]=[(X0+X1)/2,(Y0+Y1)/2,80]; sc.add(cam,pose=M)
    PXW=1400; r=pyrender.OffscreenRenderer(PXW,int(PXW*h/w)); col,_=r.render(sc); r.delete()
    png=OUT.replace(".glb","_3d.png"); Image.fromarray(col).save(png); print("wrote",png)
