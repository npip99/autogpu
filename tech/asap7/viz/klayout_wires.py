"""Extract metal + via geometry from the GDS (full hierarchy) over a window. No DEF involved.
Default keeps ALL metal (MINLONG=0) incl. via pads; vias come from the cut layers so the 3D
stack connects vertically. Run inside the ORFS image.
Env: GDS, VIZ_CROP="x0,y0,x1,y1" (µm), OUT (json), MINLONG (drop metal with longer side < this; 0=keep all)."""
import pya, os, json
gds=os.environ["GDS"]; x0,y0,x1,y1=[float(v) for v in os.environ["VIZ_CROP"].split(",")]
OUT=os.environ.get("OUT","/out/gds_wires.json"); MINLONG=float(os.environ.get("MINLONG","0"))
ly=pya.Layout(); ly.read(gds); top=ly.top_cell(); dbu=ly.dbu
reg=pya.Box(int(x0/dbu),int(y0/dbu),int(x1/dbu),int(y1/dbu))
METAL={19:"m1",20:"m2",30:"m3",40:"m4",50:"m5",60:"m6",70:"m7"}
VIA={21:("m1","m2"),25:("m2","m3"),35:("m3","m4"),45:("m4","m5"),55:("m5","m6"),65:("m6","m7")}
def boxes(li):
    it=top.begin_shapes_rec_touching(li,reg)
    while not it.at_end():
        sh=it.shape()
        if sh.is_polygon() or sh.is_box() or sh.is_path():
            yield (sh.polygon.transformed(it.trans()).bbox() if sh.is_polygon() else sh.bbox().transformed(it.trans()))
        it.next()
metal=[]; vias=[]; drop=0
for li in ly.layer_indexes():
    info=ly.get_info(li)
    if info.datatype!=0: continue
    if info.layer in METAL:
        nm=METAL[info.layer]
        for b in boxes(li):
            wx=(b.right-b.left)*dbu; hy=(b.top-b.bottom)*dbu
            if max(wx,hy)<MINLONG: drop+=1; continue
            cx0=max(b.left*dbu,x0); cy0=max(b.bottom*dbu,y0); cx1=min(b.right*dbu,x1); cy1=min(b.top*dbu,y1)
            if cx1-cx0>1e-4 and cy1-cy0>1e-4: metal.append([nm,round(cx0,4),round(cy0,4),round(cx1,4),round(cy1,4)])
    elif info.layer in VIA:
        lo,hi=VIA[info.layer]
        for b in boxes(li):
            cx=(b.left+b.right)/2*dbu; cy=(b.bottom+b.top)/2*dbu
            if x0<=cx<=x1 and y0<=cy<=y1: vias.append([lo,hi,round(cx,4),round(cy,4)])
json.dump({"metal":metal,"vias":vias},open(OUT,"w"))
print(f"wrote {OUT}: {len(metal)} metal rects, {len(vias)} vias  (MINLONG={MINLONG}, dropped {drop})")
