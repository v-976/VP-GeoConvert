"""Stage 1 of the geometry benchmark: coarse transform search per PDF/page.

Research tool. Prints candidates only; no results are written to the repository.
"""

import math
from collections import defaultdict

import dxf_reader as D
import pdf_reader as P
import transform_est as T

DXF = r'benchmark\TKA_Tunnelitie\TKA_Tunnelitie_Suunnitelmakartta_MAS!BG.dxf'
ART = r'research\results\archive\TKA_Tunnelitie'

FILES = [("3D-Win", "3dwin_probe.txt"),
         ("ProgeCAD Export", "proge_export_probe.txt"),
         ("ProgeCAD Print", "proge_print_probe.txt")]


def dxf_straight(ents):
    out = []
    for e in ents:
        if e.etype not in ("LINE", "LWPOLYLINE"):
            continue
        pts = e.pts
        if len(pts) < 2:
            continue
        for i in range(len(pts) - 1):
            out.append((pts[i], pts[i + 1]))
        if e.closed and pts[0] != pts[-1]:
            out.append((pts[-1], pts[0]))
    return out


def main():
    ents, hdr, blocks, _ = D.parse(DXF)
    dx = dxf_straight(ents)
    print("DXF straight segments (SURVEY X,Y): %d" % len(dx))

    # Long DXF segments carry the orientation signal most reliably.
    dx_long = [s for s in dx if T.seg_len(*s) > 0.5]
    print("DXF segments longer than 0.5 m: %d" % len(dx_long))

    for lab, fn in FILES:
        paths, pages = P.parse(ART + "\\" + fn)
        by_page = defaultdict(list)
        for p in paths:
            by_page[p.page].append(p)

        print()
        print("=" * 70)
        print("%s   pages=%d" % (lab, len(pages)))
        print("=" * 70)
        for pg in sorted(by_page):
            psegs = []
            for p in by_page[pg]:
                psegs.extend(P.segments_of(p))
            # discard implausibly tiny fragments for the orientation signal
            sig = [s for s in psegs if T.seg_len(*s) > 0.5]
            print()
            print("  --- page %d : %d LINE segments, %d longer than 0.5 pt ---"
                  % (pg, len(psegs), len(sig)))

            for eps in (+1, -1):
                cands = T.best_rotation(dx_long, sig, eps,
                                        bucket_deg=1.0, top=4)
                lbl = "no reflection " if eps > 0 else "REFLECTION v "
                print("    %s top rotations:" % lbl)
                for sc, deg in cands:
                    print("       score=%.4f  th=%+8.2f deg" % (sc, deg))


if __name__ == "__main__":
    main()