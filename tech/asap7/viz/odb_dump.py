# Run inside the ORFS image with: openroad -python odb_dump.py
# Independently extracts routing geometry from 6_final.odb (OpenROAD's authoritative DB)
# within the corner window, and prints per-layer length / via / rect tallies as JSON.
import odb, json, math
db = odb.dbDatabase.create()
odb.read_db(db, "/data/6_final.odb")
block = db.getChip().getBlock()
dbu = block.getDbUnitsPerMicron()
W = int(235*dbu)
def inwin(x1,y1,x2,y2): return max(x1,x2)>=0 and min(x1,x2)<=W and max(y1,y2)>=0 and min(y1,y2)<=W
def ptin(x,y): return 0<=x<=W and 0<=y<=W

from collections import Counter, defaultdict
reg_len=defaultdict(float); spc_len=defaultdict(float); spc_area=defaultdict(float)
via_ct=Counter(); rect_ct=Counter()
n_net=n_snet=0

D=odb.dbWireDecoder
for net in block.getNets():
    wire = net.getWire()
    if wire is None: continue
    n_net+=1
    dec=D(); dec.begin(wire); op=dec.next()
    layer=None; px=py=None
    while op != D.END_DECODE:
        if op in (D.PATH, D.JUNCTION, D.SHORT, D.VWIRE):
            layer=dec.getLayer().getName(); px=py=None
        elif op == D.POINT:
            x,y=dec.getPoint()
            if px is not None and inwin(px,py,x,y): reg_len[layer]+=math.hypot(x-px,y-py)/dbu
            px,py=x,y
        elif op == D.POINT_EXT:
            x,y,e=dec.getPoint_ext()
            if px is not None and inwin(px,py,x,y): reg_len[layer]+=math.hypot(x-px,y-py)/dbu
            px,py=x,y
        elif op in (D.VIA, D.TECH_VIA):
            v=dec.getTechVia() if op==D.TECH_VIA else dec.getVia()
            try: bl=v.getBottomLayer().getName(); tl=v.getTopLayer().getName()
            except: bl=tl=None
            if bl and px is not None and ptin(px,py): via_ct[tuple(sorted((bl,tl)))]+=1
        elif op == D.RECT:
            # RECT is anchored at the current point; count it there (geometry not needed for the tally)
            if layer and px is not None and ptin(px,py): rect_ct[layer]+=1
        op=dec.next()

for net in block.getNets():
    if not net.isSpecial(): continue
    n_snet+=1
    for sw in net.getSWires():
        for box in sw.getWires():
            if box.isVia():
                v=box.getTechVia() or box.getBlockVia()
                try: bl=v.getBottomLayer().getName(); tl=v.getTopLayer().getName()
                except: bl=tl=None
                cx=(box.xMin()+box.xMax())//2; cy=(box.yMin()+box.yMax())//2
                if bl and ptin(cx,cy): via_ct[tuple(sorted((bl,tl)))]+=1
            else:
                L=box.getTechLayer().getName()
                x1,y1,x2,y2=box.xMin(),box.yMin(),box.xMax(),box.yMax()
                if inwin(x1,y1,x2,y2):
                    w=(x2-x1)/dbu; h=(y2-y1)/dbu
                    spc_len[L]+=max(w,h); spc_area[L]+=w*h
out=dict(dbu=dbu, n_net=n_net, n_snet=n_snet,
         reg_len={k:round(v,2) for k,v in reg_len.items()},
         spc_len={k:round(v,2) for k,v in spc_len.items()},
         spc_area={k:round(v,2) for k,v in spc_area.items()},
         via_ct={f"{a}|{b}":c for (a,b),c in via_ct.items()},
         rect_ct=dict(rect_ct))
print("ODB_JSON_BEGIN"); print(json.dumps(out)); print("ODB_JSON_END")
