"""GPU (pyrender/EGL) preview of the macros_instanced scene at a copy-pose, instanced so the
9 macros don't blow up memory. Reproduces the viewer's transforms + flat-shaded directional
lighting so we can iterate on the look fast. Usage: python pyrender_pose.py out.png [amb key fill]"""
import os, sys, json, math, numpy as np, trimesh
os.environ["PYOPENGL_PLATFORM"]="egl"; import pyrender
HERE=os.path.dirname(os.path.abspath(__file__))
OUT=sys.argv[1] if len(sys.argv)>1 else "out/pose.png"
AMB =float(sys.argv[2]) if len(sys.argv)>2 else 0.30
KEY =float(sys.argv[3]) if len(sys.argv)>3 else 4.0
FILL=float(sys.argv[4]) if len(sys.argv)>4 else 1.2
# --- pose (the viewer's copy-pose) ---
POSE=dict(layoutX=124.1, layoutY=151.8, height=124.90, yaw=-81.6, pitch=-83.0, roll=-0.0)
x,pit,yaw,rol=POSE["height"],math.radians(POSE["pitch"]),math.radians(POSE["yaw"]),math.radians(POSE["roll"])
# three.js Euler 'YXZ' rotation matrix (x=pitch, y=yaw, z=roll), columns per three.js Matrix4
c1,s1=math.cos(pit),math.sin(pit); c2,s2=math.cos(yaw),math.sin(yaw); c3,s3=math.cos(rol),math.sin(rol)
R=np.array([[c2*c3+s1*s2*s3, s1*s2*c3-c2*s3, c1*s2],
            [c1*s3,          c1*c3,          -s1 ],
            [c2*s1*s3-s2*c3, s2*s3+c2*s1*c3, c1*c2]])
campose=np.eye(4); campose[:3,:3]=R; campose[:3,3]=[POSE["layoutX"], POSE["height"], -POSE["layoutY"]]
# layout->world root rotation: Rx(-90deg): (x,y,z)->(x,z,-y)
ROOT=np.array([[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]],dtype=float)

sc=pyrender.Scene(bg_color=[0.043,0.051,0.063,1.0], ambient_light=[AMB,AMB,AMB])
def add(mesh, T):
    pm=pyrender.Mesh.from_trimesh(mesh, smooth=False)   # flat shading
    sc.add(pm, pose=T)
add(trimesh.load(f"{HERE}/out/base_routing.glb",force='mesh'), ROOT)
add(trimesh.load(f"{HERE}/out/parent_feol_logic.glb",force='mesh'), ROOT)
P=json.load(open(f"{HERE}/macros/placements.json")); cache={}
for p in P:
    if not (p["x"]<235 and p["y"]<235): continue
    mp=f"{HERE}/out/macros/{p['t']}.glb"
    if not os.path.exists(mp): continue
    if p["t"] not in cache: cache[p["t"]]=trimesh.load(mp,force='mesh')
    Tloc=np.eye(4); Tloc[:3,3]=[p["x"],p["y"],0]
    if p.get("mir"): Tloc=Tloc@np.diag([1,-1,1,1])
    add(cache[p["t"]], ROOT@Tloc)
# lights: key from above + opposite fill, directions in WORLD space
def dirlight(direction, inten):
    d=np.array(direction,float); d/=np.linalg.norm(d)
    z=-d; up=np.array([0,0,1.0]) if abs(d[1])>0.9 else np.array([0,1.0,0])
    xx=np.cross(up,z); xx/=np.linalg.norm(xx); yy=np.cross(z,xx)
    T=np.eye(4); T[:3,0]=xx; T[:3,1]=yy; T[:3,2]=z
    sc.add(pyrender.DirectionalLight(color=[1,1,1],intensity=inten), pose=T)
dirlight([-0.5,-1,-0.35], KEY)    # key: from (0.5,1,0.35) downward
dirlight([0.5,-0.4,0.45], FILL)   # fill: opposite
cam=pyrender.PerspectiveCamera(yfov=math.radians(60), znear=0.05, zfar=9000)
sc.add(cam, pose=campose)
r=pyrender.OffscreenRenderer(1200,760); col,_=r.render(sc); r.delete()
from PIL import Image; Image.fromarray(col).save(OUT)
print(f"wrote {OUT}  amb={AMB} key={KEY} fill={FILL}  (macros={len(cache)} types)")
