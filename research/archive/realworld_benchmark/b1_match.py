"""Set B1: blind PAGE -> DXF CAD geometry matching.

Research tool. No manual control points, no known coordinates, no filename
evidence, no title-block use.

Coordinate domains kept strictly separate:
    PAGE space        u, v     (after PDF object transforms)
    DXF CAD space     cad_x, cad_y
    SURVEY X,Y        NOT USED - the CAD -> SURVEY mapping is not proven here

Primary transform: 2D similarity / Helmert (translation, uniform scale,
rotation). Reflection and axis swap are tested as EXPLICIT separate
hypotheses, never folded in silently. Affine is diagnostic only, computed
after Helmert.

Run:  python b1_match.py
"""

import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "geometry_benchmark"))

import dxf_geom as G                                   # noqa: E402
import pdf_reader as P                                 # noqa: E402
import matcher as M                                    # noqa: E402

B1 = r'benchmark\Set_B1_Baana'
DXF = os.path.join(B1, 'TKA_Baana_Suunnitelmakartta!BG.dxf')
PROBE = r'research\results\archive\b1\b1_probe.txt'

TOL_PT = 1.5


# --------------------------------------------------------------- utilities

def pseg_dist(p, a, b):
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2
    if t < 0:
        t = 0.0
    elif t > 1:
        t = 1.0
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def nearest_point(p, segs, grid, tol):
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


def load_pdf_page():
    paths, pages = P.parse(PROBE)
    by_page = defaultdict(list)
    for p in paths:
        by_page[p.page].append(p)
    return by_page, pages


def dx_segments(entities):
    return [(a, b, e) for e in entities for a, b in e.segs]


def norm_stats(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return {"n": n,
            "rms": math.sqrt(sum(x * x for x in s) / n),
            "median": s[n // 2],
            "p95": s[min(n - 1, int(round(0.95 * (n - 1))))],
            "max": s[-1]}


def main():
    ents, hdr = G.load(DXF)
    ins = hdr.get("$INSUNITS")
    unit_code = int(float(ins[0][1])) if ins else None
    UNIT = {1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m", 14: "dm"}.get(
        unit_code, "UNKNOWN")
    U = 1000.0 if unit_code == 6 else 1.0     # convert CAD -> mm when metres
    UL = "mm" if unit_code == 6 else "CAD units"

    by_page, pages = load_pdf_page()
    print("=== Set B1 inputs ===")
    print("DXF entities: %d   $INSUNITS=%s -> %s (residual unit label: %s)"
          % (len(ents), unit_code, UNIT, UL))
    for pg in sorted(by_page):
        segs = []
        for p in by_page[pg]:
            segs.extend(P.segments_of(p))
        print("PDF page %d: %d paths -> %d straight PAGE segments"
              % (pg, len(by_page[pg]), len(segs)))

    dxsegs = dx_segments([e for e in ents if e.segs])
    dseg = [(a, b) for a, b, _e in dxsegs]
    print("DXF straight segments: %d" % len(dseg))

    best = None
    for pg in sorted(by_page):
        psegs = []
        for p in by_page[pg]:
            psegs.extend(P.segments_of(p))
        grid = M.Grid(psegs)
        print()
        print("#" * 74)
        print("# PDF page %d  (%d PAGE segments)" % (pg, len(psegs)))
        print("#" * 74)

        results = []
        for eps in (+1, -1):
            r = M.ransac(dseg, psegs, eps, tol=TOL_PT, n_dxf=30, n_pdf=60)
            if r["T"] is None:
                print("  eps=%+d : no hypothesis" % eps)
                continue
            s, th, tx, ty = r["T"][0], r["T"][1], r["T"][2], r["T"][3]
            Tt = r["T"]
            # count entity-level matches
            nm = count_entity_matches(Tt, [e for e in ents if e.segs],
                                      psegs, grid, TOL_PT, ents)
            results.append((nm, eps, r))
            lbl = "no reflection" if eps > 0 else "REFLECTION of v"
            print("  eps=%+d (%-16s) tried=%-9d fitted=%-7d probe=%2d  "
                  "matched entities=%d/%d"
                  % (eps, lbl, r["tried"], r["fitted"], r["hits"], nm[0],
                     nm[1]))
        results.sort(key=lambda z: -z[0][0])
        if results:
            best = (pg, results[0][1], results[0][2]["T"], results[0][0],
                    psegs, grid)
    return best, ents, dseg, U, UL, unit_code, by_page


def count_entity_matches(Tt, dxf_ents, psegs, grid, tol, all_ents):
    """An entity counts as MATCHED when every one of its segments matches."""
    matched = 0
    total = 0
    for e in dxf_ents:
        total += 1
        ok = 0
        for a, b in e.segs:
            if M.match_one(Tt, a, b, grid, tol) is not None:
                ok += 1
        if e.segs and ok == len(e.segs):
            matched += 1
    return matched, total


if __name__ == "__main__":
    main()