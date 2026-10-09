"""Stage 2: for every rotation/reflection hypothesis, estimate scale and
translation, then measure how many real DXF segments land on real PDF segments.

Research tool. An inlier is a DXF segment whose two endpoints, after the
transform, both fall within tol of a PDF segment of the matching length and
direction. This is the criterion that decides between hypotheses; orientation
histograms alone cannot.
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

DIST_TOL_PT = 1.5      # inlier tolerance in PAGE points


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


class PdfIndex:
    """Uniform grid over PAGE-space segments for fast proximity queries."""

    def __init__(self, segs, cell=8.0):
        self.segs = segs
        self.cell = cell
        self.grid = defaultdict(list)
        for i, (a, b) in enumerate(segs):
            x0 = int(min(a[0], b[0]) // cell)
            x1 = int(max(a[0], b[0]) // cell)
            y0 = int(min(a[1], b[1]) // cell)
            y1 = int(max(a[1], b[1]) // cell)
            for gx in range(x0, x1 + 1):
                for gy in range(y0, y1 + 1):
                    self.grid[(gx, gy)].append(i)

    def near(self, p, r):
        out = set()
        c = self.cell
        for gx in range(int((p[0] - r) // c), int((p[0] + r) // c) + 1):
            for gy in range(int((p[1] - r) // c), int((p[1] + r) // c) + 1):
                out.update(self.grid.get((gx, gy), ()))
        return out


def point_seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx = bx - ax
    dy = by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2
    if t < 0:
        t = 0.0
    elif t > 1:
        t = 1.0
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def count_inliers(tr, dx_segs, index, tol=DIST_TOL_PT):
    """Count DXF segments matching a PDF segment under transform tr."""
    hits = []
    for a, b in dx_segs:
        Ld = T.seg_len(a, b)
        if Ld <= 0:
            continue
        pa = tr.apply(*a)
        pb = tr.apply(*b)
        cand = index.near(pa, tol) | index.near(pb, tol)
        best = None
        for i in cand:
            qa, qb = index.segs[i]
            if abs(T.seg_len(qa, qb) - tr.s * Ld) > max(tol, 0.01 * tr.s * Ld):
                continue
            d = point_seg_dist(pa, qa, qb) + point_seg_dist(pb, qa, qb)
            if best is None or d < best[0]:
                best = (d, i)
        if best is not None and best[0] <= 2.0 * tol:
            hits.append((a, b, best[0] / 2.0))
    return hits


def main():
    ents, hdr, blocks, _ = D.parse(DXF)
    dx = dxf_straight(ents)
    dx_sig = [s for s in dx if T.seg_len(*s) > 0.5]

    for lab, fn in FILES:
        paths, pages = P.parse(ART + "\\" + fn)
        by_page = defaultdict(list)
        for p in paths:
            by_page[p.page].append(p)

        print()
        print("=" * 74)
        print("%s" % lab)
        print("=" * 74)
        for pg in sorted(by_page):
            psegs = []
            for p in by_page[pg]:
                psegs.extend(P.segments_of(p))
            sig = [s for s in psegs if T.seg_len(*s) > 0.5]
            index = PdfIndex(psegs)
            print()
            print("  page %d  (%d PAGE segments)" % (pg, len(psegs)))

            hyps = []
            for eps in (+1, -1):
                for sc, deg in T.best_rotation(dx_sig, sig, eps,
                                                bucket_deg=1.0, top=3):
                    hyps.append((sc, eps, deg))
            hyps.sort(reverse=True)

            best_overall = None
            for sc, eps, deg in hyps:
                res = T.estimate_scale_translation(
                    dx_sig, sig, eps, deg, min_dxf_len=0.5, min_pdf_len=1.0)
                if res is None:
                    print("     eps=%+d th=%+7.2f  -> scale estimation failed"
                          % (eps, deg))
                    continue
                scale, tx, ty, diag = res
                tr = T.Transform(scale, math.radians(deg), eps, tx, ty)
                hits = count_inliers(tr, dx, index)
                refl = "reflect-v" if eps < 0 else "no-reflect"
                print("     eps=%+d (%-11s) hist=%.3f th=%+7.2f  s=%12.6g  "
                      "t=(%.4f, %.4f)  INLIERS=%4d/%d"
                      % (eps, refl, sc, deg, scale, tx, ty,
                         len(hits), len(dx)))
                if best_overall is None or len(hits) > best_overall[0]:
                    best_overall = (len(hits), eps, deg, scale, tx, ty, tr, hits)
            if best_overall:
                n, eps, deg, scale, tx, ty, tr, hits = best_overall
                print("     -> best hypothesis: eps=%+d th=%+7.2f s=%.6g "
                      "inliers=%d" % (eps, deg, scale, n))
                if hits:
                    ds = sorted(h[2] for h in hits)
                    print("     -> inlier endpoint residual (PAGE pt): "
                          "median=%.4f p95=%.4f max=%.4f"
                          % (ds[len(ds) // 2], ds[int(0.95 * (len(ds) - 1))],
                             ds[-1]))


if __name__ == "__main__":
    main()