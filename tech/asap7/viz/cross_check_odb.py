"""Cross-check the DEF extractor against OpenROAD's own database (out/odb.json from odb_dump.py).
The authoritative independent witness: OpenROAD wrote the routing.

SCOPE: this is an AGGREGATE tie-out — per-layer TOTAL wire length (within 0.1 µm/layer, see the
abs()<0.1 below), per-pair via COUNT, per-layer RECT COUNT. It establishes extraction completeness
at the aggregate level (nothing en-masse dropped or fabricated), NOT per-segment placement: two
different geometries with equal per-layer totals would both pass. It also validates ONE design
against a committed reference (odb_reference.json), so it is a snapshot check, not a general
invariant — regenerate the reference if the extractor or the DEF changes."""
import json, math, sys, os
from collections import Counter, defaultdict
from def_wires import extract, extract_vias, extract_rects
from config import DEF_FILE, WINDOW
X0,Y0,X1,Y1=WINDOW
odb=json.load(open(sys.argv[1] if len(sys.argv)>1 else os.path.join(os.path.dirname(__file__),"odb_reference.json")))

segs=extract(DEF_FILE,X0,Y0,X1,Y1); vias=extract_vias(DEF_FILE,X0,Y0,X1,Y1); rects=extract_rects(DEF_FILE,X0,Y0,X1,Y1)
mine_len=defaultdict(float)
for lay,x1,y1,x2,y2,w in segs: mine_len[lay]+=math.hypot(x2-x1,y2-y1)
mine_via=Counter((lo,hi) for lo,hi,x,y in vias)
mine_rect=Counter(r[0] for r in rects)

odb_len={f"m{i}": round(odb["reg_len"].get(f"M{i}",0)+odb["spc_len"].get(f"M{i}",0),2) for i in range(1,8)}
odb_via={tuple(sorted(int(c) for c in k.replace("M","").split("|"))):v for k,v in odb["via_ct"].items()}
odb_rect={k.lower():v for k,v in odb["rect_ct"].items()}

ok=True
print(f"{'layer':6} {'mine_len':>12} {'odb_len':>12}  match")
for i in range(1,8):
    L=f"m{i}"; a=round(mine_len[L],2); b=odb_len[L]; m=abs(a-b)<0.1; ok&=m   # 0.1µm/layer tolerance
    print(f"{L:6} {a:12.2f} {b:12.2f}  {'OK' if m else 'MISMATCH'}")
print("via counts:", "OK" if mine_via==odb_via else f"MISMATCH mine={dict(mine_via)} odb={odb_via}"); ok&=(mine_via==odb_via)
print("rect counts:", "OK" if mine_rect==odb_rect else f"MISMATCH mine={dict(mine_rect)} odb={odb_rect}"); ok&=(mine_rect==odb_rect)
print("\n"+("CROSS-CHECK PASSED — extractor matches OpenROAD's database (aggregate totals)" if ok else "CROSS-CHECK FAILED"))
sys.exit(0 if ok else 1)
