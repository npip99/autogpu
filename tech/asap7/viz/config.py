"""Shared paths/config for the compute_array routing visualization + verification.
Override any of these via environment variables."""
import os

# ORFS results dir holding <design>/base/6_final.{def,odb,gds}
ORFS_RESULTS = os.environ.get("ORFS_RESULTS",
    "/home/ubuntu/pipitone/gpu3/build/orfs/results/asap7")
DESIGN   = os.environ.get("VIZ_DESIGN", "compute_array_abut")
_BASE    = f"{ORFS_RESULTS}/{DESIGN}/base"
DEF_FILE = os.environ.get("VIZ_DEF", f"{_BASE}/6_final.def")
ODB_FILE = os.environ.get("VIZ_ODB", f"{_BASE}/6_final.odb")
GDS_FILE = os.environ.get("VIZ_GDS", f"{_BASE}/6_final.gds")

HERE     = os.path.dirname(os.path.abspath(__file__))
OUT_DIR  = os.environ.get("VIZ_OUT", os.path.join(HERE, "out"))
WIRES_GLB= os.path.join(OUT_DIR, "wires.glb")
# macro LOD tile meshes (mac/skew/cmd) built separately by the gds2stl step
LOD_DIR  = os.environ.get("VIZ_LOD", "/tmp/lod")

# corner crop window in microns (x0,y0,x1,y1)
WINDOW = tuple(float(v) for v in os.environ.get("VIZ_WINDOW", "0,0,235,235").split(","))
