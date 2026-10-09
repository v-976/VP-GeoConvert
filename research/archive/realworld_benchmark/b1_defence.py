"""Set B1 — false-match defence, spatial analysis and revision analysis.

Research tool. Consumes the transform produced by b1_analysis.py (hardcoded
here only as a re-entry convenience; it is recomputed and re-verified below).

Coordinate domains:
    PAGE    u, v
    CAD     cad_x, cad_y
    SURVEY  X, Y   -- not used, mapping not proven
Strictly 2D.
"""

import math
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "geometry_benchmark"))

import dxf_geom as G                                   # noqa: E402
import pdf_reader as P                                 # noqa: E402
import matcher as M                                    # noqa: E402
from b1_analysis import (spatial_split, nearest_point, entity_match,      # noqa
                         entity_residuals, stats, robust_refit,
                         dxf_ents_of, DXF, PROBE, TOL_PT)

U = 1000.0     # CAD units (metres) -> mm
UL = "mm"


def spatial_split_balanced(entities, n_cells=8):
    """Balanced AND spatially spread: sort by cell, then alternate."""
    cells = []
    xs = [p[0] for e in entities for a, b in e.segs for p in (a, b)]
    ys = [p[1] for e in entities for a, b in e.segs for p in (a, b)]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    dx = max(1e-9, (x1 - x0) / n_cells)
    dy = max(1e-9, (y1 - y0) / n_cells)
    for e in entities:
        cx = sum(p[0] for a, b in e.segs for p in (a, b)) / (2 * len(e.segs))
        cy = sum(p[1] for a, b in e.segs for p in (a, b)) / (2 * len(e.segs))
        gx = min(n_cells - 1, int((cx - x0) / dx))
        gy = min(n_cells - 1, int((cy - y0) / dy))
        cells.append((gy, gx, e.idx, e))
    cells.sort()
    ctrl = [c[3] for i, c in enumerate(cells) if i % 2 == 0]
    chk = [c[3] for i, c in enumerate(cells) if i % 2 == 1]
    return ctrl, chk


def spread(entities):
    """Bounding box and cell occupancy of an entity set, in CAD units."""
    xs = [p[0] for e in entities for a, b in e.segs for p in (a, b)]
    ys = [p[1] for e in entities for a, b in e.segs for p in (a, b)]
    if not xs:
        return None
    return (min(xs), max(xs), min(ys), max(ys))


def cell_map(entities, n_cells=8):
    xs = [p[0] for e in entities for a, b in e.segs for p in (a, b)]
    ys = [p[1] for e in entities for a, b in e.segs for p in (a, b)]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    dx = max(1e-9, (x1 - x0) / n_cells)
    dy = max(1e-9, (y1 - y0) / n_cells)
    occ = Counter()
    for e in entities:
        cx = sum(p[0] for a, b in e.segs for p in (a, b)) / (2 * len(e.segs))
        cy = sum(p[1] for a, b in e.segs for p in (a, b)) / (2 * len(e.segs))
        gx = min(n_cells - 1, int((cx - x0) / dx))
        gy = min(n_cells - 1, int((cy - y0) / dy))
        occ[(gy, gx)] += 1
    return occ


def main():
    ents, hdr = G.load(DXF)
    dxf_ents = [e for e in ents if e.segs]
    ctrl_pool, chk_pool = spatial_split_balanced(dxf_ents)
    print("=== balanced spatial entity-level split ===")
    print("  DXF entities with straight geometry : %d" % len(dxf_ents))
    print("  CONTROL pool : %d entities" % len(ctrl_pool))
    print("  CHECK   pool : %d entities" % len(chk_pool))

    paths, pages = P.parse(PROBE)
    psegs = []
    for p in paths:
        psegs.extend(P.segments_of(p))
    grid = M.Grid(psegs)

    # ---- recompute transform on CONTROL only ----------------------------
    csegs = [(a, b) for e in ctrl_pool for a, b in e.segs]
    print()
    print("=== hypothesis search on CONTROL only ===")
    cands = []
    for eps in (+1, -1):
        r = M.ransac(csegs, psegs, eps, tol=TOL_PT, n_dxf=34, n_pdf=70)
        if r["T"] is None:
            continue
        rows = entity_residuals(r["T"], ctrl_pool, psegs, grid, TOL_PT, U)
        cands.append((len(rows), eps, r))
        print("  eps=%+d  CONTROL matched entities = %d / %d"
              % (eps, len(rows), len(ctrl_pool)))
    cands.sort(key=lambda z: -z[0])
    bestn, besteps, bestR = cands[0]
    Tc = robust_refit(bestR["T"], ctrl_pool, psegs, grid, TOL_PT, U)
    print()
    print("  SELECTED: eps=%+d  s=%.12f  th=%+.9f deg  t=(%.4f, %.4f)"
          % (besteps, Tc[0], math.degrees(Tc[1]), Tc[2], Tc[3]))

    # ================= PHASE 6: FALSE MATCH DEFENCE =====================
    print()
    print("=" * 74)
    print("PHASE 6 — FALSE-MATCH DEFENCE")
    print("=" * 74)

    def consensus(T, pool):
        return len(entity_residuals(T, pool, psegs, grid, TOL_PT, U))

    print()
    print("[1] competing hypotheses ranked by CONTROL entity consensus")
    ranked = []
    for eps in (+1, -1):
        r = M.ransac(csegs, psegs, eps, tol=TOL_PT, n_dxf=34, n_pdf=70)
        if r["T"] is None:
            continue
        ranked.append((consensus(r["T"], ctrl_pool), eps, r["T"]))
    ranked.sort(key=lambda z: -z[0])
    for i, (n, eps, T) in enumerate(ranked[:6]):
        print("    #%d eps=%+d  entities=%4d/%d  s=%.9f  th=%+10.5f deg"
              % (i + 1, eps, n, len(ctrl_pool), T[0], math.degrees(T[1])))
    if len(ranked) > 1:
        print("    margin between #1 and #2 : %d entities (%.1fx)"
              % (ranked[0][0] - ranked[1][0],
                 ranked[0][0] / max(1, ranked[1][0])))

    print()
    print("[2] explicit axis-swap hypothesis (cad_x<->cad_y)")
    # axis swap of the target equals reflection + 90 deg rotation
    sw = (Tc[0], Tc[1] + math.pi / 2.0, -Tc[3], Tc[2], -Tc[4])
    print("    CONTROL consensus with axis swap applied : %d / %d"
          % (consensus(sw, ctrl_pool), len(ctrl_pool)))
    print("    CONTROL consensus of selected transform  : %d / %d"
          % (consensus(Tc, ctrl_pool), len(ctrl_pool)))

    print()
    print("[3] deliberate perturbation neighbourhood (CONTROL consensus)")
    for label, dth, ds in (("rotation -1 deg", -1.0, 0.0),
                           ("rotation +1 deg", +1.0, 0.0),
                           ("rotation +90 deg", +90.0, 0.0),
                           ("scale x0.99", 0.0, -0.01),
                           ("scale x1.01", 0.0, +0.01),
                           ("scale x1.10", 0.0, +0.10),
                           ("scale x0.90", 0.0, -0.10)):
        T = (Tc[0] * (1.0 + ds), Tc[1] + math.radians(dth),
             Tc[2], Tc[3], Tc[4])
        print("    %-18s -> %d / %d entities"
              % (label, consensus(T, ctrl_pool), len(ctrl_pool)))

    print()
    print("[4] spatial distribution of matched entities (8x8 grid over CAD)")
    matched_ctrl = [r["entity"] for r in
                    entity_residuals(Tc, ctrl_pool, psegs, grid, TOL_PT, U)]
    matched_chk = [r["entity"] for r in
                   entity_residuals(Tc, chk_pool, psegs, grid, TOL_PT, U)]
    for label, pool, mat in (("CONTROL", ctrl_pool, matched_ctrl),
                             ("CHECK  ", chk_pool, matched_chk)):
        occ = cell_map(pool)
        mo = cell_map(mat) if mat else Counter()
        filled = sum(1 for k in occ if mo.get(k, 0) > 0)
        print("    %s: %d/%d occupied cells have >=1 match; %d/%d entities matched"
              % (label, filled, len(occ), len(mat), len(pool)))
        print("      %s" % " ".join(
            "%d" % min(mo.get(k, 0), 99) for k in sorted(occ)))

    print()
    print("[5] CHECK pool consensus under the selected transform (never fitted)")
    print("    CHECK entities matched: %d / %d" % (len(matched_chk),
                                                  len(chk_pool)))

    print()
    print("[6] second geometry class: DXF CIRCLE radius fingerprint")
    circles = [e for e in ents if e.etype == "CIRCLE" and e.radius
               and e.center]
    if circles:
        rads = Counter(round(e.radius, 6) for e in circles)
        print("    DXF circles: %d   radii histogram: %s"
              % (len(circles), dict(list(rads.items())[:6])))
        # expected PAGE radius for a DXF circle
        s = Tc[0]
        for r_dxf, n in list(rads.items())[:4]:
            r_page = r_dxf / s
            pu, pv = M.inv_apply(Tc, circles[0].center[0], circles[0].center[1])
            cand = grid.near((pu, pv), max(8.0, r_page * 2.0))
            hits = 0
            tested = 0
            for e in circles[:60]:
                pu, pv = M.inv_apply(Tc, e.center[0], e.center[1])
                rp = e.radius / s
                cand = grid.near((pu, pv), max(4.0, rp * 2.0))
                tested += 1
                for i in cand:
                    for pt in psegs[i]:
                        if abs(math.hypot(pt[0] - pu, pt[1] - pv) - rp) <= 0.2 * rp:
                            hits += 1
                            break
            print("      DXF r=%.4f m -> expected PAGE r=%.4f pt : %d/%d "
                  "circles have PAGE vertices on that radius"
                  % (r_dxf, r_page, hits, tested))

    # ================= PHASE 7: REVISION ANALYSIS =======================
    print()
    print("=" * 74)
    print("PHASE 7 — REVISION / DIFFERENCE ANALYSIS")
    print("=" * 74)

    matched_all = {}
    for e in matched_ctrl + matched_chk:
        matched_all[id(e)] = e

    print()
    print("[A] match rate by DXF entity type")
    tot = Counter()
    mat = Counter()
    for e in dxf_ents:
        tot[e.etype] += 1
        if id(e) in matched_all:
            mat[e.etype] += 1
    for t in sorted(tot, key=lambda k: -tot[k]):
        print("    %-12s matched %4d / %4d  (%5.1f%%)"
              % (t, mat[t], tot[t], 100.0 * mat[t] / tot[t]))

    print()
    print("[B] match rate by DXF layer (layers referenced by matched vs all)")
    lt = Counter(e.layer for e in dxf_ents)
    lm = Counter(e.layer for e in matched_all.values())
    print("    %-36s %8s %8s %7s" % ("layer", "matched", "total", "rate"))
    for k in sorted(lt, key=lambda k: -(lt[k])):
        r = 100.0 * lm[k] / lt[k]
        flag = "  <-- fully unmatched" if r == 0 else (
               "  <-- fully matched" if r >= 99.5 else "")
        print("    %-36s %8d %8d %6.1f%%%s" % (k, lm[k], lt[k], r, flag))

    print()
    print("[C] unmatched DXF entities: are they isolated or clustered?")
    unmatched = [e for e in dxf_ents if id(e) not in matched_all]
    occ_all = cell_map(dxf_ents)
    occ_un = cell_map(unmatched)
    print("    unmatched entities: %d / %d" % (len(unmatched), len(dxf_ents)))
    print("    per-cell unmatched / total (8x8):")
    for k in sorted(occ_all):
        print("      cell(%d,%2d): %4d / %4d" % (k[1], k[0], occ_un.get(k, 0),
                                                occ_all[k]))

    print()
    print("[D] unmatched PDF geometry estimate")
    # sample PAGE segments and ask how many lie within tol of ANY matched DXF entity
    print("    (approximate: sampled PAGE segments vs the DXF geometry set)")


if __name__ == "__main__":
    main()
