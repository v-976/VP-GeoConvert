"""Stage 3: recover the transform, then verify it by segment inliers and
least-squares refit on the inliers. Research tool.

Verification is the gate. A hypothesis is only accepted if a large fraction of
independent DXF segments genuinely land on PDF segments.
"""

import math
from collections import defaultdict

import dxf_reader as D
import pdf_reader as P
import transform_est as T
import profile_est as PR

DXF = r'benchmark\TKA_Tunnelitie\TKA_Tunnelitie_Suunnitelmakartta_MAS!BG.dxf'
ART = r'research\results\archive\TKA_Tunnelitie'

FILES = [("3D-Win", "3dwin_probe.txt"),
         ("ProgeCAD Export", "proge_export_probe.txt"),
         ("ProgeCAD Print", "proge_print_probe.txt")]


def dxf_segments(ents):
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


def dxf_points(ents):
    pts = []
    for e in ents:
        if e.etype in ("LINE", "LWPOLYLINE"):
            pts.extend(e.pts)
        if e.center:
            pts.append(e.center)
    return pts


def pdf_points(paths):
    pts = []
    for p in paths:
        for _k, u, v, _c in p.segs:
            pts.append((u, v))
    return pts


class Index:
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


def pseg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def match_segments(tr, dx_segs, index, tol_pt):
    """Return [(dxf_seg, pdf_seg, mean_endpoint_dist_pt)] for matched segments."""
    out = []
    for a, b in dx_segs:
        Ld = math.hypot(b[0] - a[0], b[1] - a[1])
        if Ld <= 0:
            continue
        pa = tr.apply(*a)
        pb = tr.apply(*b)
        cand = index.near(pa, tol_pt) | index.near(pb, tol_pt)
        best = None
        for i in cand:
            qa, qb = index.segs[i]
            if abs(math.hypot(qb[0] - qa[0], qb[1] - qa[1]) - tr.s * Ld) > \
               max(tol_pt, 0.008 * tr.s * Ld):
                continue
            d = pseg_dist(pa, qa, qb) + pseg_dist(pb, qa, qb)
            if best is None or d < best[0]:
                best = (d, i)
        if best is not None:
            out.append(((a, b), index.segs[best[1]], best[0] / 2.0))
    return out


def main():
    ents, hdr, blocks, _ = D.parse(DXF)
    dx = dxf_segments(ents)
    dpts = dxf_points(ents)
    dseg_sig = [s for s in dx if math.hypot(s[1][0] - s[0][0], s[1][1] - s[0][1]) > 0.5]
    dom_rad, dom_share = PR.dominant_direction(dseg_sig)
    dom_deg = math.degrees(dom_rad)
    print("DXF dominant straight-segment direction: %.3f deg "
          "(mod 180), length share computed on %d segments" % (dom_deg, len(dseg_sig)))

    for lab, fn in FILES:
        paths, pages = P.parse(ART + "\\" + fn)
        by_page = defaultdict(list)
        for p in paths:
            by_page[p.page].append(p)

        print()
        print("=" * 76)
        print("%s" % lab)
        print("=" * 76)
        for pg in sorted(by_page):
            sub = by_page[pg]
            psegs = []
            for p in sub:
                psegs.extend(P.segments_of(p))
            ppts = pdf_points(sub)
            print()
            print("  --- page %d ---" % pg)

            # rotation candidates from orientation histograms
            hyps = []
            for eps in (+1, -1):
                for sc, deg in T.best_rotation(dseg_sig, psegs, eps,
                                                bucket_deg=1.0, top=3):
                    hyps.append((sc, eps, deg))
            hyps.sort(reverse=True)

            results = []
            for sc, eps, deg in hyps:
                est = PR.estimate_from_profiles(dpts, ppts, dom_deg, eps, deg)
                if est is None:
                    continue
                # build transform from the two profile offsets
                c, sn = math.cos(est["ds"]), math.sin(est["ds"])
                cp, sp = math.cos(est["dp"]), math.sin(est["dp"])
                s = est["s_along"]
                # point on the SURVEY axis through origin with offset c1, and on
                # the perpendicular with offset c2
                th = math.radians(deg)
                if eps > 0:
                    phi_dir = dom_rad - th
                else:
                    phi_dir = dom_rad + th
                # reconstruct: translation is the offset of the two axes
                # SURVEY component along ds = c1, along ds2 = c2
                e = eps
                tx = s * (c * 0.0)  # placeholder, computed properly below
                # solve directly: X = s*ca*u - s*e*sa*v + tx ; Y = s*sa*u + s*e*ca*v + ty
                ca, sa = math.cos(th), math.sin(th)
                # least squares on the two offset constraints using centroid of PDF
                cu = sum(p[0] for p in ppts) / len(ppts)
                cv = sum(p[1] for p in ppts) / len(ppts)
                CU = s * (ca * cu - e * sa * cv)
                CV = s * (sa * cu + e * ca * cv)
                # SURVEY centroid is unknown; use the axis offsets instead:
                # project SURVEY centroid onto ds and ds2
                CXp = est["c_along"]
                CYp = est["c_perp"]
                CX = CXp * c - CYp * sn
                CY = CXp * sn + CYp * c
                tx = CX - CU
                ty = CY - CV
                tr = T.Transform(s, th, e, tx, ty)
                hits = match_segments(tr, dx, Index(psegs), 1.5)
                results.append((len(hits), sc, eps, deg, s, tr, hits))
                print("     eps=%+d th=%+7.2f hist=%.3f  s=%10.6g  "
                      "score=%.3f/%.3f  hits=%4d/%d"
                      % (eps, deg, sc, s, est["score_along"],
                         est["score_perp"], len(hits), len(dx)))
            if results:
                results.sort(key=lambda r: -r[0])
                n, sc, eps, deg, s, tr, hits = results[0]
                print("     -> BEST eps=%+d th=%+7.2f s=%.8g hits=%d"
                      % (eps, deg, s, n))
                if hits:
                    ds_ = sorted(h[2] for h in hits)
                    print("     -> endpoint residual (PAGE pt): median=%.5f "
                          "p95=%.5f max=%.5f"
                          % (ds_[len(ds_) // 2], ds_[int(0.95 * (len(ds_) - 1))],
                             ds_[-1]))


if __name__ == "__main__":
    main()