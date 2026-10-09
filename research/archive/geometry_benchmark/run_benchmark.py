"""Final geometry benchmark with robust refitting and a clean residual metric.

Residual definition
-------------------
A DXF vertex (SURVEY) is mapped into PAGE space, then the perpendicular/
perpendicular-plus-along distance to the nearest PDF segment is measured.
Because a PDF generator may split one DXF straight edge into several collinear
fragments, the residual is the distance from the mapped vertex to the nearest
point of the nearest PDF SEGMENT. Distance is then converted back to SURVEY
metres by multiplying by the scale.

This is a fair measure for collinear fragmenting and is reported per vertex.

PAGE   space: u = horizontal, v = vertical
SURVEY space: X = Northing,    Y = Easting
Strictly 2D. No Z anywhere.
"""

import math
import random
from collections import defaultdict

import dxf_reader as D
import pdf_reader as P
import matcher as M
import benchmark_lib as BL

DXF = (r'benchmark\TKA_Tunnelitie'
       r'\TKA_Tunnelitie_Suunnitelmakartta_MAS!BG.dxf')
ART = r'research\results\archive\TKA_Tunnelitie'
TOL_PT = 1.5
SEED = 20261007


def nearest_pdf_dist(p, segs, grid, max_r=6.0):
    """Min distance from PAGE point p to any PDF segment. None if too far."""
    cand = grid.near(p, max_r)
    if not cand:
        return None
    best = None
    for i in cand:
        qa, qb = segs[i]
        d = M.pseg_dist(p, qa, qb)
        if best is None or d < best:
            best = d
    return best


def match_vertices(Tt, dxf_pts, segs, grid, tol):
    """Return [(dxf_point, dist_pt)] for vertices within tol of PDF geometry."""
    out = []
    s = Tt[0]
    for q in dxf_pts:
        pu, pv = M.inv_apply(Tt, q[0], q[1])
        d = nearest_pdf_dist((pu, pv), segs, grid)
        if d is None or d > tol:
            continue
        out.append((q, d * s))          # residual already in SURVEY metres
    return out


def robust_refit(Tt, dxf_pts, segs, grid, tol, rounds=6, keep=0.6):
    """Iteratively reject outliers and refit the similarity."""
    cur = Tt
    pts = list(dxf_pts)
    for _ in range(rounds):
        mt = match_vertices(cur, pts, segs, grid, tol)
        if len(mt) < 10:
            break
        mt.sort(key=lambda z: z[1])
        nkeep = max(10, int(keep * len(mt)))
        good = mt[:nkeep]
        pairs = []
        for q, _d in good:
            pu, pv = M.inv_apply(cur, q[0], q[1])
            # nearest PDF point as the correspondence target
            # recover it from the grid again
            cand = grid.near((pu, pv), tol)
            bestp = None
            bestd = None
            for i in cand:
                qa, qb = segs[i]
                # project
                dx = qb[0] - qa[0]
                dy = qb[1] - qa[1]
                L2 = dx * dx + dy * dy
                t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((pu - qa[0]) * dx
                                                          + (pv - qa[1]) * dy) / L2))
                px = qa[0] + t * dx
                py = qa[1] + t * dy
                dd = math.hypot(pu - px, pv - py)
                if bestd is None or dd < bestd:
                    bestd = dd
                    bestp = (px, py)
            if bestp is not None:
                pairs.append((bestp[0], bestp[1], q[0], q[1]))
        f = M.fit_similarity(pairs, cur[4])
        if f is None:
            break
        newT = (f[0], f[1], f[2], f[3], cur[4])
        shift = (abs(f[0] - cur[0]) / cur[0] + abs(f[1] - cur[1])
                 + abs(f[2] - cur[2]) + abs(f[3] - cur[3]))
        cur = newT
        if shift < 1e-10:
            break
    return cur


def report_stats(label, resid):
    st = BL.stats(resid)
    if st is None:
        print("       %-26s (no samples)" % label)
        return None
    print("       %-26s n=%4d  RMS=%9.4f mm  median=%9.4f  p95=%9.4f  "
          "max=%9.4f" % (label, st["n"], 1000 * st["rms"],
                         1000 * st["median"], 1000 * st["p95"],
                         1000 * st["max"]))
    return st


def main():
    ents, hdr, blocks, _ = D.parse(DXF)
    dx = BL.dxf_segments(ents)
    dxf_pts = []
    seen = set()
    for a, b in dx:
        for q in (a, b):
            k = (round(q[0], 6), round(q[1], 6))
            if k not in seen:
                seen.add(k)
                dxf_pts.append(q)
    print("DXF straight segments: %d   distinct vertices: %d"
          % (len(dx), len(dxf_pts)))
    print("DXF CIRCLE=%d  ARC=%d  LWPOLYLINE=%d  LINE=%d  INSERT=%d  HATCH=%d"
          % tuple(sum(1 for e in ents if e.etype == t)
                  for t in ("CIRCLE", "ARC", "LWPOLYLINE", "LINE",
                            "INSERT", "HATCH")))

    for lab, fn in BL.FILES:
        paths, pages = P.parse(ART + "\\" + fn)
        by_page = defaultdict(list)
        for p in paths:
            by_page[p.page].append(p)
        print()
        print("#" * 78)
        print("# %s" % lab)
        print("#" * 78)
        for pg in sorted(by_page):
            ps = BL.pdf_segments(by_page[pg])
            grid = M.Grid(ps)
            print()
            print("  ===== page %d (%d PAGE segments) =====" % (pg, len(ps)))

            best = None
            for eps in (+1, -1):
                r = M.ransac(dx, ps, eps, tol=TOL_PT)
                if r["T"] is None:
                    print("     eps=%+d : no hypothesis" % eps)
                    continue
                v = match_vertices(r["T"], dxf_pts, ps, grid, TOL_PT)
                print("     eps=%+d (%-10s) tried=%-8d fitted=%-7d "
                      "vertex-matches=%4d/%d"
                      % (eps, "no-reflect" if eps > 0 else "REFLECT-v",
                         r["tried"], r["fitted"], len(v), len(dxf_pts)))
                if best is None or len(v) > best[0]:
                    best = (len(v), eps, r["T"])
            if best is None:
                print("     NO TRANSFORM RECOVERED")
                continue
            _n, eps, T0 = best
            Tref = robust_refit(T0, dxf_pts, ps, grid, TOL_PT)
            s, th, tx, ty = Tref[0], Tref[1], Tref[2], Tref[3]
            print("     --------------------------------------------------")
            print("     SELECTED eps=%+d (%s)"
                  % (eps, "no reflection" if eps > 0 else "reflection of v"))
            print("       scale       s  = %.12f   (SURVEY m per PAGE pt; "
                  "1 px = %.6f m)" % (s, s))
            print("       rotation    th = %+.9f deg" % math.degrees(th))
            print("       translation tx = %.6f" % tx)
            print("                   ty = %.6f" % ty)

            mt = match_vertices(Tref, dxf_pts, ps, grid, TOL_PT)
            print("       vertices matched to PDF geometry: %d / %d (%.1f%%)"
                  % (len(mt), len(dxf_pts), 100.0 * len(mt) / len(dxf_pts)))

            # split matched vertices into CONTROL / CHECK (disjoint features)
            rnd = random.Random(SEED + pg)
            idx = list(range(len(mt)))
            rnd.shuffle(idx)
            half = len(idx) // 2
            ctrl_idx = set(idx[:half])
            ctrl_pts = [mt[i] for i in ctrl_idx]
            chk_pts = [mt[i] for i in range(len(mt)) if i not in ctrl_idx]

            # refit on CONTROL only, with outlier rejection
            Tc = robust_refit(Tref, [q for q, _d in ctrl_pts], ps, grid,
                              TOL_PT, rounds=3)
            print("       CONTROL refit s=%.12f  th=%+.9f deg"
                  % (Tc[0], math.degrees(Tc[1])))

            rc = match_vertices(Tc, [q for q, _d in ctrl_pts], ps, grid, TOL_PT)
            rk = match_vertices(Tc, [q for q, _d in chk_pts], ps, grid, TOL_PT)
            stc = report_stats("CONTROL (fitted)",
                               [d for _q, d in rc])
            stk = report_stats("CHECK (independent)",
                               [d for _q, d in rk])
            if rk:
                print("       CHECK dX/dY split is not separable per vertex; "
                      "radial residual reported above")
            print("       independent CHECK features: %d" % len(rk))

            # affine diagnostic, fitted on CONTROL, evaluated on CHECK
            cp = []
            for q, _d in ctrl_pts:
                pu, pv = M.inv_apply(Tc, q[0], q[1])
                cand = grid.near((pu, pv), TOL_PT)
                bestp = None
                bestd = None
                for i in cand:
                    qa, qb = ps[i]
                    dxq = qb[0] - qa[0]
                    dyq = qb[1] - qa[1]
                    L2 = dxq * dxq + dyq * dyq
                    t = 0.0 if L2 == 0 else max(0.0, min(
                        1.0, ((pu - qa[0]) * dxq + (pv - qa[1]) * dyq) / L2))
                    px = qa[0] + t * dxq
                    py = qa[1] + t * dyq
                    dd = math.hypot(pu - px, pv - py)
                    if bestd is None or dd < bestd:
                        bestd = dd
                        bestp = (px, py)
                if bestp:
                    cp.append((bestp[0], bestp[1], q[0], q[1]))
            A = BL.fit_affine(cp)
            if A and stk:
                det = A[0] * A[3] - A[1] * A[2]
                sx = math.sqrt(abs(A[0] * A[3]))
                sy = math.sqrt(abs(A[1] * A[2]))
                ra = []
                for q, _d in chk_pts:
                    pu, pv = M.inv_apply(Tc, q[0], q[1])
                    cand = grid.near((pu, pv), TOL_PT)
                    bestp = None
                    bestd = None
                    for i in cand:
                        qa, qb = ps[i]
                        dxq = qb[0] - qa[0]
                        dyq = qb[1] - qa[1]
                        L2 = dxq * dxq + dyq * dyq
                        t = 0.0 if L2 == 0 else max(0.0, min(
                            1.0, ((pu - qa[0]) * dxq + (pv - qa[1]) * dyq) / L2))
                        px = qa[0] + t * dxq
                        py = qa[1] + t * dyq
                        dd = math.hypot(pu - px, pv - py)
                        if bestd is None or dd < bestd:
                            bestd = dd
                            bestp = (px, py)
                    if bestp:
                        X, Y = BL.apply_affine(A, bestp[0], bestp[1])
                        ra.append(math.hypot(X - q[0], Y - q[1]) * Tc[0])
                sa = BL.stats(ra)
                if sa:
                    print("       AFFINE diagnostic: det=%+.9f sx=%.9f "
                          "sy=%.9f anisotropy=%.6f"
                          % (det, sx, sy, (sx / sy) if sy else float('nan')))
                    print("       AFFINE on CHECK : n=%4d RMS=%9.4f mm  "
                          "max=%9.4f"
                          % (sa["n"], 1000 * sa["rms"], 1000 * sa["max"]))
                    print("       affine vs Helmert CHECK RMS: %+.1f%% "
                          "(- means affine better)"
                          % (100.0 * (sa["rms"] - stk["rms"]) / stk["rms"]))


if __name__ == "__main__":
    main()