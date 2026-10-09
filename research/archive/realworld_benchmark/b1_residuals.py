"""Set B1 — signed axis residuals for the independent CHECK pool, plus an
estimate of unmatched PDF geometry. Research tool.

Delta cad_x / Delta cad_y are the signed offsets of the matched PAGE geometry
mapped back to CAD, relative to the DXF entity endpoint. Strictly 2D.
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
from b1_defence import spatial_split_balanced          # noqa: E402
from b1_final import seg_matches, entity_matches, TOL_PT, DIR_TOL_DEG  # noqa

U = 1000.0
UL = "mm"
N_SAMPLES = 11

# transform as reported by b1_report.py
S = 0.176403540756
TH = -169.749115222 * math.pi / 180.0
TX = 25485383.8694
TY = 6677815.4683
EPS = +1
T0 = (S, TH, TX, TY, EPS)


def signed_for_segment(a, b, psegs, grid):
    """Signed CAD offsets for one matched DXF segment."""
    if seg_matches(T0, a, b, psegs, grid) is None:
        return None
    pa = M.inv_apply(T0, a[0], a[1])
    pb = M.inv_apply(T0, b[0], b[1])
    dirp = math.atan2(pb[1] - pa[1], pb[0] - pa[0])
    out = []
    for q, p in ((a, pa), (b, pb)):
        cand = grid.near(p, TOL_PT)
        best = None
        for i in cand:
            qa, qb = psegs[i]
            ddx = qb[0] - qa[0]
            ddy = qb[1] - qa[1]
            if ddx == 0 and ddy == 0:
                continue
            d = math.atan2(ddy, ddx) - dirp
            d = (d + math.pi / 2.0) % math.pi - math.pi / 2.0
            if abs(d) > math.radians(DIR_TOL_DEG):
                continue
            L2 = ddx * ddx + ddy * ddy
            t = 0.0 if L2 == 0 else max(0.0, min(
                1.0, ((p[0] - qa[0]) * ddx + (p[1] - qa[1]) * ddy) / L2))
            px = qa[0] + t * ddx
            py = qa[1] + t * ddy
            dist = math.hypot(p[0] - px, p[1] - py)
            if dist > TOL_PT:
                continue
            if best is None or dist < best[0]:
                best = (dist, (px, py))
        if best is None:
            return None
        X, Y = M.apply_T(T0, best[1][0], best[1][1])
        out.append((X - q[0], Y - q[1]))
    return out


def rms(v):
    return math.sqrt(sum(x * x for x in v) / len(v)) if v else float('nan')


def main():
    ents, hdr = G.load(DXF)
    dxf_ents = [e for e in ents if e.segs]
    ctrl, chk = spatial_split_balanced(dxf_ents)
    paths, pages = P.parse(PROBE)
    psegs = []
    for p in paths:
        psegs.extend(P.segments_of(p))
    grid = M.Grid(psegs)

    for label, pool in (("CONTROL (fitted)", ctrl),
                        ("CHECK (independent)", chk)):
        dxv = []
        dyv = []
        ents_matched = 0
        feats = 0
        for e in pool:
            ok = True
            tmp_x = []
            tmp_y = []
            for (a, b) in e.segs:
                r = signed_for_segment(a, b, psegs, grid)
                if r is None:
                    ok = False
                    break
                for ddx, ddy in r:
                    tmp_x.append(ddx)
                    tmp_y.append(ddy)
            if ok:
                ents_matched += 1
                feats += len(e.segs)
                dxv.extend(tmp_x)
                dyv.extend(tmp_y)
        print("=== %s ===" % label)
        print("  entities matched : %d / %d" % (ents_matched, len(pool)))
        print("  features (seg)   : %d" % feats)
        print("  d_cad_x RMS  = %8.4f mm" % (rms(dxv) * U))
        print("  d_cad_y RMS  = %8.4f mm" % (rms(dyv) * U))
        print("  d_cad_x bias = %8.4f mm" % ((sum(dxv) / len(dxv)) * U))
        print("  d_cad_y bias = %8.4f mm" % ((sum(dyv) / len(dyv)) * U))
        print("  d_cad_x p95  = %8.4f mm"
              % (sorted(abs(v) for v in dxv)[int(0.95 * (len(dxv) - 1))] * U))
        print("  d_cad_y p95  = %8.4f mm"
              % (sorted(abs(v) for v in dyv)[int(0.95 * (len(dyv) - 1))] * U))
        print()

    # ---- unmatched PDF geometry estimate -------------------------------
    print("=== unmatched PDF geometry estimate ===")
    print("  method: sample PAGE segments, ask whether each is within the")
    print("  matching tolerance of the matched DXF geometry set.")
    allsegs = [(a, b) for e in dxf_ents for a, b in e.segs]
    dgrid = M.Grid(allsegs, cell=20.0)
    rnd = random.Random(11)
    sample = rnd.sample(psegs, 4000)
    near = 0
    dnear = 0
    for (a, b) in sample:
        r = _near(a, allsegs, dgrid)
        if r is not None and r <= TOL_PT:
            near += 1
    print("  sampled PAGE segments          : %d" % len(sample))
    print("  within %.2f pt of ANY DXF geometry: %d (%.1f%%)"
          % (TOL_PT, near, 100.0 * near / len(sample)))
    print("  -> this is an UPPER bound on how much PDF geometry is explained")
    print("     by the DXF; the remainder is unexplained, not proven to be")
    print("     revision difference.")


def _near(p, segs, grid, r=1.5):
    cand = grid.near(p, r)
    best = None
    for i in cand:
        qa, qb = segs[i]
        d = M.pseg_dist(p, qa, qb)
        if best is None or d < best:
            best = d
    return best


if __name__ == "__main__":
    main()