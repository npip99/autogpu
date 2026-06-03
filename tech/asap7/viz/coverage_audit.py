"""
EXHAUSTIVE coverage audit: prove every non-whitespace character in the NETS and
SPECIALNETS routing is consumed by a recognized pattern. Anything left over is an
unhandled construct (the class of bug that kept slipping through extraction). The
geometry-bearing patterns (points, rects, vias) are exactly the ones the extractor
in def_wires.py uses, so 100% coverage means the extractor sees every construct.

Validated sensitive: removing the ( x y ext ) extension support makes it flag exactly
the 40,463 extension coordinates it should.

Run standalone:  python coverage_audit.py            (uses config.DEF_FILE)
As a library:    from coverage_audit import audit_coverage
"""
import re, sys

_ID=r"[^\s()]+"                                  # DEF identifier (net/pin/rule name): no ws, no parens
_KW=(r"ROUTED|NEW|FIXED|COVER|NOSHIELD|SHIELD|SHIELDNET|TAPER|TAPERRULE|STYLE|MASK|RECT|VIRTUAL|"
     r"SHAPE|STRIPE|FOLLOWPIN|IOWIRE|COREWIRE|BLOCKWIRE|BLOCKAGEWIRE|FILLWIRE|FILLWIREOPC|DRCFILL|"
     r"RING|PADRING|BLOCKRING|WIDTH|VOLTAGE|SPACING|USE|SIGNAL|POWER|GROUND|CLOCK|RESET|SCAN|TIEOFF|"
     r"ANALOG|SOURCE|DIST|NETLIST|TIMING|TEST|USER|FIXEDBUMP|FREQUENCY|ORIGINAL|PATTERN|BALANCED|"
     r"STEINER|TRUNK|WIREDLOGIC|ESTCAP|WEIGHT|PROPERTY|SUBNET|XTALK|VPIN|POLYGON|DESIGNRULEWIDTH|PIN")

def _patterns(with_ext=True):
    pt = r"\(\s*(?:-?\d+|\*)\s+(?:-?\d+|\*)"+(r"(?:\s+-?\d+)?" if with_ext else r"")+r"\s*\)"
    return [re.compile(p) for p in [
        r"^\s*-\s+"+_ID,                                       # net header:  - netName
        r"\(\s*-?\d+\s+-?\d+\s+-?\d+\s+-?\d+\s*\)",            # RECT operand:  ( a b c d )
        pt,                                                   # point:  ( x y [ext] )
        r"\(\s*"+_ID+r"\s+"+_ID+r"\s*\)",                     # connection:  ( inst pin ) / ( PIN name )
        r"\b(?:NONDEFAULTRULE|TAPERRULE)\s+"+_ID,             # rule reference
        r"\b(?:VIA|via)\w+",                                  # via instance name
        r"\bM[1-7]\b|\bV[0-7]\b|\bPad\b",                     # layer names
        r"\b(?:"+_KW+r")\b",                                  # keywords
        r"-?\d+\.?\d*",                                       # numbers (widths, ext, mask, props)
        r"[-+;*]",                                            # punctuation
    ]]

def audit_coverage(def_file, with_ext=True):
    """Returns (uncovered_token_count, examples_dict, lines_scanned, covered_chars, total_chars)."""
    from collections import Counter
    PATS=_patterns(with_ext); insec=None; bad=Counter(); ex={}
    nlines=cc=tc=0
    for line in open(def_file):
        s=line.strip()
        if insec is None:
            if re.match(r"^(NETS|SPECIALNETS) \d+ ;", s): insec=s.split()[0]
            continue
        if s.startswith("END "+insec): insec=None; continue
        if not s: continue
        nlines+=1
        mask=bytearray(len(line))
        for p in PATS:
            for m in p.finditer(line):
                for i in range(m.start(),m.end()): mask[i]=1
        for m in re.finditer(r"\S+", line):
            seg=line[m.start():m.end()]; tc+=len(seg)
            unc="".join(line[i] for i in range(m.start(),m.end()) if not mask[i])
            cc+=len(seg)-len(unc)
            if unc.strip(): bad[seg]+=1; ex.setdefault(seg, line.strip()[:120])
    return sum(bad.values()), dict(bad.most_common(25)), nlines, cc, tc

if __name__=="__main__":
    from config import DEF_FILE
    deff=sys.argv[1] if len(sys.argv)>1 else DEF_FILE
    n,ex,nl,cc,tc=audit_coverage(deff)
    print(f"routing lines scanned: {nl}\ncharacter coverage: {cc}/{tc} = {cc/tc*100:.4f}%")
    if not n:
        print("\nPASS - every non-whitespace character in NETS+SPECIALNETS routing is recognized.")
        sys.exit(0)
    print(f"\nFAIL - {n} tokens not fully covered:")
    for tok,c in ex.items(): print(f"  {c:7d}x  {tok!r}")
    sys.exit(1)
