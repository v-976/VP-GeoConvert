"""Focused diagnostics: circle survival, and the spatial structure of the
residual field (is it a smooth warp, a per-layer offset, or noise?).
Research tool.
"""

import math
from collections import defaultdict

import dxf_reader as D
import pdf_reader as P
import matcher as M
import benchmark_lib as BL
import final_report as FR

DXF = (r'benchmark\TKA_Tunnelitie'
       r'\TKA_Tunnelitie_Suunnitelmakartta_MAS!BG.dxf')
ART = r'research\results\archive\TKA_Tunnelitie'
TOL_PT = 1.5

# transforms recovered by final_report.py. Rotations must be in RADIANS.
D2R = math.pi / 180.0
T = {
    ("3D-Win", 0): (0.706092190373, -0.000012880 * D2R,
                     25484655.930663, 6677460.999906, +1),
    ("ProgeCAD Export", 0): (0.636409377503, -0.002072698 * D2R,
                             25484804.632703, 6677443.937214, +1),
    ("ProgeCAD Export", 1): (0.249398396891, -0.001575904 * D2R,
                             25484850.986127, 6677637.161463, +1),
    ("ProgeCAD Export", 2): (0.249398396891, -0.001575904 * D2R,
                             25484850.986127, 6677637.161463, +1),
    ("ProgeCAD Print", 0): (1.811675114899, 7.331385403 * D2R,
                            25484593.849277, 6676782.567801, +1),
}


def circle_survival(Tc, segs, grid, circles, tol_pt=6.0):
    """For each DXF circle, is there PAGE geometry forming it?

    Expected PAGE radius = r / s. Accept if many PAGE vertices lie on that
    radius about the transformed centre.
    """
    s = Tc[0]
    out = []
    for e in circles:
        pu, pv = M.inv_apply(Tc, e.center[0], e.center[1])
        r_page = e.radius / s
        cand = grid.near((pu, pv), max(tol_pt, r_page * 1.4))
        hits = 0
        best_off = None
        for i in cand:
            for pt in segs[i]:
                d = math.hypot(pt[0] - pu, pt[1] - pv)
                if abs(d - r_page) <= 0.15 * r_page:
                    hits += 1
        out.append((e.layer, e.radius, r_page, hits, len(cand)))
    return out


def residual_field(Tc, pts, segs, grid, tol=TOL_PT):
    res = []
    for q in pts:
        pu, pv = M.inv_apply(Tc, q[0], q[1])
        r = FR.nearest_point((pu, pv), segs, grid, tol)
        if r is None:
            continue
        Xr, Yr = M.apply_T(Tc, r[1][0], r[1][1])
        res.append((q[0], q[1], Xr - q[0], Yr - q[1],
                    math.hypot(Xr - q[0], Yr - q[1])))
    return res


def main():
    ents, hdr, blocks, _ = D.parse(DXF)
    circles = [e for e in ents if e.etype == "CIRCLE" and e.center]
    # DXF vertices tagged with layer
    by_layer = defaultdict(list)
    for e in ents:
        if e.etype not in ("LINE", "LWPOLYLINE"):
            continue
        p = e.pts
        pairs = [(p[i], p[i + 1]) for i in range(len(p) - 1)]
        if e.closed and p[0] != p[-1]:
            pairs.append((p[-1], p[0]))
        for a, b in pairs:
            by_layer[e.layer].append(a)
            by_layer[e.layer].append(b)

    for lab, fn in BL.FILES:
        paths, pages = P.parse(ART + "\\" + fn)
        bp = defaultdict(list)
        for p in paths:
            bp[p.page].append(p)
        for pg in sorted(bp):
            key = (lab, pg)
            if key not in T:
                continue
            Tc = T[key]
            sub = bp[pg]
            ps = BL.pdf_segments(sub)
            grid = M.Grid(ps)

            print()
            print("=" * 74)
            print("%s page %d" % (lab, pg))
            print("=" * 74)

            print("-- DXF CIRCLE survival --")
            cs = circle_survival(Tc, ps, grid, circles)
            good = [c for c in cs if c[3] >= 8]
            print("   circles with >=8 PAGE vertices on the expected radius: "
                  "%d / %d" % (len(good), len(circles)))
            if good:
                print("   radius (SURVEY m) -> expected PAGE radius (pt) -> hits")
                for lay, r, rp, h, n in sorted(good, key=lambda z: -z[3])[:6]:
                    print("     %-24s r=%.4f m  r_page=%.4f pt  hits=%d "
                          "(of %d nearby segments)" % (lay, r, rp, h, n))

            print("-- residual field structure --")
            allpts = []
            for lay, pts in by_layer.items():
                allpts.extend(pts)
            res = residual_field(Tc, allpts, ps, grid)
            if not res:
                print("   (none)")
                continue
            rad = sorted(r[4] for r in res)
            print("   n=%d  median=%.4f m  p95=%.4f m  max=%.4f m"
                  % (len(res), rad[len(rad) // 2],
                     rad[int(0.95 * (len(rad) - 1))], rad[-1]))
            # signed offsets: is there a constant bias?
            mx = sum(r[2] for r in res) / len(res)
            my = sum(r[3] for r in res) / len(res)
            print("   mean signed dX=%.4f m  dY=%.4f m  "
                  "(constant-bias hypothesis)" % (mx, my))
            # correlation of residual magnitude with position
            xs = [r[0] for r in res]
            ys = [r[1] for r in res]
            rs = [r[4] for r in res]
            print("   position spread  X %.1f m   Y %.1f m"
                  % (max(xs) - min(xs), max(ys) - min(ys)))
            # bucket by position to see if error grows with distance from centre
            cx = (max(xs) + min(xs)) / 2.0
            cy = (max(ys) + min(ys)) / 2.0
            buckets = defaultdict(list)
            for x, y, _a, _b, r in res:
                dd = math.hypot(x - cx, y - cy)
                buckets[int(dd // 20)].append(r)
            print("   residual vs distance from drawing centre (20 m bands):")
            for k in sorted(buckets):
                v = sorted(buckets[k])
                print("     %3d-%3d m : n=%4d median=%.4f m"
                      % (k * 20, k * 20 + 20, len(v), v[len(v) // 2]))

            # per-layer worst offenders
            lay_res = defaultdict(list)
            for (x, y, a, b, r) in res:
                pass
            # recompute per layer using stored points
            for lay, pts in by_layer.items():
                rr = residual_field(Tc, pts, ps, grid)
                if rr:
                    lay_res[lay] = sorted(r[4] for r in rr)
            print("   worst layers by median residual:")
            ranked = sorted(((sorted(v)[len(v) // 2], k, len(v))
                             for k, v in lay_res.items()), reverse=True)
            for med, k, n in ranked[:6]:
                print("     %-28s median=%.4f m  (n=%d)" % (k, med, n))


if __name__ == "__main__":
    main()