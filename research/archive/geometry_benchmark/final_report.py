"""Final consolidated geometry-benchmark report.

Adds: per-axis dX / dY residuals, DXF CIRCLE-centre verification, a correctly
signed affine diagnostic, and a float-precision propagation analysis.

PAGE   space: u = horizontal, v = vertical
SURVEY space: X = Northing,    Y = Easting
Strictly 2D.
"""

import math
import random
import struct
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


def nearest_point(p, segs, grid, tol):
    """Return (distance, nearest_point) of the closest PDF segment, or None."""
    cand = grid.near(p, tol)
    if not cand:
        return None
    bd = None
    bp = None
    for i in cand:
        qa, qb = segs[i]
        ddx = qb[0] - qa[0]
        ddy = qb[1] - qa[1]
        L2 = ddx * ddx + ddy * ddy
        t = 0.0 if L2 == 0 else max(0.0, min(
            1.0, ((p[0] - qa[0]) * ddx + (p[1] - qa[1]) * ddy) / L2))
        px = qa[0] + t * ddx
        py = qa[1] + t * ddy
        d = math.hypot(p[0] - px, p[1] - py)
        if bd is None or d < bd:
            bd = d
            bp = (px, py)
    return (bd, bp)


def collect(Tc, pts, segs, grid, tol):
    """Return [(dX, dY, radial)] in SURVEY metres for matched points."""
    out = []
    for q in pts:
        pu, pv = M.inv_apply(Tc, q[0], q[1])
        r = nearest_point((pu, pv), segs, grid, tol)
        if r is None or r[0] > tol:
            continue
        Xr, Yr = M.apply_T(Tc, r[1][0], r[1][1])
        dX = Xr - q[0]
        dY = Yr - q[1]
        out.append((dX, dY, math.hypot(dX, dY)))
    return out


def robust_refit(Tt, pts, segs, grid, tol, rounds=6, keep=0.6):
    cur = Tt
    work = list(pts)
    for _ in range(rounds):
        mt = []
        for q in work:
            pu, pv = M.inv_apply(cur, q[0], q[1])
            r = nearest_point((pu, pv), segs, grid, tol)
            if r is not None and r[0] <= tol:
                mt.append((r[0] * cur[0], r[1], q))
        if len(mt) < 12:
            break
        mt.sort(key=lambda z: z[0])
        good = mt[:max(12, int(keep * len(mt)))]
        pairs = [(bp[0], bp[1], q[0], q[1]) for _d, bp, q in good]
        f = M.fit_similarity(pairs, cur[4])
        if f is None:
            break
        cur = (f[0], f[1], f[2], f[3], cur[4])
    return cur


def rep(label, res):
    if not res:
        print("       %-28s (none)" % label)
        return None
    rad = [r[2] for r in res]
    st = BL.stats(rad)
    dX = [1000 * r[0] for r in res]
    dY = [1000 * r[1] for r in res]
    rmsx = math.sqrt(sum(x * x for x in dX) / len(dX))
    rmsy = math.sqrt(sum(y * y for y in dY) / len(dY))
    print("       %-28s n=%4d RMS=%9.4f mm  median=%9.4f  p95=%9.4f  "
          "max=%9.4f" % (label, st["n"], 1000 * st["rms"], 1000 * st["median"],
                         1000 * st["p95"], 1000 * st["max"]))
    print("       %-28s   dX rms=%9.4f mm   dY rms=%9.4f mm"
          % ("", rmsx, rmsy))
    return st


def float32_ulp(x):
    """Spacing of float32 at magnitude x."""
    if x == 0:
        return 1.4e-45
    e = math.floor(math.log2(abs(x)))
    if e < -126:
        return 1.4e-45
    return 2.0 ** (e - 23)


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
    circles = [e for e in ents if e.etype == "CIRCLE" and e.center]
    print("DXF: %d straight segments, %d distinct vertices, %d CIRCLE"
          % (len(dx), len(dxf_pts), len(circles)))

    summary = []
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
            sub = by_page[pg]
            ps = BL.pdf_segments(sub)
            grid = M.Grid(ps)
            print()
            print("  ===== page %d : %d PAGE segments =====" % (pg, len(ps)))

            best = None
            for eps in (+1, -1):
                r = M.ransac(dx, ps, eps, tol=TOL_PT)
                if r["T"] is None:
                    continue
                v = collect(r["T"], dxf_pts, ps, grid, TOL_PT)
                print("     eps=%+d (%-10s) vertex matches=%4d/%d"
                      % (eps, "no-reflect" if eps > 0 else "REFLECT-v",
                         len(v), len(dxf_pts)))
                if best is None or len(v) > best[0]:
                    best = (len(v), eps, r["T"])
            if best is None:
                print("     NO TRANSFORM")
                continue
            _n, eps, T0 = best
            Tref = robust_refit(T0, dxf_pts, ps, grid, TOL_PT)
            s, th, tx, ty = Tref[:4]
            print("     --------------------------------------------------")
            print("     eps=%+d  (%s)"
                  % (eps, "no reflection" if eps > 0 else "reflection of v"))
            print("       scale       s  = %.12f m/pt" % s)
            print("       rotation    th = %+.9f deg" % math.degrees(th))
            print("       translation tx = %.6f  ty = %.6f" % (tx, ty))
            allv = collect(Tref, dxf_pts, ps, grid, TOL_PT)
            print("       DXF vertices matched: %d / %d (%.1f%%)"
                  % (len(allv), len(dxf_pts), 100.0 * len(allv) / len(dxf_pts)))

            rnd = random.Random(SEED + pg + len(lab))
            order = list(range(len(dxf_pts)))
            rnd.shuffle(order)
            half = len(order) // 2
            ctrl_pts = [dxf_pts[i] for i in order[:half]]
            chk_pts = [dxf_pts[i] for i in order[half:]]

            Tc = robust_refit(Tref, ctrl_pts, ps, grid, TOL_PT, rounds=3)
            print("       CONTROL-refit s=%.12f  th=%+.9f deg"
                  % (Tc[0], math.degrees(Tc[1])))
            rc = collect(Tc, ctrl_pts, ps, grid, TOL_PT)
            rk = collect(Tc, chk_pts, ps, grid, TOL_PT)
            stc = rep("CONTROL (fitted)", rc)
            stk = rep("CHECK (independent)", rk)

            # --- independent circular feature check ---------------------
            cc = []
            for e in circles:
                pu, pv = M.inv_apply(Tc, e.center[0], e.center[1])
                r = nearest_point((pu, pv), ps, grid, TOL_PT * 6)
                if r is not None:
                    Xr, Yr = M.apply_T(Tc, r[1][0], r[1][1])
                    cc.append(math.hypot(Xr - e.center[0],
                                         Yr - e.center[1]))
            if cc:
                print("       DXF CIRCLE centres as separate feature class:")
                print("         matched %d / %d   radial offset median=%.4f m "
                      "max=%.4f m" % (len(cc), len(circles),
                                      sorted(cc)[len(cc) // 2], max(cc)))

            # --- affine diagnostic --------------------------------------
            cp = []
            for q in ctrl_pts:
                pu, pv = M.inv_apply(Tc, q[0], q[1])
                r = nearest_point((pu, pv), ps, grid, TOL_PT)
                if r:
                    cp.append((r[1][0], r[1][1], q[0], q[1]))
            A = BL.fit_affine(cp)
            if A and rk:
                detA = A[0] * A[3] - A[1] * A[2]
                sx = math.sqrt(abs(A[0] * A[3]))
                sy = math.sqrt(abs(A[1] * A[2]))
                ra = []
                for q in chk_pts:
                    pu, pv = M.inv_apply(Tc, q[0], q[1])
                    r = nearest_point((pu, pv), ps, grid, TOL_PT)
                    if r:
                        X, Y = BL.apply_affine(A, r[1][0], r[1][1])
                        ra.append(math.hypot(X - q[0], Y - q[1]) * Tc[0])
                sa = BL.stats(ra)
                print("       AFFINE diagnostic (fit on CONTROL):")
                print("         det=%+.9f  sx=%.9f  sy=%.9f  anisotropy=%.6f"
                      % (detA, sx, sy, (sx / sy) if sy else float('nan')))
                print("         AFFINE on CHECK : n=%4d RMS=%9.4f mm "
                      "max=%9.4f" % (sa["n"], 1000 * sa["rms"],
                                     1000 * sa["max"]))
                print("         affine vs Helmert on CHECK RMS: %+.2f%% "
                      "(- = affine better)"
                      % (100.0 * (sa["rms"] - stk["rms"]) / stk["rms"]))

            # --- float precision propagation ---------------------------
            umax = 0.0
            for p in sub:
                for _k, u, v, _c in p.segs:
                    umax = max(umax, abs(u), abs(v))
            # matrix magnitude from the dominant object matrix
            mm = max(math.hypot(p.matrix[0], p.matrix[1]) for p in sub)
            ulp_pt = float32_ulp(umax) * mm
            print("       FLOAT precision analysis:")
            print("         largest PAGE coord seen   = %.3f pt" % umax)
            print("         float32 ulp there         = %.3e" % float32_ulp(umax))
            print("         after object matrix       = %.3e pt" % ulp_pt)
            print("         -> SURVEY equivalent      = %.4f mm"
                  % (1000 * ulp_pt * s))
            summary.append((lab, pg, s, math.degrees(th), eps,
                            stc["rms"] if stc else None,
                            stk["rms"] if stk else None,
                            ulp_pt * s))


if __name__ == "__main__":
    main()