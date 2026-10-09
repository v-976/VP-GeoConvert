"""Set B1 — false-positive calibration of the matching criterion.

Research tool. Before any transform may be called a real match, the matching
test itself must be shown to be selective: a deliberately wrong transform must
produce (near) zero consensus.

Adds:
  * shift / rotation / scale sweeps around the selected transform
  * fully random transforms
  * per-circle verification with a proper 360-degree coverage test
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
from b1_analysis import (entity_residuals, robust_refit, DXF, PROBE,
                         TOL_PT)                       # noqa: E402
from b1_defence import spatial_split_balanced          # noqa: E402

U = 1000.0
UL = "mm"


def consensus(T, pool, psegs, grid):
    return len(entity_residuals(T, pool, psegs, grid, TOL_PT, U))


def main():
    ents, hdr = G.load(DXF)
    dxf_ents = [e for e in ents if e.segs]
    ctrl, chk = spatial_split_balanced(dxf_ents)

    paths, pages = P.parse(PROBE)
    psegs = []
    for p in paths:
        psegs.extend(P.segments_of(p))
    grid = M.Grid(psegs)

    csegs = [(a, b) for e in ctrl for a, b in e.segs]
    best = None
    for eps in (+1, -1):
        r = M.ransac(csegs, psegs, eps, tol=TOL_PT, n_dxf=34, n_pdf=70)
        if r["T"] is None:
            continue
        n = consensus(r["T"], ctrl, psegs, grid)
        if best is None or n > best[0]:
            best = (n, eps, r["T"])
    T0 = robust_refit(best[2], ctrl, psegs, grid, TOL_PT, U)
    s, th, tx, ty, eps = T0

    print("=== selected transform ===")
    print("  s=%.12f CAD/pt   th=%+.9f deg   t=(%.4f, %.4f)   eps=%+d"
          % (s, math.degrees(th), tx, ty, eps))
    print("  CONTROL consensus      : %d / %d entities"
          % (consensus(T0, ctrl, psegs, grid), len(ctrl)))
    print("  CHECK   consensus      : %d / %d entities (independent)"
          % (consensus(T0, chk, psegs, grid), len(chk)))
    print()

    print("=" * 72)
    print("FALSE-POSITIVE CALIBRATION")
    print("=" * 72)
    print("Matching tolerance is %.2f PAGE pt = %.1f mm in CAD units"
          % (TOL_PT, TOL_PT * s * U))
    print()
    print("-- pure translation offset (no other change) --")
    for dm in (0.0, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 20.0, 100.0):
        T = (s, th, tx + dm, ty, eps)
        print("   shift +%-8.2f m -> CONTROL %4d / %d"
              % (dm, consensus(T, ctrl, psegs, grid), len(ctrl)))

    print()
    print("-- pure rotation offset --")
    for dd in (0.0, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 45.0, 90.0):
        T = (s, th + math.radians(dd), tx, ty, eps)
        print("   rot  +%-8.2f deg -> CONTROL %4d / %d"
              % (dd, consensus(T, ctrl, psegs, grid), len(ctrl)))

    print()
    print("-- pure scale factor --")
    for f in (1.0, 0.999, 0.995, 0.99, 0.95, 0.9, 1.01, 1.05, 1.1, 1.5):
        T = (s * f, th, tx, ty, eps)
        print("   scale x%-6.3f -> CONTROL %4d / %d"
              % (f, consensus(T, ctrl, psegs, grid), len(ctrl)))

    print()
    print("-- random wrong transforms (100 draws) --")
    rnd = random.Random(4242)
    counts = []
    for _ in range(100):
        T = (s * math.exp(rnd.uniform(-1.2, 1.2)),
             th + rnd.uniform(-3.14, 3.14),
             tx + rnd.uniform(-400, 400),
             ty + rnd.uniform(-400, 400),
             rnd.choice((+1, -1)))
        counts.append(consensus(T, ctrl, psegs, grid))
    counts.sort()
    print("   random consensus: min=%d median=%d max=%d  (of %d CONTROL)"
          % (counts[0], counts[len(counts) // 2], counts[-1], len(ctrl)))
    print("   transforms with consensus >= 10 : %d / 100"
          % sum(1 for c in counts if c >= 10))
    print("   transforms with consensus >= 50 : %d / 100"
          % sum(1 for c in counts if c >= 50))
    print()
    print("   -> selectivity: a correct transform must sit far above this band.")

    # ---- proper circle verification ------------------------------------
    print()
    print("=" * 72)
    print("INDEPENDENT GEOMETRY CLASS: DXF CIRCLE (radius fingerprint)")
    print("=" * 72)
    circles = [e for e in ents if e.etype == "CIRCLE" and e.radius
               and e.center]
    print("DXF circles: %d   radii: %s"
          % (len(circles),
             dict(Counter(round(e.radius, 4) for e in circles).most_common(5))))

    # build a fast PAGE point lookup of the expected radius for one circle
    def circle_ok(e):
        pu, pv = M.inv_apply(T0, e.center[0], e.center[1])
        rp = e.radius / s
        cand = grid.near((pu, pv), rp * 1.6 + 2.0)
        if not cand:
            return None
        angs = []
        for i in cand:
            qa, qb = psegs[i]
            for pt in (qa, qb):
                d = math.hypot(pt[0] - pu, pt[1] - pv)
                if abs(d - rp) <= 0.12 * rp:
                    angs.append(math.atan2(pt[1] - pv, pt[0] - pu))
        if len(angs) < 6:
            return None
        angs.sort()
        maxgap = 0.0
        for i in range(len(angs)):
            a = angs[i]
            b = angs[(i + 1) % len(angs)]
            gap = (b - a) % (2 * math.pi)
            if gap > maxgap:
                maxgap = gap
        return (len(angs), math.degrees(maxgap))

    ok = 0
    full = 0
    tested = 0
    details = []
    for e in circles:
        r = circle_ok(e)
        tested += 1
        if r:
            ok += 1
            if r[1] < 150.0:
                full += 1
            details.append((e.layer, e.radius, r[0], r[1]))
    print("circles with >=6 PAGE vertices on the expected radius : %d / %d"
          % (ok, tested))
    print("  of those, angular coverage complete (max gap <150 deg): %d"
          % full)
    if details:
        details.sort(key=lambda z: z[3])
        print("  best-covered circles (layer, r_m, PAGE vertices, max gap deg):")
        for d in details[:6]:
            print("     %-26s r=%.4f  n=%3d  maxgap=%.1f" % d)


if __name__ == "__main__":
    main()