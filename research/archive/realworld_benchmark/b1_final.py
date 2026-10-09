"""Set B1 — final correspondence criterion, selectivity calibration,
residuals, revision analysis. Research tool.

Correspondence criterion (fragmentation tolerant, still selective)
-----------------------------------------------------------------
A DXF segment matches when EVERY sample point along its transformed image
lies within tol of a PAGE segment whose direction agrees within dir_tol.

This accepts the normal PDF-generation behaviour where one long DXF line
becomes several collinear PAGE fragments, while still rejecting coincidental
proximity: proximity ALONE is not sufficient, the direction must agree too.

Strictly 2D. PAGE (u,v) <-> DXF CAD (cad_x,cad_y). SURVEY not used.
"""

import math
import os
import random
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "geometry_benchmark"))

import dxf_geom as G                                   # noqa: E402
import pdf_reader as P                                 # noqa: E402
import matcher as M                                    # noqa: E402
from b1_analysis import DXF, PROBE                    # noqa: E402
from b1_defence import spatial_split_balanced, cell_map  # noqa: E402

U = 1000.0
UL = "mm"
TOL_PT = 1.5
DIR_TOL_DEG = 1.0
N_SAMPLES = 11


def seg_matches(Tt, a, b, psegs, grid, tol=TOL_PT,
                dir_tol_deg=DIR_TOL_DEG, n=N_SAMPLES):
    """Return mean/max sample deviation in PAGE pt, or None."""
    s = Tt[0]
    La = math.hypot(b[0] - a[0], b[1] - a[1])
    if La <= 0:
        return None
    pa = M.inv_apply(Tt, a[0], a[1])
    pb = M.inv_apply(Tt, b[0], b[1])
    dir_dxf = math.atan2(pb[1] - pa[1], pb[0] - pa[0])
    d_tol = math.radians(dir_tol_deg)

    devs = []
    for k in range(n):
        t = k / (n - 1.0)
        px = pa[0] + t * (pb[0] - pa[0])
        py = pa[1] + t * (pb[1] - pa[1])
        cand = grid.near((px, py), tol)
        if not cand:
            return None
        best = None
        for i in cand:
            qa, qb = psegs[i]
            ddx = qb[0] - qa[0]
            ddy = qb[1] - qa[1]
            if ddx == 0 and ddy == 0:
                continue
            dp = math.atan2(ddy, ddx)
            d = dir_dxf - dp
            d = (d + math.pi / 2.0) % math.pi - math.pi / 2.0
            if abs(d) > d_tol:
                continue
            dist = M.pseg_dist((px, py), qa, qb)
            if dist > tol:
                continue
            if best is None or dist < best:
                best = dist
        if best is None:
            return None
        devs.append(best)
    return (sum(devs) / len(devs), max(devs))


def entity_matches(Tt, e, psegs, grid):
    devs = []
    for a, b in e.segs:
        r = seg_matches(Tt, a, b, psegs, grid)
        if r is None:
            return None
        devs.append(r)
    return devs


def entity_rows(Tt, entities, psegs, grid):
    rows = []
    for e in entities:
        m = entity_matches(Tt, e, psegs, grid)
        if m is None:
            continue
        rad = [dev * Tt[0] * U for (mean, dev) in m for dev in (dev_max_of(m),)][:len(m)] \
            if False else [dv * Tt[0] * U for (_mn, dv) in m]
        rows.append({"entity": e, "n": len(e.segs), "rad": rad,
                     "mean": sum(rad) / len(rad), "max": max(rad)})
    return rows


def dev_max_of(m):
    return max(m)[1]


def refit(Tt, entities, psegs, grid, rounds=5, keep=0.6):
    cur = Tt
    for _ in range(rounds):
        rows = entity_rows(cur, entities, psegs, grid)
        if len(rows) < 10:
            return cur
        rows.sort(key=lambda r: r["mean"])
        good = rows[:max(10, int(keep * len(rows)))]
        pairs = []
        for r in good:
            for (a, b) in r["entity"].segs:
                pa = M.inv_apply(cur, a[0], a[1])
                pb = M.inv_apply(cur, b[0], b[1])
                # nearest collinear PAGE point for each endpoint
                for q, seg_dir in ((a, (pa, pb)), (b, (pb, pa))):
                    dd = math.atan2(seg_dir[1][1] - seg_dir[0][1],
                                    seg_dir[1][0] - seg_dir[0][0])
                    best = None
                    for i in grid.near((pa, pb)[0 if q is a else 1], TOL_PT):
                        qa, qb = psegs[i]
                        ddx = qb[0] - qa[0]
                        ddy = qb[1] - qa[1]
                        if ddx == 0 and ddy == 0:
                            continue
                        d = math.atan2(ddy, ddx) - dd
                        d = (d + math.pi / 2.0) % math.pi - math.pi / 2.0
                        if abs(d) > math.radians(DIR_TOL_DEG):
                            continue
                        p = (qa, qb)[0] if False else None
                        L2 = ddx * ddx + ddy * ddy
                        tpar = 0.0 if L2 == 0 else max(
                            0.0, min(1.0, ((q[0] - qa[0]) * ddx
                                           + (q[1] - qa[1]) * ddy) / L2))
                        px = qa[0] + tpar * ddx
                        py = qa[1] + tpar * ddy
                        dist = math.hypot(q[0] - px, q[1] - py)
                        if best is None or dist < best:
                            best = dist
                            bp = (px, py)
                    if best is not None and bp is not None:
                        pairs.append((bp[0], bp[1], q[0], q[1]))
        f = M.fit_similarity(pairs, cur[4])
        if f is None:
            break
        cur = (f[0], f[1], f[2], f[3], cur[4])
    return cur


def stats(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return {"n": n, "rms": math.sqrt(sum(x * x for x in s) / n),
            "median": s[n // 2],
            "p95": s[min(n - 1, int(round(0.95 * (n - 1))))],
            "max": s[-1]}


def main():
    ents, hdr = G.load(DXF)
    dxf_ents = [e for e in ents if e.segs]
    ctrl, chk = spatial_split_balanced(dxf_ents)
    paths, pages = P.parse(PROBE)
    psegs = []
    for p in paths:
        psegs.extend(P.segments_of(p))
    grid = M.Grid(psegs)
    print("DXF entities %d | CONTROL %d | CHECK %d | PAGE segments %d"
          % (len(dxf_ents), len(ctrl), len(chk), len(psegs)))
    print()

    csegs = [(a, b) for e in ctrl for a, b in e.segs]
    best = None
    for eps in (+1, -1):
        r = M.ransac(csegs, psegs, eps, tol=TOL_PT, n_dxf=34, n_pdf=70)
        if r["T"] is None:
            continue
        n = len(entity_rows(r["T"], ctrl, psegs, grid))
        print("eps=%+d (%-12s) CONTROL matched = %d / %d"
              % (eps, "no reflection" if eps > 0 else "REFLECTION", n,
                 len(ctrl)))
        if best is None or n > best[0]:
            best = (n, eps, r["T"])
    T0 = refit(best[2], ctrl, psegs, grid)
    s, th, tx, ty, eps = T0
    nc = len(entity_rows(T0, ctrl, psegs, grid))
    nk = len(entity_rows(T0, chk, psegs, grid))
    print()
    print("=== SELECTED TRANSFORM ===")
    print("  scale    s  = %.12f   CAD units per PAGE pt" % s)
    print("  rotation th = %+.9f deg" % math.degrees(th))
    print("  transl. cad_x = %.4f   cad_y = %.4f" % (tx, ty))
    print("  reflection    = %s" % ("NO" if eps > 0 else "YES (v mirrored)"))
    print("  CONTROL matched %d / %d  (%.1f%%)" % (nc, len(ctrl),
                                                   100.0 * nc / len(ctrl)))
    print("  CHECK   matched %d / %d  (%.1f%%)" % (nk, len(chk),
                                                   100.0 * nk / len(chk)))
    print("  tolerance: %.2f PAGE pt = %.1f mm CAD ; direction tol %.1f deg"
          % (TOL_PT, TOL_PT * s * U, DIR_TOL_DEG))

    def cons(T, pool):
        return len(entity_rows(T, pool, psegs, grid))

    print()
    print("=== SELECTIVITY CALIBRATION ===")
    print("  translation offset (m) :", end="")
    for dm in (0.1, 0.5, 1.0, 5.0, 100.0):
        print("  +%g->%d" % (dm, cons((s, th, tx + dm, ty, eps), ctrl)), end="")
    print()
    print("  rotation offset (deg)  :", end="")
    for dd in (0.05, 0.25, 1.0, 5.0, 90.0):
        print("  +%g->%d" % (dd, cons((s, th + math.radians(dd), tx, ty, eps),
                                       ctrl)), end="")
    print()
    print("  scale factor           :", end="")
    for f in (0.99, 0.95, 1.01, 1.05):
        print("  x%g->%d" % (f, cons((s * f, th, tx, ty, eps), ctrl)), end="")
    print()
    print("  axis swap              : %d / %d"
          % (cons((s, th + math.pi / 2.0, -ty, tx, -eps), ctrl), len(ctrl)))
    rnd = random.Random(7)
    cs = []
    for _ in range(60):
        T = (s * math.exp(rnd.uniform(-0.8, 0.8)),
             th + rnd.uniform(-3.14159, 3.14159),
             tx + rnd.uniform(-250, 250), ty + rnd.uniform(-250, 250),
             rnd.choice((+1, -1)))
        cs.append(cons(T, ctrl))
    cs.sort()
    print("  60 random wrong T      : median=%d max=%d ; >=10: %d/60"
          % (cs[len(cs) // 2], cs[-1], sum(1 for c in cs if c >= 10)))
    print("  SELECTIVITY margin     : correct=%d vs random max=%d"
          % (nc, cs[-1]))

    print()
    print("=== RESIDUALS (entity level, %s) ===" % UL)
    for label, pool in (("CONTROL (fitted)", ctrl), ("CHECK (independent)", chk)):
        rows = entity_rows(T0, pool, psegs, grid)
        rad = [v for r in rows for v in r["rad"]]
        st = stats(rad)
        print("  %-20s entities %4d / %4d ; unmatched %4d"
              % (label, len(rows), len(pool), len(pool) - len(rows)))
        if st:
            print("      features %d  RMS=%.4f  median=%.4f  p95=%.4f  max=%.4f"
                  % (st["n"], st["rms"], st["median"], st["p95"], st["max"]))
            if label.startswith("CHECK"):
                for t_mm in (1, 5, 10, 20, 50, 100, 250, 500, 1000):
                    ok = sum(1 for v in rad if v <= t_mm)
                    print("      <= %6d mm : %6d / %6d (%5.1f%%)"
                          % (t_mm, ok, len(rad), 100.0 * ok / len(rad)))

    # ---- revision analysis --------------------------------------------
    mrows = entity_rows(T0, dxf_ents, psegs, grid)
    matched = {id(r["entity"]) for r in mrows}
    print()
    print("=== DIFFERENCE ANALYSIS (all %d DXF entities) ===" % len(dxf_ents))
    tot = Counter()
    mat = Counter()
    for e in dxf_ents:
        tot[e.etype] += 1
        if id(e) in matched:
            mat[e.etype] += 1
    print("  by entity type:")
    for t in sorted(tot, key=lambda k: -tot[k]):
        print("     %-12s %4d / %4d  (%5.1f%%)" % (t, mat[t], tot[t],
                                                   100.0 * mat[t] / tot[t]))
    lt = Counter(e.layer for e in dxf_ents)
    lm = Counter(e.layer for e in dxf_ents if id(e) in matched)
    print("  by layer (worst 10 and best 5):")
    rows = sorted(((100.0 * lm[k] / lt[k], k, lm[k], lt[k]) for k in lt))
    for r, k, m_, t_ in rows[:10]:
        print("     %-36s %4d / %4d  %6.1f%%" % (k, m_, t_, r))
    print("     ...")
    for r, k, m_, t_ in rows[-5:]:
        print("     %-36s %4d / %4d  %6.1f%%" % (k, m_, t_, r))
    occ_all = cell_map(dxf_ents)
    occ_un = cell_map([e for e in dxf_ents if id(e) not in matched])
    print("  unmatched entities per 8x8 cell (unmatched/total):")
    print("     " + " ".join("%d/%d" % (occ_un.get(k, 0), v)
                             for k, v in sorted(occ_all.items())))


if __name__ == "__main__":
    main()