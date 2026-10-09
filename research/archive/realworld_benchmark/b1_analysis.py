"""Set B1 full analysis: entity-level CONTROL/CHECK, CAD-unit residuals,
threshold pass rates, false-match defence and revision analysis.

Research tool.

PROTOCOL (deliberately strict)
-------------------------------
1. DXF entities are split into CONTROL and CHECK POOLS BEFORE any hypothesis
   is generated. The split is spatial, so both pools are distributed over the
   whole sheet rather than one local area.
2. CHECK entities are never used for hypothesis generation, hypothesis
   ranking, transform fit, transform refinement, reflection selection or
   orientation selection.
3. A whole entity belongs to exactly one pool. Entities are never split.
4. Reflection and axis-swap are decided by CONTROL consensus only.

Coordinate domains:
    PAGE      u, v
    DXF CAD   cad_x, cad_y
    SURVEY    X, Y  -- NOT USED, mapping not proven
Strictly 2D.
"""

import math
import os
import random
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
SEED = 20261007


# ------------------------------------------------------------------ helpers

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


def spatial_split(entities, n_cells=6):
    """Deterministic spatial split so both pools span the whole sheet."""
    pts = []
    for e in entities:
        xs = [p[0] for a, b in e.segs for p in (a, b)]
        ys = [p[1] for a, b in e.segs for p in (a, b)]
        if xs:
            pts.append(((min(xs) + max(xs)) / 2.0,
                        (min(ys) + max(ys)) / 2.0, e))
    if not pts:
        return [], []
    x0 = min(p[0] for p in pts)
    x1 = max(p[0] for p in pts)
    y0 = min(p[1] for p in pts)
    y1 = max(p[1] for p in pts)
    dx = max(1e-9, (x1 - x0) / n_cells)
    dy = max(1e-9, (y1 - y0) / n_cells)
    ctrl = []
    chk = []
    for cx, cy, e in pts:
        gx = int((cx - x0) / dx)
        gy = int((cy - y0) / dy)
        # checkerboard: both pools spread over the whole sheet
        if (gx + gy) % 2 == 0:
            ctrl.append(e)
        else:
            chk.append(e)
    return ctrl, chk


def entity_match(Tt, e, psegs, grid, tol):
    """Return list of (dxf_point, nearest_pdf_point, dist_pt) or None.

    The tolerance IS enforced here. nearest_point() only returns the nearest
    candidate found in nearby grid cells; it does not filter by distance, so the
    r[0] <= tol test below is what actually decides a match. Omitting it makes
    the criterion non-selective: a 100 m translation error still matched hundreds
    of entities.
    """
    out = []
    for a, b in e.segs:
        for q in (a, b):
            pu, pv = M.inv_apply(Tt, q[0], q[1])
            r = nearest_point((pu, pv), psegs, grid, tol)
            if r is None or r[0] > tol:
                return None
            out.append((q, r[1], r[0]))
    return out


def entity_residuals(Tt, entities, psegs, grid, tol, scale_to_unit):
    """Per-entity radial residuals.

    |d| comes back from entity_match() in PAGE points. To reach the residual
    unit it must be multiplied by the scale s (CAD units per PAGE point) and
    then by scale_to_unit (1000 when the file declares metres, giving mm).
    """
    rows = []
    s_per_pt = Tt[0]
    for e in entities:
        res = entity_match(Tt, e, psegs, grid, tol)
        if res is None:
            continue
        rad = [d * s_per_pt * scale_to_unit for (_q, _p, d) in res]
        rows.append({"entity": e, "n": len(e.segs),
                     "rad": rad,
                     "mean": sum(rad) / len(rad),
                     "max": max(rad)})
    return rows


def stats(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return {"n": n, "rms": math.sqrt(sum(x * x for x in s) / n),
            "median": s[n // 2],
            "p95": s[min(n - 1, int(round(0.95 * (n - 1))))],
            "max": s[-1]}


def robust_refit(Tt, entities, psegs, grid, tol, scale_to_unit,
                 rounds=5, keep=0.6):
    cur = Tt
    for _ in range(rounds):
        rows = entity_residuals(cur, entities, psegs, grid, tol,
                                scale_to_unit)
        if len(rows) < 10:
            return cur
        rows.sort(key=lambda r: r["mean"])
        good = rows[:max(10, int(keep * len(rows)))]
        pairs = []
        for r in good:
            m = entity_match(cur, r["entity"], psegs, grid, tol)
            for q, p, _d in m:
                pairs.append((p[0], p[1], q[0], q[1]))
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


# --------------------------------------------------------------------- main

def main():
    ents, hdr = G.load(DXF)
    ins = hdr.get("$INSUNITS")
    unit_code = int(float(ins[0][1])) if ins else None
    UNIT_NAME = {1: "inches", 2: "feet", 4: "mm", 5: "cm", 6: "metres",
                 14: "decimetres"}.get(unit_code, None)
    # residual unit: millimetres when the file declares metres
    U = 1000.0 if unit_code == 6 else 1.0
    UL = "mm" if unit_code == 6 else "CAD units"

    dxf_ents = [e for e in ents if e.segs]
    ctrl_pool, chk_pool = spatial_split(dxf_ents)
    print("=== protocol setup ===")
    print("DXF entities with straight geometry : %d" % len(dxf_ents))
    print("CONTROL pool (spatial, pre-registered): %d entities" % len(ctrl_pool))
    print("CHECK   pool (spatial, held out)      : %d entities" % len(chk_pool))
    print("DXF units declared by file           : %s ($INSUNITS=%s)"
          % (UNIT_NAME, unit_code))
    print("residuals reported in                : %s" % UL)

    paths, pages = P.parse(PROBE)
    psegs = []
    for p in paths:
        psegs.extend(P.segments_of(p))
    grid = M.Grid(psegs)
    print("PDF PAGE straight segments           : %d" % len(psegs))
    print()

    # ---- hypothesis generation using CONTROL ONLY ----------------------
    csegs = [(a, b) for e in ctrl_pool for a, b in e.segs]
    print("=== hypothesis search (CONTROL entities only, %d segments) ==="
          % len(csegs))
    hyps = []
    for eps in (+1, -1):
        r = M.ransac(csegs, psegs, eps, tol=TOL_PT, n_dxf=34, n_pdf=70)
        if r["T"] is None:
            print("  eps=%+d : no hypothesis" % eps)
            continue
        Tt = r["T"]
        rows = entity_residuals(Tt, ctrl_pool, psegs, grid, TOL_PT, U)
        lbl = "no reflection" if eps > 0 else "REFLECTION of v"
        print("  eps=%+d (%-16s) tried=%-9d fitted=%-6d probe=%2d  "
              "CONTROL matched entities=%d/%d"
              % (eps, lbl, r["tried"], r["fitted"], r["hits"], len(rows),
                 len(ctrl_pool)))
        hyps.append((len(rows), eps, Tt, r))
    if not hyps:
        print("NO HYPOTHESIS -> benchmark failed at matching stage")
        return
    hyps.sort(key=lambda z: -z[0])
    bestn, besteps, bestT, bestR = hyps[0]
    runner = hyps[1] if len(hyps) > 1 else None

    print()
    print("=== selected hypothesis (decided on CONTROL only) ===")
    print("  reflection eps = %+d  (%s)" % (besteps,
                                             "none" if besteps > 0 else "yes"))
    print("  scale    s  = %.12f  (CAD units per PAGE pt)" % bestT[0])
    print("  rotation th = %+.9f deg" % math.degrees(bestT[1]))
    print("  transl. cad_x=%.6f  cad_y=%.6f" % (bestT[2], bestT[3]))
    print("  CONTROL consensus: %d / %d entities" % (bestn, len(ctrl_pool)))
    if runner:
        print("  runner-up (eps=%+d): %d / %d entities"
              % (runner[1], runner[0], len(ctrl_pool)))
        print("  margin best - runner-up = %d entities"
              % (bestn - runner[0]))
    print()

    # ---- robust refinement on CONTROL only ------------------------------
    Tc = robust_refit(bestT, ctrl_pool, psegs, grid, TOL_PT, U)
    print("=== refined transform (CONTROL entities only) ===")
    print("  scale    s  = %.12f" % Tc[0])
    print("  rotation th = %+.9f deg" % math.degrees(Tc[1]))
    print("  transl. cad_x=%.6f  cad_y=%.6f" % (Tc[2], Tc[3]))
    print()

    # ---- residuals ------------------------------------------------------
    for label, pool in (("CONTROL", ctrl_pool), ("CHECK  ", chk_pool)):
        rows = entity_residuals(Tc, pool, psegs, grid, TOL_PT, U)
        rad = [r_ for row in rows for r_ in row["rad"]]
        st = stats(rad)
        print("=== %s entity-level residuals (%s) ===" % (label, UL))
        print("  entities offered  : %d" % len(pool))
        print("  entities MATCHED  : %d" % len(rows))
        print("  entities unmatched: %d" % (len(pool) - len(rows)))
        print("  features (endpoints) matched: %d" % st["n"] if st else 0)
        if st:
            print("  radial RMS=%.4f  median=%.4f  p95=%.4f  max=%.4f"
                  % (st["rms"], st["median"], st["p95"], st["max"]))
            # per-axis
            dx_all = []
            dy_all = []
            for e in pool:
                m = entity_match(Tc, e, psegs, grid, TOL_PT)
                if m is None:
                    continue
                for q, p, _d in m:
                    X, Y = M.apply_T(Tc, p[0], p[1])
                    dx_all.append(X - q[0])
                    dy_all.append(Y - q[1])
            sx = math.sqrt(sum(v * v for v in dx_all) / len(dx_all)) if dx_all else 0
            sy = math.sqrt(sum(v * v for v in dy_all) / len(dy_all)) if dy_all else 0
            print("  d_cad_x RMS=%.4f   d_cad_y RMS=%.4f" % (sx, sy))
            if label.strip() == "CHECK":
                print("  threshold pass rates (%s):" % UL)
                if unit_code == 6:
                    for t_mm in (1, 5, 10, 20, 50, 100, 250, 500, 1000):
                        thr = t_mm
                        ok = sum(1 for r_ in rad if r_ <= thr)
                        print("     <= %6d %-3s : %6d / %6d  (%5.1f%%)"
                              % (t_mm, "mm", ok, len(rad),
                                 100.0 * ok / len(rad)))
                else:
                    for thr in (0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25,
                                0.5, 1.0):
                        ok = sum(1 for r_ in rad if r_ <= thr)
                        print("     <= %8.3f u : %6d / %6d  (%5.1f%%)"
                              % (thr, ok, len(rad), 100.0 * ok / len(rad)))
        print()


if __name__ == "__main__":
    main()

def dxf_ents_of(entities):
    """Entities that carry straight 2D geometry."""
    return [e for e in entities if e.segs]
