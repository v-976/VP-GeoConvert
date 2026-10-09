"""Set B1 — strict segment correspondence + selectivity calibration.

Research tool.

Strict criterion for a DXF segment <-> PAGE segment correspondence:
  * both transformed endpoints within tol of the PAGE segment
  * PAGE segment length agrees within rel_len
  * PAGE segment direction agrees within dir_tol
  * the endpoints must lie in the SAME PAGE segment (not two different ones)

The last condition is what removes coincidental near-misses: a DXF vertex that
happens to fall near some unrelated PDF line no longer produces a match.
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
from b1_analysis import robust_refit, DXF, PROBE       # noqa: E402
from b1_defence import spatial_split_balanced          # noqa: E402

U = 1000.0
UL = "mm"
TOL_PT = 1.5
REL_LEN = 0.02
DIR_TOL_DEG = 1.0


def strict_segment(Tt, a, b, psegs, grid, tol=TOL_PT, rel_len=REL_LEN,
                  dir_tol_deg=DIR_TOL_DEG):
    """Return (pdf_seg, mean_endpoint_dist_pt) or None."""
    s = Tt[0]
    La = math.hypot(b[0] - a[0], b[1] - a[1])
    if La <= 0:
        return None
    pa = M.inv_apply(Tt, a[0], a[1])
    pb = M.inv_apply(Tt, b[0], b[1])
    Lp_expect = La / s
    cand = grid.near(pa, tol) | grid.near(pb, tol)
    if not cand:
        return None
    dir_dxf = math.atan2(b[1] - a[1], b[0] - a[0])
    d_tol = math.radians(dir_tol_deg)
    best = None
    for i in cand:
        qa, qb = psegs[i]
        Lp = math.hypot(qb[0] - qa[0], qb[1] - qa[1])
        if abs(Lp - Lp_expect) > max(tol, rel_len * Lp_expect):
            continue
        if Lp <= 0:
            continue
        dir_pdf = math.atan2(qb[1] - qa[1], qb[0] - qa[0])
        d = dir_dxf - dir_pdf
        d = (d + math.pi / 2.0) % math.pi - math.pi / 2.0
        if abs(d) > d_tol:
            continue
        da = M.pseg_dist(pa, qa, qb)
        db = M.pseg_dist(pb, qa, qb)
        # endpoints must be served by the SAME segment and near its own span
        if da > tol or db > tol:
            continue
        mean = (da + db) / 2.0
        if best is None or mean < best[0]:
            best = (mean, (qa, qb))
    if best is None:
        return None
    return (best[1], best[0])


def strict_entity(Tt, e, psegs, grid):
    """Entity matches only when EVERY segment matches strictly."""
    out = []
    for a, b in e.segs:
        r = strict_segment(Tt, a, b, psegs, grid)
        if r is None:
            return None
        out.append((r[0], r[1]))
    return out


def entity_rows(Tt, entities, psegs, grid):
    rows = []
    for e in entities:
        m = strict_entity(Tt, e, psegs, grid)
        if m is None:
            continue
        rad = [d * Tt[0] * U for (_q, d) in m]
        rows.append({"entity": e, "n": len(e.segs), "rad": rad,
                     "mean": sum(rad) / len(rad), "max": max(rad)})
    return rows


def strict_refit(Tt, entities, psegs, grid, rounds=5, keep=0.6):
    cur = Tt
    for _ in range(rounds):
        rows = entity_rows(cur, entities, psegs, grid)
        if len(rows) < 10:
            return cur
        rows.sort(key=lambda r: r["mean"])
        good = rows[:max(10, int(keep * len(rows)))]
        pairs = []
        for r in good:
            for (qa, qb), _d in strict_entity(cur, r["entity"], psegs, grid):
                pairs.append((qa[0], qa[1], r["entity"].center[0] or 0, 0))
                break
            # rebuild correspondence from the DXF segment itself
        pairs = []
        for r in good:
            for (a, b) in r["entity"].segs:
                m = strict_segment(cur, a, b, psegs, grid)
                if m is None:
                    continue
                qa, qb = m[0]
                # orient the PDF segment to match the DXF segment direction
                if math.hypot(qa[0] - a[0], qa[1] - a[1]) + math.hypot(qb[0] - b[0],
                                                                     qb[1] - b[1]) \
                        > math.hypot(qb[0] - a[0], qb[1] - a[1]) + math.hypot(qa[0] - b[0],
                                                                             qa[1] - b[1]):
                    qa, qb = qb, qa
                pairs.append((qa[0], qa[1], a[0], a[1]))
                pairs.append((qb[0], qb[1], b[0], b[1]))
        f = M.fit_similarity(pairs, cur[4])
        if f is None:
            break
        newT = (f[0], f[1], f[2], f[3], cur[4])
        shift = (abs(f[0] - cur[0]) / cur[0] + abs(f[1] - cur[1])
                 + abs(f[2] - cur[2]) + abs(f[3] - cur[3]))
        cur = newT
        if shift < 1e-11:
            break
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
    print("CONTROL %d entities | CHECK %d entities | PAGE segments %d"
          % (len(ctrl), len(chk), len(psegs)))
    print()

    csegs = [(a, b) for e in ctrl for a, b in e.segs]
    best = None
    for eps in (+1, -1):
        r = M.ransac(csegs, psegs, eps, tol=TOL_PT, n_dxf=34, n_pdf=70)
        if r["T"] is None:
            continue
        n = len(entity_rows(r["T"], ctrl, psegs, grid))
        lbl = "no reflection" if eps > 0 else "REFLECTION"
        print("eps=%+d (%-12s) STRICT CONTROL matched = %d / %d"
              % (eps, lbl, n, len(ctrl)))
        if best is None or n > best[0]:
            best = (n, eps, r["T"])
    T0 = strict_refit(best[2], ctrl, psegs, grid)
    s, th, tx, ty, eps = T0
    nc = len(entity_rows(T0, ctrl, psegs, grid))
    nk = len(entity_rows(T0, chk, psegs, grid))
    print()
    print("=== STRICT selected transform ===")
    print("  s  = %.12f CAD/pt" % s)
    print("  th = %+.9f deg" % math.degrees(th))
    print("  t  = (%.4f, %.4f)" % (tx, ty))
    print("  eps = %+d (%s)" % (eps, "no reflection" if eps > 0 else "reflection"))
    print("  CONTROL matched entities : %d / %d (%.1f%%)"
          % (nc, len(ctrl), 100.0 * nc / len(ctrl)))
    print("  CHECK   matched entities : %d / %d (%.1f%%)"
          % (nk, len(chk), 100.0 * nk / len(chk)))

    print()
    print("=" * 72)
    print("STRICT SELECTIVITY CALIBRATION")
    print("=" * 72)

    def cons(T, pool):
        return len(entity_rows(T, pool, psegs, grid))

    print("-- translation offset --")
    for dm in (0.0, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 20.0, 100.0, 500.0):
        print("   +%-8.2f m -> %4d / %d" % (dm, cons((s, th, tx + dm, ty, eps),
                                                    ctrl), len(ctrl)))
    print("-- rotation offset --")
    for dd in (0.0, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0, 45.0, 90.0):
        print("   +%-8.2f deg -> %4d / %d"
              % (dd, cons((s, th + math.radians(dd), tx, ty, eps), ctrl),
                 len(ctrl)))
    print("-- scale --")
    for f in (1.0, 0.999, 0.99, 0.98, 0.95, 0.9, 1.01, 1.02, 1.05, 1.5):
        print("   x%-6.3f -> %4d / %d" % (f, cons((s * f, th, tx, ty, eps),
                                                 ctrl), len(ctrl)))
    print("-- axis swap --")
    print("   swap cad_x<->cad_y -> %4d / %d"
          % (cons((s, th + math.pi / 2.0, -ty, tx, -eps), ctrl), len(ctrl)))
    print("-- 100 random wrong transforms --")
    rnd = random.Random(99)
    cs = []
    for _ in range(100):
        T = (s * math.exp(rnd.uniform(-1.0, 1.0)),
             th + rnd.uniform(-3.14159, 3.14159),
             tx + rnd.uniform(-300, 300), ty + rnd.uniform(-300, 300),
             rnd.choice((+1, -1)))
        cs.append(cons(T, ctrl))
    cs.sort()
    print("   min=%d median=%d p90=%d max=%d" % (cs[0], cs[50], cs[90], cs[-1]))
    print("   with consensus >= 10 : %d / 100" % sum(1 for c in cs if c >= 10))
    print("   with consensus >= 50 : %d / 100" % sum(1 for c in cs if c >= 50))
    print("   with consensus >= 200: %d / 100" % sum(1 for c in cs if c >= 200))
    print()
    print("   SELECTIVITY: correct transform = %d ; random floor max = %d"
          % (nc, cs[-1]))

    # ---- residuals ----------------------------------------------------
    print()
    print("=== residual statistics, entity level (%s) ===" % UL)
    for label, pool in (("CONTROL (fitted)", ctrl), ("CHECK (independent)", chk)):
        rows = entity_rows(T0, pool, psegs, grid)
        rad = [v for r in rows for v in r["rad"]]
        st = stats(rad)
        print("  %-20s entities matched %4d / %4d" % (label, len(rows),
                                                      len(pool)))
        if st:
            print("      features %d  RMS=%.4f  median=%.4f  p95=%.4f  max=%.4f"
                  % (st["n"], st["rms"], st["median"], st["p95"], st["max"]))
        if label.startswith("CHECK"):
            dxv = []
            dyv = []
            for r in rows:
                for (a, b) in r["entity"].segs:
                    m = strict_segment(T0, a, b, psegs, grid)
                    if m is None:
                        continue
                    qa, qb = m[0]
                    if math.hypot(qa[0] - a[0], qa[1] - a[1]) > math.hypot(
                            qb[0] - a[0], qb[1] - a[1]):
                        qa, qb = qb, qa
                    for (X, Y), (qx, qy) in (((a[0], a[1]), (qa[0], qa[1])),
                                              ((b[0], b[1]), (qb[0], qb[1]))):
                        px, py = M.apply_T(T0, qx, qy)
                        dxv.append(px - X)
                        dyv.append(py - Y)
            if dxv:
                print("      d_cad_x RMS=%.4f mm   d_cad_y RMS=%.4f mm"
                      % (1000 * math.sqrt(sum(v * v for v in dxv) / len(dxv)),
                         1000 * math.sqrt(sum(v * v for v in dyv) / len(dyv))))
            for t_mm in (1, 5, 10, 20, 50, 100, 250, 500, 1000):
                ok = sum(1 for v in rad if v <= t_mm)
                print("      <= %6d mm : %6d / %6d (%5.1f%%)"
                      % (t_mm, ok, len(rad), 100.0 * ok / len(rad)))


if __name__ == "__main__":
    main()