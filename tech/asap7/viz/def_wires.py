import re, time
# Extract routed wire segments from a DEF, cropped to a window, in microns.
# Returns list of (layer_lower, x1,y1,x2,y2, width_um_or_None).
# width None -> signal net (use a fixed display width); a number -> PDN strap/followpin real width.
WIDTH={"m1":0.018,"m2":0.018,"m3":0.018,"m4":0.024,"m5":0.024,"m6":0.032,"m7":0.032}
# a DEF point is ( x y ) OR ( x y ext ) — the optional 3rd value is a wire-end extension
PT=re.compile(r"\(\s*(-?\d+|\*)\s+(-?\d+|\*)(?:\s+-?\d+)?\s*\)")
LEADW=re.compile(r"^\s*(\d+)\b")

def _flush(text, segs, x0,y0,x1,y1, unit, special):
    for m in re.finditer(r"(?:ROUTED|NEW)\s+(\w+)\s+(.*?)(?=\bNEW\b|;|$)", text, re.S):
        lay=m.group(1).lower(); body=m.group(2)
        if lay not in WIDTH: continue
        w=None
        if special:
            mw=LEADW.match(body)
            w=(int(mw.group(1))/unit) if mw else 0.0
        px=py=None
        for tok in PT.finditer(body):
            ax,ay=tok.group(1),tok.group(2)
            x=px if ax=="*" else int(ax); y=py if ay=="*" else int(ay)
            if px is not None and (x!=px or y!=py):
                X1,Y1,X2,Y2=px/unit,py/unit,x/unit,y/unit
                if max(X1,X2)>=x0 and min(X1,X2)<=x1 and max(Y1,Y2)>=y0 and min(Y1,Y2)<=y1:
                    segs.append((lay,X1,Y1,X2,Y2,w))
            px,py=x,y

def extract(def_path, x0,y0,x1,y1, unit=1000.0, sections=("NETS","SPECIALNETS")):
    t=time.time(); segs=[]
    for sec in sections:
        special=(sec=="SPECIALNETS"); insec=False; cur=None
        with open(def_path) as f:
            for line in f:
                s=line.lstrip()
                if not insec:
                    if s.startswith(sec+" ") and s.rstrip().endswith(";"): insec=True
                    continue
                if s.startswith("END "+sec): break
                if s.startswith("- "):
                    if cur is not None: _flush(cur,segs,x0,y0,x1,y1,unit,special)
                    cur=line
                elif cur is not None:
                    cur+=line
                    if s.rstrip().endswith(";"): _flush(cur,segs,x0,y0,x1,y1,unit,special); cur=None
        if cur is not None: _flush(cur,segs,x0,y0,x1,y1,unit,special)
    print(f"def_wires: {len(segs)} segments in window ({time.time()-t:.0f}s)", flush=True)
    return segs

VIA_RE=re.compile(r"\(\s*(-?\d+)\s+(-?\d+)(?:\s+-?\d+)?\s*\)\s+((?:VIA|via)\w+)")
def _via_layers(name):
    # handles VIA23, VIA34_1_2_58_52, via1_2_..., via6_7_... (suffixes after the layer pair)
    m=re.match(r"VIA(\d)(\d)",name) or re.match(r"via(\d+)_(\d+)",name)
    return (int(m.group(1)),int(m.group(2))) if m else None

RECT_RE=re.compile(r"\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+RECT\s+\(\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s*\)")
def extract_rects(def_path, x0,y0,x1,y1, unit=1000.0, sections=("NETS","SPECIALNETS")):
    # RECT fill/enclosure patches: returns (layer_lower, ax0,ay0,ax1,ay1) absolute corners (um).
    # A RECT is relative to the preceding point on the same path statement.
    t=time.time(); rects=[]
    for sec in sections:
        special=(sec=="SPECIALNETS"); insec=False; cur=None
        def flush(text):
            for m in re.finditer(r"(?:ROUTED|NEW)\s+(\w+)\s+(.*?)(?=\bNEW\b|;|$)", text, re.S):
                lay=m.group(1).lower(); body=m.group(2)
                if lay not in WIDTH: continue
                # walk points; when a RECT appears, it applies to the most recent point
                px=py=None; pos=0
                for tk in re.finditer(r"\(\s*(-?\d+|\*)\s+(-?\d+|\*)(?:\s+-?\d+)?\s*\)(?!\s*RECT)|"     # plain point (+opt ext)
                                      r"\(\s*(-?\d+)\s+(-?\d+)(?:\s+-?\d+)?\s*\)\s+RECT\s+\(\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s*\)", body):
                    if tk.group(1) is not None:
                        ax,ay=tk.group(1),tk.group(2)
                        px=px if ax=="*" else int(ax); py=py if ay=="*" else int(ay)
                    else:
                        bx=int(tk.group(3)); by=int(tk.group(4))
                        ax0=(bx+int(tk.group(5)))/unit; ay0=(by+int(tk.group(6)))/unit
                        ax1=(bx+int(tk.group(7)))/unit; ay1=(by+int(tk.group(8)))/unit
                        px,py=bx,by
                        lo_x,hi_x=sorted((ax0,ax1)); lo_y,hi_y=sorted((ay0,ay1))
                        if hi_x>=x0 and lo_x<=x1 and hi_y>=y0 and lo_y<=y1:
                            rects.append((lay,lo_x,lo_y,hi_x,hi_y))
        for line in open(def_path):
            s=line.lstrip()
            if not insec:
                if s.startswith(sec+" ") and s.rstrip().endswith(";"): insec=True
                continue
            if s.startswith("END "+sec): break
            if s.startswith("- "):
                if cur is not None: flush(cur)
                cur=line
            elif cur is not None:
                cur+=line
                if s.rstrip().endswith(";"): flush(cur); cur=None
        if cur is not None: flush(cur)
    print(f"def_rects: {len(rects)} RECT patches in window ({time.time()-t:.0f}s)", flush=True)
    return rects

def extract_via_placements(def_path, x0,y0,x1,y1, unit=1000.0, sections=("NETS","SPECIALNETS")):
    # returns (lo_layer, hi_layer, x_um, y_um, via_name)
    t=time.time(); vias=[]
    for sec in sections:
        insec=False
        with open(def_path) as f:
            for line in f:
                s=line.lstrip()
                if not insec:
                    if s.startswith(sec+" ") and s.rstrip().endswith(";"): insec=True
                    continue
                if s.startswith("END "+sec): break
                for mm in VIA_RE.finditer(line):
                    lp=_via_layers(mm.group(3))
                    if not lp: continue
                    X=int(mm.group(1))/unit; Y=int(mm.group(2))/unit
                    if x0<=X<=x1 and y0<=Y<=y1: vias.append((lp[0],lp[1],X,Y,mm.group(3)))
    print(f"def_vias: {len(vias)} vias in window ({time.time()-t:.0f}s)", flush=True)
    return vias

def extract_vias(def_path, x0,y0,x1,y1, unit=1000.0, sections=("NETS","SPECIALNETS")):
    return [(lo,hi,x,y) for lo,hi,x,y,_ in extract_via_placements(def_path,x0,y0,x1,y1,unit,sections)]

_METAL=re.compile(r"^M[1-7]$")
def extract_via_geom(def_path, unit=1000.0):
    # parse the VIAS section -> {via_name: [(metal_layer_lower, dx0,dy0,dx1,dy1)]} (um, relative to via origin)
    defs={}; insec=False; cur=None
    def finish(text):
        toks=text.split()
        name=toks[1]; rects=[]
        if "RECT" in toks:                                   # explicit-geometry via
            for m in re.finditer(r"RECT\s+(\w+)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)", text):
                lay=m.group(1)
                if _METAL.match(lay):
                    rects.append((lay.lower(), int(m.group(2))/unit,int(m.group(3))/unit,int(m.group(4))/unit,int(m.group(5))/unit))
        else:                                                # VIARULE generate: cut array + enclosure
            def grab(kw,n):
                mm=re.search(kw+r"\s+("+r"\s+".join([r"(-?\d+)"]*n)+r")", text);
                return [int(v) for v in mm.group(1).split()] if mm else None
            cs=grab("CUTSIZE",2); sp=grab("CUTSPACING",2); rc=grab("ROWCOL",2); en=grab("ENCLOSURE",4)
            lm=re.search(r"LAYERS\s+(\w+)\s+(\w+)\s+(\w+)", text)
            if cs and sp and rc and en and lm:
                nr,nc=rc; bx=nc*cs[0]+(nc-1)*sp[0]; by=nr*cs[1]+(nr-1)*sp[1]
                lo=lm.group(1); hi=lm.group(3)
                for lay,ex,ey in ((lo,en[0],en[1]),(hi,en[2],en[3])):
                    if _METAL.match(lay):
                        hx=(bx/2+ex)/unit; hy=(by/2+ey)/unit; rects.append((lay.lower(),-hx,-hy,hx,hy))
        if rects: defs[name]=rects
    for line in open(def_path):
        s=line.strip()
        if not insec:
            if s.startswith("VIAS ") and s.endswith(";"): insec=True
            continue
        if s.startswith("END VIAS"): break
        if s.startswith("- "):
            if cur: finish(cur)
            cur=s
        elif cur is not None: cur+=" "+s
    if cur: finish(cur)
    return defs

if __name__=="__main__":
    import sys
    from collections import Counter
    segs=extract(sys.argv[1], 0,0,235,235)
    print("by layer:",dict(Counter(s[0] for s in segs)))
    print("signal:",sum(1 for s in segs if s[5] is None),"  pdn:",sum(1 for s in segs if s[5] is not None))
    vias=extract_vias(sys.argv[1], 0,0,235,235)
    print("via pairs:",dict(Counter(f"{v[0]}-{v[1]}" for v in vias)))
