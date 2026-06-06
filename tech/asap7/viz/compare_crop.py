"""
A/B top-down crop: render the SAME window from (1) the 2D GDS (KLayout, in the ORFS image)
and (2) our 3D model (base_routing + parent FEOL + instanced macro meshes), so the two can be
compared pixel-for-pixel to debug what's in the GDS but not in the 3D.

Usage:  python compare_crop.py X Y W H        # µm; lower-left origin
Outputs: out/cmp_2d.png (GDS) and out/cmp_3d.png (3D model)
"""
import os, sys, subprocess, numpy as np, trimesh
HERE=os.path.dirname(os.path.abspath(__file__))
x,y,w,h=[float(v) for v in sys.argv[1:5]]; X0,Y0,X1,Y1=x,y,x+w,y+h
PXW=int(os.environ.get("VIZ_W","1600"))
from config import GDS_FILE

# --- 3D: our model, orthographic top-down, cropped ---
def render3d():
    os.environ["PYOPENGL_PLATFORM"]="egl"; import pyrender
    from PIL import Image
    import json
    parts=[trimesh.load(f"{HERE}/out/base_routing.glb",force='mesh'),
           trimesh.load(f"{HERE}/out/parent_feol_logic.glb",force='mesh')]
    P=json.load(open(f"{HERE}/macros/placements.json")); meshes={}
    for p in P:
        if not (X0-40<=p["x"]<=X1+1 and Y0-40<=p["y"]<=Y1+1): continue
        mp=f"{HERE}/out/macros/{p['t']}.glb"
        if not os.path.exists(mp): continue
        if p["t"] not in meshes: meshes[p["t"]]=trimesh.load(mp,force='mesh')
        m=meshes[p["t"]].copy(); m.apply_translation([p["x"],p["y"],0]); parts.append(m)
    chip=trimesh.util.concatenate(parts)
    # crop by triangle BBOX overlap (not centroid): long wires/PDN straps span far, so a
    # centroid test would drop them. bbox-overlap keeps any triangle touching the window.
    tri=chip.triangles; tmin=tri.min(axis=1); tmax=tri.max(axis=1)
    sel=(tmax[:,0]>=X0)&(tmin[:,0]<=X1)&(tmax[:,1]>=Y0)&(tmin[:,1]<=Y1)
    chip=chip.submesh([np.where(sel)[0]],append=True)
    sc=pyrender.Scene(bg_color=[0,0,0,1],ambient_light=[1,1,1]); sc.add(pyrender.Mesh.from_trimesh(chip,smooth=False))
    cam=pyrender.OrthographicCamera(xmag=w/2,ymag=h/2,znear=0.01,zfar=400)
    M=np.eye(4); M[:3,3]=[(X0+X1)/2,(Y0+Y1)/2,80]; sc.add(cam,pose=M); sc.add(pyrender.DirectionalLight(intensity=2),pose=M)
    r=pyrender.OffscreenRenderer(PXW,int(PXW*h/w)); col,_=r.render(sc); r.delete()
    Image.fromarray(col).save(f"{HERE}/out/cmp_3d.png"); print(f"wrote out/cmp_3d.png ({len(chip.faces)} faces)")

# --- 2D: GDS via KLayout in the ORFS docker ---
def render2d():
    lyp="/home/ubuntu/.volare/asap7/libs.tech/klayout/asap7.lyp"
    img=subprocess.check_output(". "+HERE+"/../orfs/orfs_image.sh >/dev/null 2>&1; echo $ORFS_IMAGE",shell=True,executable="/bin/bash").decode().strip() or "openroad/orfs:latest"
    gdir=os.path.dirname(GDS_FILE); gname=os.path.basename(GDS_FILE)
    subprocess.run(["docker","run","--rm","-v",f"{gdir}:/gds:ro","-v",f"{os.path.dirname(lyp)}:/lyp:ro",
        "-v",f"{HERE}:/work:ro","-v",f"{HERE}/out:/out","-e",f"GDS=/gds/{gname}","-e","LYP=/lyp/asap7.lyp",
        "-e",f"VIZ_CROP={X0},{Y0},{X1},{Y1}","-e",f"VIZ_W={PXW}","-e","OUT=/out/cmp_2d.png",
        "--entrypoint","bash",img,"-c","klayout -b -r /work/klayout_crop.py"],check=True)

print(f"crop µm: x[{X0},{X1}] y[{Y0},{Y1}]")
render3d(); render2d()
print("done -> out/cmp_2d.png (GDS), out/cmp_3d.png (3D)")
