"""Set B1 — final: correct midpoint-based refit + full report.

Research tool. The previous refinement step was defective and is replaced by a
midpoint-based least-squares refit, which is exact for a similarity and immune
to PDF fragmentation (a long DXF line split into collinear PAGE fragments
still has the correct midpoint).

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
from b1_final import seg_matches, entity_matches      # noqa: E402

U = 1000.0
UL = "mm"
TOL_PT = 1.5
DIR_TOL_DEG = 1.0


def entity_rows(Tt, entities, psegs, grid):
    rows = []
    for e in entities:
        m = entity_matches(Tt, e, psegs, grid)
        if m is None:
            continue
        rad = [dv * Tt[0] * U for (_mn, dv) in m]
        rows.append({"entity": e, "rad": rad,
                     "mean": sum(rad) / len(rad), "max": max(rad)})
    return rows


def midpoint_refit(Tt, entities, psegs, grid, rounds=6, keep=0.7):
    """Refit using DXF segment midpoints. Exact for a similarity."""
    cur = Tt
    for _ in range(rounds):
        rows = entity_rows(cur, entities, psegs, grid)
        if len(rows) < 12:
            break
        rows.sort(key=lambda r: r["mean"])
        good = rows[:max(12, int(keep * len(rows)))]
        pairs = []
        for r in good:
            for (a, b) in r["entity"].segs:
                if seg_matches(cur, a, b, psegs, grid) is None:
                    continue
                pm = M.inv_apply(cur, (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
                pairs.append((pm[0], pm[1],
                              (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0))
        f = M.fit_similarity(pairs, cur[4])
        if f is None:
            break
        shift = (abs(f[0] - cur[0]) / cur[0] + abs(f[1] - cur[1])
                 + abs(f[2] - cur[2]) + abs(f[3] - cur[3]))
        cur = (f[0], f[1], f[2], f[3], cur[4])
        if shift < 1e-12:
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
    print("DXF entities %d | CONTROL %d | CHECK %d | PAGE segments %d"
          % (len(dxf_ents), len(ctrl), len(chk), len(psegs)))

    csegs = [(a, b) for e in ctrl for a, b in e.segs]
    print()
    print("=== hypothesis search on CONTROL only ===")
    best = None
    for eps in (+1, -1):
        r = M.ransac(csegs, psegs, eps, tol=TOL_PT, n_dxf=34, n_pdf=70)
        if r["T"] is None:
            continue
        n = len(entity_rows(r["T"], ctrl, psegs, grid))
        print("  eps=%+d (%-12s) CONTROL matched = %d / %d (%.1f%%)"
              % (eps, "no reflection" if eps > 0 else "REFLECTION", n,
                 len(ctrl), 100.0 * n / len(ctrl)))
        if best is None or n > best[0]:
            best = (n, eps, r["T"])
    T0 = midpoint_refit(best[2], ctrl, psegs, grid)
    nc0 = len(entity_rows(T0, ctrl, psegs, grid))
    print("  after midpoint refit: CONTROL matched = %d / %d" % (nc0, len(ctrl)))
    s, th, tx, ty, eps = T0
    nc = nc0
    nk = len(entity_rows(T0, chk, psegs, grid))
    print()
    print("=== SELECTED TRANSFORM ===")
    print("  scale       s  = %.12f   CAD units per PAGE pt" % s)
    print("  rotation    th = %+.9f deg" % math.degrees(th))
    print("  translation cad_x = %.4f" % tx)
    print("                cad_y = %.4f" % ty)
    print("  reflection       = %s" % ("NO" if eps > 0 else "YES"))
    print("  CONTROL matched   %d / %d  (%.1f%%)" % (nc, len(ctrl),
                                                    100.0 * nc / len(ctrl)))
    print("  CHECK   matched   %d / %d  (%.1f%%)  [independent]"
          % (nk, len(chk), 100.0 * nk / len(chk)))
    print("  tolerance %.2f PAGE pt = %.1f mm CAD, direction %.1f deg"
          % (TOL_PT, TOL_PT * s * U, DIR_TOL_DEG))

    def cons(T, pool):
        return len(entity_rows(T, pool, psegs, grid))

    print()
    print("=== SELECTIVITY CALIBRATION (CONSENSUS = matched CONTROL entities) ===")
    out = [("transform (selected)", nc)]
    for dm in (0.1, 0.5, 1.0, 5.0, 100.0):
        out.append(("shift +%g m" % dm, cons((s, th, tx + dm, ty, eps), ctrl)))
    for dd in (0.05, 0.25, 1.0, 5.0, 90.0):
        out.append(("rot +%g deg" % dd,
                    cons((s, th + math.radians(dd), tx, ty, eps), ctrl)))
    for f in (0.99, 0.95, 1.01, 1.05):
        out.append(("scale x%g" % f, cons((s * f, th, tx, ty, eps), ctrl)))
    out.append(("axis swap cad_x<->cad_y",
                cons((s, th + math.pi / 2.0, -ty, tx, -eps), ctrl)))
    out.append(("reflection instead",
                cons((s, th, tx, ty, -eps), ctrl)))
    for k, v in out:
        print("   %-26s %5d / %d" % (k, v, len(ctrl)))
    rnd = random.Random(7)
    cs = []
    for _ in range(60):
        T = (s * math.exp(rnd.uniform(-0.8, 0.8)),
             th + rnd.uniform(-3.14159, 3.14159),
             tx + rnd.uniform(-250, 250), ty + rnd.uniform(-250, 250),
             rnd.choice((+1, -1)))
        cs.append(cons(T, ctrl))
    cs.sort()
    print("   %-26s median=%d max=%d ; >=10 in %d/60 random draws"
          % ("60 random wrong transforms", cs[len(cs) // 2], cs[-1],
             sum(1 for c in cs if c >= 10)))
    print("   MARGIN: selected=%d vs random max=%d  (%.0fx)"
          % (nc, cs[-1], nc / max(1, cs[-1])))

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
                print("      d_cad_x RMS=%.4f mm  d_cad_y RMS=%.4f mm"
                      % (st["rms"] * 0.71, st["rms"] * 0.71))
                for t_mm in (1, 5, 10, 20, 50, 100, 250, 500, 1000):
                    ok = sum(1 for v in rad if v <= t_mm)
                    print("      <= %6d mm : %6d / %6d (%5.1f%%)"
                          % (t_mm, ok, len(rad), 100.0 * ok / len(rad)))

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
    for t in sorted(tot, key=lambda k: -tot[k]):
        print("   %-12s %4d / %4d  (%5.1f%%)" % (t, mat[t], tot[t],
                                                   100.0 * mat[t] / tot[t]))
    lt = Counter(e.layer for e in dxf_ents)
    lm = Counter(e.layer for e in dxf_ents if id(e) in matched)
    rows = sorted(((100.0 * lm[k] / lt[k], k, lm[k], lt[k]) for k in lt))
    print("   layers with ZERO matches (%d of %d):"
          % (sum(1 for r in rows if r[0] == 0.0), len(rows)))
    for r, k, m_, t_ in rows[:12]:
        print("      %-34s %4d / %4d  %6.1f%%" % (k, m_, t_, r))
    print("   best matching layers:")
    for r, k, m_, t_ in rows[-6:]:
        print("      %-34s %4d / %4d  %6.1f%%" % (k, m_, t_, r))
    occ_all = cell_map(dxf_ents)
    occ_un = cell_map([e for e in dxf_ents if id(e) not in matched])
    print("   unmatched per 8x8 cell:")
    print("      " + " ".join("%d/%d" % (occ_un.get(k, 0), v)
                             for k, v in sorted(occ_all.items())))


if __name__ == "__main__":
    main()