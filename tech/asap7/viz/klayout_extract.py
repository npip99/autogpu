"""KLayout polygon EXTRACT (not render) over a window, full hierarchy. Run inside the ORFS image.
Reports, per metal layer, every polygon's bbox+size in the region — settles wire (long rect)
vs fill (tiny square). Env: GDS, VIZ_CROP="x0,y0,x1,y1" (µm)."""
import pya, os
gds=os.environ["GDS"]; x0,y0,x1,y1=[float(v) for v in os.environ["VIZ_CROP"].split(",")]
ly=pya.Layout(); ly.read(gds); top=ly.top_cell(); dbu=ly.dbu
reg=pya.Box(int(x0/dbu),int(y0/dbu),int(x1/dbu),int(y1/dbu))
NAME={19:"m1",20:"m2",30:"m3",40:"m4",50:"m5",60:"m6",70:"m7"}
for li in ly.layer_indexes():
    info=ly.get_info(li); lay=info.layer
    if lay not in NAME or info.datatype!=0: continue
    polys=[]
    it=top.begin_shapes_rec_touching(li,reg)
    while not it.at_end():
        sh=it.shape()
        if sh.is_polygon() or sh.is_box() or sh.is_path():
            b=sh.polygon.transformed(it.trans()).bbox() if sh.is_polygon() else sh.bbox().transformed(it.trans())
            wx=(b.right-b.left)*dbu; hy=(b.top-b.bottom)*dbu
            polys.append((round(b.bottom*dbu,3),round(b.top*dbu,3),round(wx,3),round(hy,3)))
        it.next()
    if not polys: continue
    polys.sort()
    tall=[p for p in polys if p[3]>0.5]      # >0.5µm tall = wire-like (vertical)
    sq  =[p for p in polys if p[2]<0.08 and p[3]<0.08]  # <80nm both = fill-like
    print(f"=== {NAME[lay]} (layer {lay}): {len(polys)} polys  | wire-like(tall>0.5um)={len(tall)} fill-like(<80nm sq)={len(sq)}")
    for p in polys[-8:]: print(f"    ylo={p[0]} yhi={p[1]} w={p[2]} h={p[3]}")
