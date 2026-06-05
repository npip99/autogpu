"""Flat bright top-down of GDS wire rects (klayout_wires.py output), painter's order by layer.
No lighting -> full-brightness PAL, matches the KLayout 2D. Usage:
  python render_wires_flat.py <wires.json> <out.png> X0 Y0 X1 Y1 [pxw]"""
import sys, json, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
WJSON=sys.argv[1]; OUT=sys.argv[2]; X0,Y0,X1,Y1=[float(v) for v in sys.argv[3:7]]
PXW=int(sys.argv[7]) if len(sys.argv)>7 else 1400
ORDER=["m1","m2","m3","m4","m5","m6","m7"]            # paint low->high so upper layers sit on top
PAL={"m1":"#ff3b30","m2":"#ff9500","m3":"#ffd60a","m4":"#34c759",
     "m5":"#32ade6","m6":"#5e5ce6","m7":"#ff2d92"}
W=json.load(open(WJSON))
fig=plt.figure(figsize=(PXW/100,PXW*(Y1-Y0)/(X1-X0)/100),dpi=100)
ax=fig.add_axes([0,0,1,1]); ax.set_xlim(X0,X1); ax.set_ylim(Y0,Y1); ax.set_facecolor("black"); ax.axis("off")
n=0
for lay in ORDER:
    for L,ax0,ay0,ax1,ay1 in W:
        if L!=lay: continue
        ax.add_patch(Rectangle((ax0,ay0),ax1-ax0,ay1-ay0,facecolor=PAL[lay],edgecolor="none")); n+=1
fig.savefig(OUT,facecolor="black"); print(f"wrote {OUT}: {n} rects, window x[{X0},{X1}] y[{Y0},{Y1}]")
