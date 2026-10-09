"""Independent checks: page-identity test, affine conditioning, and a circular
feature test using DXF CIRCLE centres. Research tool.
"""

import math
from collections import defaultdict

import dxf_reader as D
import pdf_reader as P
import matcher as M
import benchmark_lib as BL

DXF = (r'benchmark\TKA_Tunnelitie'
       r'\TKA_Tunnelitie_Suunnitelmakartta_MAS!BG.dxf')
ART = r'research\results\archive\TKA_Tunnelitie'


def main():
    # ---- 1. are ProgeCAD Export pages 1 and 2 identical? ---------------
    paths, pages = P.parse(ART + r"\proge_export_probe.txt")
    by_page = defaultdict(list)
    for p in paths:
        by_page[p.page].append(p)

    def sig(pg):
        pts = []
        for p in by_page[pg]:
            for _k, u, v, _c in p.segs:
                pts.append((round(u, 4), round(v, 4)))
        pts.sort()
        return pts

    s1, s2 = sig(1), sig(2)
    same = (s1 == s2)
    print("=== ProgeCAD Export page identity test ===")
    print("  page1 vertices: %d   page2 vertices: %d" % (len(s1), len(s2)))
    print("  identical sorted vertex sets: %s" % same)
    if not same and len(s1) == len(s2):
        diff = sum(1 for a, b in zip(s1, s2) if a != b)
        print("  differing entries: %d / %d" % (diff, len(s1)))
    s0 = sig(0)
    print("  page0 vs page1 identical: %s (sizes %d / %d)"
          % (s0 == s1, len(s0), len(s1)))

    # ---- 2. affine conditioning on the matched point set ----------------
    print()
    print("=== affine conditioning analysis ===")
    print("  DXF geometry is overwhelmingly straight edges, so matched vertex")
    print("  pairs concentrate on a few lines. The affine normal matrix is")
    print("  therefore close to rank-deficient in the perpendicular direction.")
    ents, hdr, blocks, _ = D.parse(DXF)
    dx = BL.dxf_segments(ents)
    for lab, fn in BL.FILES:
        paths2, pages2 = P.parse(ART + "\\" + fn)
        bp = defaultdict(list)
        for p in paths2:
            bp[p.page].append(p)
        for pg in sorted(bp):
            pts = []
            for p in bp[pg]:
                for _k, u, v, _c in p.segs:
                    pts.append((u, v))
            us = [q[0] for q in pts]
            vs = [q[1] for q in pts]
            cu = sum(us) / len(us)
            cv = sum(vs) / len(vs)
            Suu = sum((u - cu) ** 2 for u in us)
            Svv = sum((v - cv) ** 2 for v in vs)
            Suv = sum((u - cu) * (v - cv) for u, v in pts)
            det = Suu * Svv - Suv * Suv
            # condition number of the 2x2 normal matrix
            tr = Suu + Svv
            disc = math.sqrt(max(0.0, (Suu - Svv) ** 2 + 4 * Suv * Suv))
            l1 = (tr + disc) / 2.0
            l2 = (tr - disc) / 2.0
            cond = (l1 / l2) if l2 > 0 else float('inf')
            print("  %-17s page %d : det(normal)=%.4g  eigenvalue ratio=%.4g"
                  % (lab, pg, det, cond))

    # ---- 3. DXF CIRCLE centres as an independent feature class ---------
    print()
    print("=== DXF CIRCLE inventory (SURVEY) ===")
    circ = [e for e in ents if e.etype == "CIRCLE" and e.center]
    print("  circles: %d" % len(circ))
    rs = sorted(e.radius for e in circ)
    print("  radii (m): %s" % sorted(set(round(r, 5) for r in rs)))
    xs = [e.center[0] for e in circ]
    ys = [e.center[1] for e in circ]
    print("  centre extent  X %.3f .. %.3f   Y %.3f .. %.3f"
          % (min(xs), max(xs), min(ys), max(ys)))
    # nearest-neighbour spacing between circle centres
    best = []
    for i, a in enumerate(circ):
        d = min(math.hypot(a.center[0] - b.center[0], a.center[1] - b.center[1])
                for j, b in enumerate(circ) if j != i)
        best.append(d)
    print("  nearest-neighbour spacing: min=%.3f m  median=%.3f m"
          % (min(best), sorted(best)[len(best) // 2]))
    print("  -> a circle centre is distinguishable from its neighbours when the")
    print("     nearest neighbour is farther than the extraction tolerance.")

    # ---- 4. which DXF entity types survive into each PDF? ---------------
    print()
    print("=== survival of DXF geometry classes (crude indicators) ===")
    for lab, fn in BL.FILES:
        paths3, pages3 = P.parse(ART + "\\" + fn)
        n_bez = sum(sum(1 for s in p.segs if s[0] == "CUBIC_BEZIER")
                    for p in paths3)
        n_line = sum(sum(1 for s in p.segs if s[0] == "LINE") for p in paths3)
        n_close = sum(sum(1 for s in p.segs if s[3]) for p in paths3)
        print("  %-17s CUBIC_BEZIER=%6d  LINE=%7d  close-flags=%6d"
              % (lab, n_bez, n_line, n_close))


if __name__ == "__main__":
    main()