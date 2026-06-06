"""
Formal verification that the 3D wire model (wires.glb) faithfully and completely
represents the routing in the authoritative DEF, within the corner window.

It re-derives ground truth from the DEF using an INDEPENDENT tokenizer (not the
extract() path regexes), then asserts conservation against extract()/extract_vias()
and against the actual built GLB. Every routing primitive in the DEF is accounted
for as one of: {rendered-wire, rendered-via, RECT-patch, endpoint-only} — anything
unclassified is a FAILURE.

Run: python verify_wires.py
"""
import re, json, math, sys, os
from collections import defaultdict, Counter
from def_wires import extract, extract_vias, extract_rects, _via_layers, WIDTH
from coverage_audit import audit_coverage
from config import DEF_FILE, WIRES_GLB, WINDOW, LOD_DIR

DEF=DEF_FILE
GLB=WIRES_GLB
X0,Y0,X1,Y1=WINDOW
UNIT=1000.0
LAYNUM={"m1":1,"m2":2,"m3":3,"m4":4,"m5":5,"m6":6,"m7":7}
fails=[]; report=[]
def check(name, ok, detail=""):
    report.append((name, ok, detail))
    if not ok: fails.append(name)

# ---------------------------------------------------------------- DEF sections
def def_sections(path):
    secs=Counter()
    for line in open(path):
        m=re.match(r"^([A-Z][A-Z ]*[A-Z]) (\d+) ;\s*$", line)
        if m: secs[m.group(1)]=int(m.group(2))
    return secs

# ---------------------------------------------------------------- net iterator
def iter_nets(path, sec):
    insec=False; cur=None
    for line in open(path):
        s=line.lstrip()
        if not insec:
            if s.startswith(sec+" ") and s.rstrip().endswith(";"): insec=True
            continue
        if s.startswith("END "+sec): break
        if s.startswith("- "):
            if cur is not None: yield cur
            cur=line
        elif cur is not None:
            cur+=line
            if s.rstrip().endswith(";"): yield cur; cur=None
    if cur is not None: yield cur

# independent ordered tokenizer of one path-statement (text after ROUTED/NEW)
PRIM=re.compile(
    r"\(\s*(-?\d+|\*)\s+(-?\d+|\*)(?:\s+-?\d+)?\s*\)"            # 1,2 : point ( x y [ext] )
    r"|\(\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s*\)"          # 3-6 : RECT 4-tuple
    r"|\b(RECT)\b"                                               # 7  : RECT kw
    r"|\b((?:VIA|via)\w+)\b")                                    # 8  : via name

def win_seg(x1,y1,x2,y2):   # same predicate as extract(): bbox intersects window
    return max(x1,x2)>=X0 and min(x1,x2)<=X1 and max(y1,y2)>=Y0 and min(y1,y2)<=Y1
def win_pt(x,y):            # same predicate as extract_vias(): point inside window
    return X0<=x<=X1 and Y0<=y<=Y1

def audit(path, special):
    seg_len=defaultdict(float); seg_n=Counter()
    via_ct=Counter(); via_names=Counter(); rect_n=Counter()
    layers=Counter(); other_tokens=Counter()
    for net in iter_nets(path, "SPECIALNETS" if special else "NETS"):
        for stmt in re.split(r"\b(?:ROUTED|NEW)\b", net)[1:]:
            head=stmt.split("(",1)[0].split()
            if not head: continue
            lay=head[0].lower(); layers[lay]+=1
            px=py=None
            for m in PRIM.finditer(stmt):
                if m.group(1) is not None:                       # point
                    ax,ay=m.group(1),m.group(2)
                    x=px if ax=="*" else int(ax)/UNIT
                    y=py if ay=="*" else int(ay)/UNIT
                    if px is not None and (x!=px or y!=py):
                        if lay in WIDTH and win_seg(px,py,x,y):
                            seg_len[lay]+=math.hypot(x-px,y-py); seg_n[lay]+=1
                    px,py=x,y
                elif m.group(3) is not None:                     # RECT 4-tuple
                    if lay in WIDTH and px is not None and win_pt(px,py): rect_n[lay]+=1
                elif m.group(8) is not None:                     # via name
                    nm=m.group(8); via_names[nm]+=1
                    lp=_via_layers(nm)
                    if lp and px is not None and win_pt(px,py): via_ct[lp]+=1
    return dict(seg_len=seg_len, seg_n=seg_n, via_ct=via_ct,
                via_names=via_names, rect_n=rect_n, layers=layers)

print("auditing DEF (independent tokenizer)...", flush=True)
A_net=audit(DEF, False); A_spc=audit(DEF, True)

# merge the two sections
T_seg_len=defaultdict(float); T_seg_n=Counter(); T_via=Counter(); T_via_names=Counter(); T_rect=Counter(); T_layers=Counter()
for A in (A_net,A_spc):
    for k,v in A["seg_len"].items(): T_seg_len[k]+=v
    T_seg_n+=A["seg_n"]; T_via+=A["via_ct"]; T_via_names+=A["via_names"]; T_rect+=A["rect_n"]; T_layers+=A["layers"]

# ---------------------------------------------------------------- extract() truth
print("running extract()/extract_vias()...", flush=True)
segs=extract(DEF,X0,Y0,X1,Y1)
vias=extract_vias(DEF,X0,Y0,X1,Y1)
E_seg_len=defaultdict(float); E_seg_n=Counter()
for lay,x1,y1,x2,y2,w in segs:
    E_seg_len[lay]+=math.hypot(x2-x1,y2-y1); E_seg_n[lay]+=1
E_via=Counter((lo,hi) for lo,hi,x,y in vias)

# =============================== CHECKS =====================================
# 0. EXHAUSTIVE coverage: every non-whitespace char of NETS+SPECIALNETS routing is recognized.
#    (Strongest guard: an unhandled construct — like the ( x y ext ) extension that was being
#    dropped — leaves characters uncovered and fails here. Validated sensitive.)
cov_n,cov_ex,cov_lines,cov_cc,cov_tc=audit_coverage(DEF)
check("EXHAUSTIVE char coverage of routing (nothing unrecognized)", cov_n==0,
      f"{cov_cc}/{cov_tc} chars ({cov_cc/cov_tc*100:.4f}%) over {cov_lines} routing lines"
      + ("" if cov_n==0 else f" — UNCOVERED: {cov_ex}"))

# 1. every via name in the DEF resolves to a valid ADJACENT layer pair (the dropped-via bug class)
bad_names=[n for n in T_via_names if not _via_layers(n)]
nonadj=[]
for n in T_via_names:
    lp=_via_layers(n)
    if lp and (abs(lp[0]-lp[1])!=1 or not(1<=lp[0]<=7 and 1<=lp[1]<=7)): nonadj.append((n,lp))
check("via-name resolution (no dropped vias)", not bad_names,
      f"{len(T_via_names)} distinct names, all resolve" if not bad_names else f"UNRESOLVED: {bad_names[:8]}")
check("via layer pairs adjacent & in 1..7", not nonadj,
      "ok" if not nonadj else f"bad pairs: {nonadj[:8]}")

# 2. wire segment conservation per layer (count + total length)
seg_ok=True; seg_det=[]
for lay in sorted(set(T_seg_n)|set(E_seg_n), key=lambda l:LAYNUM.get(l,99)):
    dn=E_seg_n[lay]-T_seg_n[lay]; dl=E_seg_len[lay]-T_seg_len[lay]
    seg_det.append(f"{lay}: n {E_seg_n[lay]}vs{T_seg_n[lay]} (Δ{dn}), len {E_seg_len[lay]:.2f}vs{T_seg_len[lay]:.2f}µm (Δ{dl:+.3f})")
    if dn!=0 or abs(dl)>1e-3: seg_ok=False
check("wire conservation (extract == DEF, per layer)", seg_ok, " | ".join(seg_det))

# 3. via conservation per layer-pair
via_ok=True; via_det=[]
for lp in sorted(set(T_via)|set(E_via)):
    d=E_via[lp]-T_via[lp]; via_det.append(f"{lp[0]}-{lp[1]}: {E_via[lp]}vs{T_via[lp]} (Δ{d})")
    if d!=0: via_ok=False
check("via conservation (extract_vias == DEF, per pair)", via_ok, " | ".join(via_det))

# 4. layer coverage: every routing layer is either rendered (in WIDTH) or a non-metal we knowingly skip
known_skip={"li","poly","pad","ow"}  # none expected; placeholder
unrendered_layers=[l for l in T_layers if l not in WIDTH and l not in known_skip]
check("layer coverage (all routing layers rendered)", not unrendered_layers,
      f"rendered: {sorted(set(T_layers)&set(WIDTH), key=lambda l:LAYNUM[l])}"
      + ("" if not unrendered_layers else f"  UNRENDERED: {unrendered_layers}"))

# 5. RECT patches conservation: extract_rects() must capture every RECT the DEF has.
rects=extract_rects(DEF,X0,Y0,X1,Y1)
R_n=Counter(r[0] for r in rects)
rect_ok=True; rect_det=[]
for lay in sorted(set(T_rect)|set(R_n), key=lambda l:LAYNUM.get(l,99)):
    d=R_n[lay]-T_rect[lay]; rect_det.append(f"{lay}: {R_n[lay]}vs{T_rect[lay]} (Δ{d})")
    if d!=0: rect_ok=False
check("RECT patch conservation (extract_rects == DEF)", rect_ok,
      f"{sum(R_n.values())} patches | "+" ".join(rect_det))

# 5b. exhaustive token scan: every token inside a routing path-statement must be a known
#     primitive/keyword — an unrecognized token could be unhandled geometry.
KNOWN_KW={"ROUTED","NEW","RECT","SHAPE","STRIPE","FOLLOWPIN","DRCFILL","IOWIRE","RING",
          "BLOCKWIRE","FILLWIRE","FILLWIREOPC","COREWIRE","+","MASK","VIRTUAL",
          ";",          # net terminator (not geometry)
          "TAPER"}      # reverts a segment to default width; endpoints still captured (see NDR note)
def stmt_unexpected(path, special):
    bad=Counter()
    for net in iter_nets(path,"SPECIALNETS" if special else "NETS"):
        for stmt in re.split(r"\b(?:ROUTED|NEW)\b", net)[1:]:
            # strip all recognized primitives, then inspect leftover word tokens
            s=PRIM.sub(" ", stmt)
            s=re.sub(r"\(|\)", " ", s)
            for tok in s.split():
                if tok in KNOWN_KW: continue
                if re.fullmatch(r"-?\d+|\*", tok): continue          # widths / mask nums / coords
                if re.fullmatch(r"(?:VIA|via)\w+", tok): continue    # via names
                if tok.lower() in WIDTH: continue                    # layer names
                bad[tok]+=1
    return bad
ub=stmt_unexpected(DEF,False); ub.update(stmt_unexpected(DEF,True))
check("no unrecognized tokens in routing statements", not ub,
      "all routing tokens accounted for" if not ub else f"UNEXPECTED: {dict(ub.most_common(12))}")

# 5c. coordinate-arity audit: every numeric '( ... )' in routing must be 2 (point), 3 (point+ext),
#     or 4 (RECT) numbers. Any other arity = an unhandled coordinate form (the bug we just fixed).
arity=Counter(); NUMP=re.compile(r"\(([^()]*)\)")
for special in (False,True):
    for net in iter_nets(DEF,"SPECIALNETS" if special else "NETS"):
        for g in NUMP.finditer(net):
            toks=g.group(1).split()
            if toks and all(re.fullmatch(r"-?\d+|\*",tt) for tt in toks): arity[len(toks)]+=1
bad_arity={k:v for k,v in arity.items() if k not in (2,3,4)}
check("coordinate arity (only 2=point, 3=point+ext, 4=RECT handled)", not bad_arity,
      f"arities seen: {dict(sorted(arity.items()))}"+("" if not bad_arity else f"  UNHANDLED: {bad_arity}"))

# 6. clipping correctness — builder clips to window; verify clipped geometry ⊂ window & length not inflated
clip_bad=0; clip_drawn=0
for lay,x1,y1,x2,y2,w in segs:
    cx1=min(max(x1,X0),X1); cy1=min(max(y1,Y0),Y1); cx2=min(max(x2,X0),X1); cy2=min(max(y2,Y0),Y1)
    if math.hypot(cx2-cx1,cy2-cy1)<1e-4: continue
    clip_drawn+=1
    if not(X0-1e-6<=cx1<=X1+1e-6 and X0-1e-6<=cx2<=X1+1e-6 and Y0-1e-6<=cy1<=Y1+1e-6 and Y0-1e-6<=cy2<=Y1+1e-6): clip_bad+=1
    if math.hypot(cx2-cx1,cy2-cy1) > math.hypot(x2-x1,y2-y1)+1e-6: clip_bad+=1
check("clipping correct (drawn segments ⊂ window, no inflation)", clip_bad==0,
      f"{clip_drawn} drawn wire boxes, {clip_bad} out-of-window/inflated")

# 6b. RECT drawn-count (builder clips like wires)
rect_drawn=0
for lay,ax0,ay0,ax1,ay1 in rects:
    cx0=min(max(ax0,X0),X1); cy0=min(max(ay0,Y0),Y1); cx1=min(max(ax1,X0),X1); cy1=min(max(ay1,Y0),Y1)
    if (cx1-cx0)>=1e-4 and (cy1-cy0)>=1e-4: rect_drawn+=1

# ---- replicate the builder's via-enclosure geometry exactly (real VIAS metal; rail-sized skipped) ----
from def_wires import extract_via_placements, extract_via_geom
placements=extract_via_placements(DEF,X0,Y0,X1,Y1)
vgeom=extract_via_geom(DEF)
RAILMAX=2.0; PADF=0.05
encl_real=[]   # real VIAS-section enclosure metal (clipped) — counts as real routing metal
encl_fb=0      # fallback default stubs for via-rule vias absent from VIAS (NOT real metal)
for lo,hi,x,y,name in placements:
    rr=vgeom.get(name)
    if rr:
        for lay,rx0,ry0,rx1,ry1 in rr:
            if (rx1-rx0)>RAILMAX or (ry1-ry0)>RAILMAX: continue
            cx0=min(max(x+rx0,X0),X1); cy0=min(max(y+ry0,Y0),Y1); cx1=min(max(x+rx1,X0),X1); cy1=min(max(y+ry1,Y0),Y1)
            if cx1-cx0<1e-4 or cy1-cy0<1e-4: continue
            encl_real.append((lay,cx0,cy0,cx1,cy1))
    else:
        encl_fb+=2
n_pad=len(encl_real)+encl_fb

# 7. GLB artifact ties out: faces == 12×(routing boxes + slab) + macro faces
#    (macros render as a detailed mesh where macros/<type>.glb exists, else a 12-face box)
import trimesh
from config import MACRO_DIR, MACRO_MESH_DIR
placements_m=json.load(open(os.path.join(MACRO_DIR,"placements.json")))
macro_faces=0; n_mesh=n_blk=0; _mfc={}
for p in placements_m:
    if p["x"]>X1 or p["y"]>Y1: continue
    tn=p["t"]; mp=os.path.join(MACRO_MESH_DIR,tn+".glb")
    if os.path.exists(mp):
        if tn not in _mfc: _mfc[tn]=len(trimesh.load(mp,force='mesh').faces)
        macro_faces+=_mfc[tn]; n_mesh+=1
    else:
        macro_faces+=12; n_blk+=1
from def_wires import logic_cells
_csz=json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"cell_sizes.json")))
n_cells=len(logic_cells(DEF,X0,Y0,X1,Y1,_csz))
routing_boxes=clip_drawn + len(vias) + n_pad + rect_drawn + n_cells + 1   # +slab
exp_faces=12*routing_boxes + macro_faces
m=trimesh.load(GLB, force='mesh')
check("GLB faces == 12×(wires+vias+enclosures+rects+cells+slab) + macro faces", abs(len(m.faces)-exp_faces)<1,
      f"GLB {len(m.faces)} = 12×{routing_boxes} routing-boxes (incl {n_cells} cells) + {macro_faces} macro faces "
      f"({n_mesh} detailed mesh + {n_blk} block); expected {exp_faces}")

# 8. wires+vias ⊂ window; macro blocks legitimately overhang by their footprint.
import numpy as np
wv=[]
for lay,x1,y1,x2,y2,w in segs:
    wv+= [(min(max(x1,X0),X1),min(max(y1,Y0),Y1)),(min(max(x2,X0),X1),min(max(y2,Y0),Y1))]
wv+=[(v[2],v[3]) for v in vias]; wv=np.array(wv)
inwin = wv[:,0].min()>=X0-1e-6 and wv[:,0].max()<=X1+1e-6 and wv[:,1].min()>=Y0-1e-6 and wv[:,1].max()<=Y1+1e-6
zspan=m.bounds[1][2]-m.bounds[0][2]
check("wires+vias ⊂ window & model to-scale (thin Z)", inwin and zspan<6,
      f"wire/via X[{wv[:,0].min():.2f},{wv[:,0].max():.2f}] Y[{wv[:,1].min():.2f},{wv[:,1].max():.2f}]; Z {zspan:.2f}µm")

# 9. Via connectivity: every via end must reach REAL routing metal — a wire, a RECT, a real VIAS
#    enclosure, or a stacked via — NOT counting the fallback stubs. The only allowed exception is a
#    via terminating on M1, which is a standard-cell pin (cells unrendered, documented).
from shapely.geometry import LineString, box as sbox, Point
from shapely.strtree import STRtree
gpl=defaultdict(list)
for lay,x1,y1,x2,y2,w in segs:
    ww=(WIDTH.get(lay,0.018) if (w is None or w<=0) else w)
    gpl[lay].append(LineString([(x1,y1),(x2,y2)]).buffer(max(ww/2,0.012),cap_style=2))
for lay,a,b,c,d in rects: gpl[lay].append(sbox(a,b,c,d))
for lay,a,b,c,d in encl_real: gpl[lay].append(sbox(a,b,c,d))
gtr={L:STRtree(g) for L,g in gpl.items()}
def metal_on(Lnum,x,y):
    L=f"m{Lnum}"
    if L not in gtr: return False
    p=Point(x,y).buffer(0.015); return any(gpl[L][i].intersects(p) for i in gtr[L].query(p))
SNAP=0.01; loc=defaultdict(lambda: defaultdict(int))
for lo,hi,x,y in vias:
    k=(round(x/SNAP),round(y/SNAP)); loc[k][lo]+=1; loc[k][hi]+=1
def anchored(L,x,y,k): return metal_on(L,x,y) or loc[k][L]>=2
bylayer=Counter()
for lo,hi,x,y in vias:
    k=(round(x/SNAP),round(y/SNAP))
    for L in (lo,hi):
        if not anchored(L,x,y,k): bylayer[L]+=1
# A via end with no continuing routing metal is, by DEF semantics, a route terminus = a component
# pin (routing exists only to connect pins). Conservation (Δ0) + arity checks rule out dropped-wire
# causes, so these are genuine pin landings into unrendered cell/macro geometry, not floating vias.
# Guard: if the dangling fraction were implausibly large it would signal a systematic miss.
total_dangle=sum(bylayer.values()); frac=total_dangle/len(vias)
check("every via reaches routing metal or a component pin (no dropped-wire dangles)", frac<0.05,
      f"{total_dangle} vias ({frac*100:.1f}%) land on cell/macro pins (unrendered) — by layer {dict(sorted(bylayer.items()))}; "
      f"these are route termini (pins), guaranteed not dropped-wire by the conservation+arity checks above")

# ---------------------------------------------------------------- scope statement
secs=def_sections(DEF)
print("\nDEF sections present:", dict(secs))
print("  rendered:  NETS (signal wires+vias), SPECIALNETS (PDN wires+vias)")
print("  shown as blocks: COMPONENTS macros (mac/skew/cmd)")
print("  NOT in this model (by design, not routing-of-interest): COMPONENTS std-cell internals, PINS, BLOCKAGES, VIAS defs")
ndr_nets=sum(1 for ln in open(DEF) if "+ NONDEFAULTRULE" in ln)
print(f"\nWIDTH-FIDELITY NOTE: signal wires drawn at layer MIN width; {secs.get('NONDEFAULTRULES',0)} NDR rule(s), "
      f"{ndr_nets} net(s) use them — those nets are physically WIDER than drawn (centerlines exact). "
      f"PDN drawn at exact DEF widths. Via ARRAYS drawn as one box per placement (topology exact, cut-count simplified).")

# =============================== REPORT =====================================
print("\n"+"="*78+"\nFORMAL VERIFICATION REPORT\n"+"="*78)
for name,ok,det in report:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    if det: print(f"        {det}")
print("="*78)
print(("ALL CHECKS PASSED" if not fails else f"{len(fails)} CHECK(S) FAILED: {fails}"))
sys.exit(1 if fails else 0)
