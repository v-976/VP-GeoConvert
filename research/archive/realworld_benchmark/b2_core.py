"""Set B2 — core matching primitives, rebuilt clean and self-tested.

Research tool. Nothing here is production code.

Why this module exists
----------------------
The Set B1 research scripts were audited and three components were found
defective:

  * a proximity matcher that never enforced its tolerance
    (`b1_analysis.nearest_point` returned the nearest candidate found in
    adjacent grid cells regardless of distance, so almost any point "matched");
  * a refinement step that DEGRADED a good hypothesis (324 -> 1 matched);
  * an unmatched-PDF metric that mixed PAGE and CAD coordinate domains.

None of those are reused. What IS reused from B1 is the one component that
proved selective: the fragmentation-tolerant sampled criterion with an explicit
direction test. It is re-implemented here with a self-test so the same class of
defect cannot go unnoticed again.

Correspondence criterion
------------------------
A DXF segment matches when EVERY sample point along its transformed image lies
within `tol` of a PAGE segment whose direction agrees within `dir_tol_deg`.

* proximity alone is NOT sufficient -- that was B1's defect;
* requiring one single PAGE segment to cover the whole DXF segment is too
  strict, because PDF generation legitimately splits one long CAD line into
  several collinear fragments (measured in Set A);
* sampling along the segment plus a direction test satisfies both.

Coordinate domains (never mixed)
--------------------------------
    PAGE space       u, v      after each PDF object's own transform matrix
    DXF CAD space    cad_x, cad_y   raw DXF group codes 10/20/11/21
    SURVEY space     X, Y      NOT USED. The CAD -> SURVEY mapping is not
                               established by this benchmark.

Strictly 2D. Z / H are never read.
"""

import math
import os
import random
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "geometry_benchmark"))

# Proven components from the Set A/B1 matcher. Do not reimplement.
from matcher import (fit_similarity, pseg_dist, apply_T, inv_apply,  # noqa
                     Grid as _Grid, ransac as _ransac, seg_len, ang_diff)

__all__ = ["fit_similarity", "pseg_dist", "apply_T", "inv_apply", "seg_len",
           "ang_diff", "SegGrid", "ransac", "seg_matches", "entity_matches",
           "seg_matches_set", "entity_rows", "midpoint_refit",
           "signed_segment_residuals", "spatial_split_balanced", "cell_map",
           "evidence", "search", "spread_sample", "stats",
           "TOL_PT", "DIR_TOL_DEG", "N_SAMPLES"]

TOL_PT = 1.5          # PAGE points
DIR_TOL_DEG = 1.0     # degrees, modulo pi (line direction, not oriented)
N_SAMPLES = 11


class SegGrid(_Grid):
    """Grid with a cached candidate list per cell, avoiding set rebuilding."""

    def near_list(self, p, r):
        c = self.cell
        gx0 = int((p[0] - r) // c)
        gx1 = int((p[0] + r) // c)
        gy0 = int((p[1] - r) // c)
        gy1 = int((p[1] + r) // c)
        out = {}
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                g = self.grid.get((gx, gy))
                if g:
                    for i in g:
                        out[i] = None
        return out


def ransac(dx_segs, pdf_segs, eps, tol=TOL_PT, n_dxf=30, n_pdf=60, **kw):
    return _ransac(dx_segs, pdf_segs, eps, tol=tol, n_dxf=n_dxf, n_pdf=n_pdf,
                   **kw)


def _collinear_hit(px, py, dir_rad, psegs, cand, tol, d_tol):
    """Best perpendicular deviation among direction-agreeing candidates."""
    best = None
    for i in cand:
        qa, qb = psegs[i]
        dx = qb[0] - qa[0]
        dy = qb[1] - qa[1]
        if dx == 0.0 and dy == 0.0:
            continue
        if abs(ang_diff(math.atan2(dy, dx), dir_rad)) > d_tol:
            continue
        d = pseg_dist((px, py), qa, qb)
        if d > tol:
            continue
        if best is None or d < best[0]:
            best = (d, i)
    return best


def seg_matches(T, a, b, psegs, grid, tol=TOL_PT,
                dir_tol_deg=DIR_TOL_DEG, n=N_SAMPLES):
    """Deviation stats in PAGE pt for one DXF segment, or None if unmatched.

    Returns (mean_dev, max_dev, [(pdf_seg_index, dev), ...]).
    """
    if T[0] <= 0:
        return None
    pa = inv_apply(T, a[0], a[1])
    pb = inv_apply(T, b[0], b[1])
    if pa == pb:
        return None
    dir_rad = math.atan2(pb[1] - pa[1], pb[0] - pa[0])
    d_tol = math.radians(dir_tol_deg)
    devs = []
    used = []
    for k in range(n):
        t = k / (n - 1.0)
        px = pa[0] + t * (pb[0] - pa[0])
        py = pa[1] + t * (pb[1] - pa[1])
        cand = grid.near_list((px, py), tol)
        if not cand:
            return None
        hit = _collinear_hit(px, py, dir_rad, psegs, cand, tol, d_tol)
        if hit is None:
            return None
        devs.append(hit[0])
        used.append((hit[1], hit[0]))
    return (sum(devs) / len(devs), max(devs), used)


def entity_matches(T, e, psegs, grid, tol=TOL_PT, dir_tol_deg=DIR_TOL_DEG):
    """All segments matched, or None. A whole entity is one unit of evidence."""
    out = []
    for a, b in e.segs:
        r = seg_matches(T, a, b, psegs, grid, tol, dir_tol_deg)
        if r is None:
            return None
        out.append(r)
    return out


def seg_matches_set(T, e, psegs, grid, tol=TOL_PT,
                    dir_tol_deg=DIR_TOL_DEG):
    """Boolean form of entity_matches: returns e itself when fully matched.

    Kept separate so the matrix runner can pass tolerance positionally without
    accidentally binding it to a different parameter.
    """
    for a, b in e.segs:
        if seg_matches(T, a, b, psegs, grid, tol, dir_tol_deg) is None:
            return None
    return e


def entity_rows(T, entities, psegs, grid, unit_scale=1.0):
    """Per-entity match rows. `unit_scale` converts PAGE pt -> report unit."""
    rows = []
    for e in entities:
        m = entity_matches(T, e, psegs, grid)
        if m is None:
            continue
        rad = [d * T[0] * unit_scale for (_mn, dx, _u) in m for d in (dx,)]
        rows.append({"entity": e, "segs": len(e.segs),
                     "rad": rad,
                     "mean": sum(rad) / len(rad), "max": max(rad)})
    return rows


def signed_segment_residuals(T, a, b, psegs, grid, tol=TOL_PT,
                             dir_tol_deg=DIR_TOL_DEG):
    """Signed CAD offsets (d_cad_x, d_cad_y) for one matched DXF segment.

    Maps the matched PAGE points back to CAD and reports the signed offset
    relative to the DXF endpoint. Returns None when the segment is unmatched.
    """
    r = seg_matches(T, a, b, psegs, grid, tol=tol, dir_tol_deg=dir_tol_deg)
    if r is None:
        return None
    pa = inv_apply(T, a[0], a[1])
    pb = inv_apply(T, b[0], b[1])
    dir_rad = math.atan2(pb[1] - pa[1], pb[0] - pa[0])
    d_tol = math.radians(dir_tol_deg)
    out = []
    for q, p in ((a, pa), (b, pb)):
        cand = grid.near_list(p, tol)
        hit = _collinear_hit(p[0], p[1], dir_rad, psegs, cand, tol, d_tol)
        if hit is None:
            return None
        qa, qb = psegs[hit[1]]
        dx = qb[0] - qa[0]
        dy = qb[1] - qa[1]
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(
            1.0, ((p[0] - qa[0]) * dx + (p[1] - qa[1]) * dy) / L2))
        proj = (qa[0] + t * dx, qa[1] + t * dy)
        X, Y = apply_T(T, proj[0], proj[1])
        out.append((X - q[0], Y - q[1]))
    return out


def midpoint_refit(T, entities, psegs, grid, rounds=6, keep=0.7,
                   tol=TOL_PT, dir_tol_deg=DIR_TOL_DEG):
    """Least-squares refit on DXF segment midpoints. Exact for a similarity.

    Midpoints are used because they survive PDF fragmentation: a long DXF line
    split into collinear PAGE fragments still has the correct midpoint.
    """
    cur = T
    for _ in range(rounds):
        rows = entity_rows(cur, entities, psegs, grid)
        if len(rows) < 12:
            break
        rows.sort(key=lambda r: r["mean"])
        good = rows[:max(12, int(keep * len(rows)))]
        pairs = []
        for r in good:
            for (a, b) in r["entity"].segs:
                if seg_matches(cur, a, b, psegs, grid, tol,
                               dir_tol_deg) is None:
                    continue
                pm = inv_apply(cur, (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
                pairs.append((pm[0], pm[1],
                              (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0))
        f = fit_similarity(pairs, cur[4])
        if f is None:
            break
        shift = (abs(f[0] - cur[0]) / cur[0] + abs(f[1] - cur[1])
                 + abs(f[2] - cur[2]) + abs(f[3] - cur[3]))
        cur = (f[0], f[1], f[2], f[3], cur[4])
        if shift < 1e-12:
            break
    return cur


def spatial_split_balanced(entities, n_cells=8):
    """Balanced AND spatially spread entity-level CONTROL/CHECK split.

    Entities are bucketed into an n_cells x n_cells grid over their own
    bounding box, sorted by cell, then alternated. Both pools therefore span
    the whole sheet and neither can be a single local cluster.
    """
    xs = [p[0] for e in entities for a, b in e.segs for p in (a, b)]
    ys = [p[1] for e in entities for a, b in e.segs for p in (a, b)]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    dx = max(1e-9, (x1 - x0) / n_cells)
    dy = max(1e-9, (y1 - y0) / n_cells)
    keyed = []
    for e in entities:
        n = 2 * len(e.segs)
        cx = sum(p[0] for a, b in e.segs for p in (a, b)) / n
        cy = sum(p[1] for a, b in e.segs for p in (a, b)) / n
        gx = min(n_cells - 1, max(0, int((cx - x0) / dx)))
        gy = min(n_cells - 1, max(0, int((cy - y0) / dy)))
        keyed.append((gy, gx, e.idx, e))
    keyed.sort()
    ctrl = [k[3] for i, k in enumerate(keyed) if i % 2 == 0]
    chk = [k[3] for i, k in enumerate(keyed) if i % 2 == 1]
    return ctrl, chk


def cell_map(entities, n_cells=8):
    """Occupancy counters: total entities per cell and matched entities."""
    xs = [p[0] for e in entities for a, b in e.segs for p in (a, b)]
    ys = [p[1] for e in entities for a, b in e.segs for p in (a, b)]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    dx = max(1e-9, (x1 - x0) / n_cells)
    dy = max(1e-9, (y1 - y0) / n_cells)
    occ = Counter()
    for e in entities:
        n = 2 * len(e.segs)
        cx = sum(p[0] for a, b in e.segs for p in (a, b)) / n
        cy = sum(p[1] for a, b in e.segs for p in (a, b)) / n
        gx = min(n_cells - 1, max(0, int((cx - x0) / dx)))
        gy = min(n_cells - 1, max(0, int((cy - y0) / dy)))
        occ[(gy, gx)] += 1
    return occ


def stats(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return {"n": n, "rms": math.sqrt(sum(x * x for x in s) / n),
            "median": s[n // 2],
            "p95": s[min(n - 1, int(round(0.95 * (n - 1))))],
            "max": s[-1]}


def evidence(T, pool_ctrl, pool_chk, psegs, grid, unit_scale=1.0,
             tol=TOL_PT, dir_tol_deg=DIR_TOL_DEG):
    """Evidence dimensions, measured SEPARATELY. Never a magic score."""
    rows_c = entity_rows(T, pool_ctrl, psegs, grid, unit_scale)
    rows_k = entity_rows(T, pool_chk, psegs, grid, unit_scale)
    rad_c = [v for r in rows_c for v in r["rad"]]
    rad_k = [v for r in rows_k for v in r["rad"]]

    # D. entity diversity: matched / total per entity type
    tot_t = Counter(e.etype for e in pool_ctrl + pool_chk)
    mat_t = Counter(r["entity"].etype for r in rows_c + rows_k)
    types_total = len(tot_t)
    types_matched = sum(1 for t in tot_t if mat_t[t] > 0)

    # E. feature diversity: distinct PAGE segments touched
    pdf_idx = set()
    for e in (pool_ctrl + pool_chk):
        m = entity_matches(T, e, psegs, grid)
        if m is None:
            continue
        for (_mn, _mx, used) in m:
            for (i, _d) in used:
                pdf_idx.add(i)

    # C. spatial distribution over 8x8 cells
    allp = pool_ctrl + pool_chk
    occ_all = cell_map(allp)
    mat_all = [r["entity"] for r in rows_c + rows_k]
    occ_mat = cell_map(mat_all) if mat_all else Counter()
    cells_filled = sum(1 for k in occ_all if occ_mat.get(k, 0) > 0)

    # J. repeated-geometry ambiguity: matched entities whose midpoint has
    #    another matched entity of the same length within a small radius
    return {
        "T": T,
        "A_consensus": len(rows_c) + len(rows_k),
        "A_control": len(rows_c), "A_check": len(rows_k),
        "B_fraction": (len(rows_c) + len(rows_k)) / max(1, len(allp)),
        "B_control_fraction": len(rows_c) / max(1, len(pool_ctrl)),
        "B_check_fraction": len(rows_k) / max(1, len(pool_chk)),
        "C_cells_filled": cells_filled,
        "C_cells_total": len(occ_all),
        "C_distribution": cells_filled / max(1, len(occ_all)),
        "D_types_matched": types_matched, "D_types_total": types_total,
        "D_entity_type_counts": {t: (mat_t[t], tot_t[t]) for t in tot_t},
        "E_pdf_segments_used": len(pdf_idx),
        "E_pdf_segments_total": len(psegs),
        "F_control": stats(rad_c), "F_check": stats(rad_k),
        "G_check_entities": len(rows_k),
        "G_check_features": len(rad_k),
        "rad_control": rad_c, "rad_check": rad_k,
        "rows_control": rows_c, "rows_check": rows_k,
    }


# ---------------------------------------------------------------- self-test

def _self_test():
    """Synthetic checks. Must pass before the module is used on real data."""
    fails = []

    # 1. fit_similarity recovers an exact synthetic similarity.
    rnd = random.Random(1)
    s, th, tx, ty = 0.1764, math.radians(-169.75), 25485383.87, 6677815.47
    T = (s, th, tx, ty, +1)
    pairs = []
    for _ in range(20):
        u = rnd.uniform(0, 3000)
        v = rnd.uniform(0, 800)
        X, Y = apply_T(T, u, v)
        pairs.append((u, v, X, Y))
    f = fit_similarity(pairs, +1)
    if f is None or abs(f[0] - s) > 1e-12 or abs(f[1] - th) > 1e-12:
        fails.append("fit_similarity does not recover an exact similarity")
    if f and (abs(f[2] - tx) > 1e-6 or abs(f[3] - ty) > 1e-6):
        fails.append("fit_similarity translation error")

    # 2. reflection is recovered only when eps is given correctly.
    Tref = (s, th, tx, ty, -1)
    pairs_r = []
    for _ in range(20):
        u = rnd.uniform(0, 3000)
        v = rnd.uniform(0, 800)
        X, Y = apply_T(Tref, u, v)
        pairs_r.append((u, v, X, Y))
    fr = fit_similarity(pairs_r, -1)
    if fr is None or abs(fr[0] - s) > 1e-12:
        fails.append("fit_similarity fails for reflection eps=-1")

    # 3. synthetic PAGE scene vs synthetic DXF scene, both directions tested.
    def scene(seed):
        r = random.Random(seed)
        segs = []
        ents = []
        for _ in range(60):
            x0 = r.uniform(0, 2000)
            y0 = r.uniform(0, 600)
            L = r.uniform(20, 400)
            a_ = r.uniform(0, math.pi)
            a = (x0 + L * math.cos(a_), y0 + L * math.sin(a_))
            b = (x0 + L * math.cos(a_ + math.pi), y0 + L * math.sin(a_ + math.pi))
            segs.append((a, b))
            ents.append(_FakeEnt(len(ents), "LINE", a, b))
        return segs, ents

    psegs, _ = scene(11)
    T2 = (0.3, math.radians(35.0), 100.0, -50.0, +1)
    # T2 maps the synthetic PAGE scene onto a synthetic CAD scene.
    # The DXF-side entities must carry the MAPPED coordinates, because in the
    # real pipeline the DXF holds CAD coordinates and the PDF holds PAGE ones.
    cad_segs = []
    for (a, b) in psegs:
        X1, Y1 = apply_T(T2, *a)
        X2, Y2 = apply_T(T2, *b)
        cad_segs.append(((X1, Y1), (X2, Y2)))
    dents = [_FakeEnt(i, "LINE", cad_segs[i][0], cad_segs[i][1])
             for i in range(len(cad_segs))]
    grid = SegGrid(psegs)          # PAGE side holds the ORIGINAL scene
    ok = sum(1 for e in dents
             if entity_matches(T2, e, psegs, grid) is not None)
    if ok != len(dents):
        fails.append("exact transform should match all synthetic entities: "
                     "%d/%d" % (ok, len(dents)))

    # 4. TOLERANCE IS ENFORCED -- the defect that invalidated Set B1.
    wrong = (T2[0], T2[1], T2[2] + 300.0, T2[3], +1)
    bad = sum(1 for e in dents
              if entity_matches(wrong, e, psegs, grid) is not None)
    if bad != 0:
        fails.append("TOLERANCE NOT ENFORCED: 300 pt shift still matched "
                     "%d/%d entities" % (bad, len(dents)))

    # 5. proximity alone must NOT be sufficient: a 1.5 deg rotation leaves
    #    points numerically close to geometry but changes every direction.
    rot = (T2[0], T2[1] + math.radians(1.5), T2[2], T2[3], +1)
    near_only = sum(1 for e in dents
                    if entity_matches(rot, e, psegs, grid) is not None)
    if near_only > len(dents) * 0.10:
        fails.append("direction test too weak: 1.5 deg rotation matched "
                     "%d/%d" % (near_only, len(dents)))

    # 6. midpoint_refit must not degrade a correct hypothesis.
    T3 = (T2[0] * 1.000001, T2[1] + 1e-7, T2[2] + 0.01, T2[3] - 0.01, +1)
    before = sum(1 for e in dents
                 if entity_matches(T3, e, psegs, grid) is not None)
    T4 = midpoint_refit(T3, dents, psegs, grid)
    after = sum(1 for e in dents
                if entity_matches(T4, e, psegs, grid) is not None)
    if after < before:
        fails.append("midpoint_refit DEGRADED the hypothesis: %d -> %d"
                     % (before, after))

    # 7. spatial split is balanced and disjoint.
    ctrl, chk = spatial_split_balanced(dents)
    ids_c = {id(e) for e in ctrl}
    ids_k = {id(e) for e in chk}
    if ids_c & ids_k:
        fails.append("spatial split pools overlap")
    if abs(len(ctrl) - len(chk)) > 2:
        fails.append("spatial split not balanced: %d / %d"
                     % (len(ctrl), len(chk)))

    # 8. the SIGN and MAGNITUDE of the signed residual are meaningful.
    #     With an identity transform PAGE == CAD numerically, so shifting the
    #     PAGE scene by a known offset must read back as exactly that offset.
    # 8. the SIGN and MAGNITUDE of the signed residual are meaningful.
    #     A well-separated scene (horizontal segments 50 pt apart) removes any
    #     ambiguity from neighbouring geometry. With an identity transform
    #     PAGE == CAD numerically, so a known PERPENDICULAR PAGE offset must
    #     read back as exactly that offset in the residual. The offset is
    #     perpendicular on purpose: a shift ALONG a line is absorbed by the
    #     projection and would carry no information.
    T_id = (1.0, 0.0, 0.0, 0.0, +1)
    offs = (0.0, 1.0)
    wide = [((0.0, float(j) * 50.0), (400.0, float(j) * 50.0))
            for j in range(6)]
    shifted = [((a[0] + offs[0], a[1] + offs[1]),
                (b[0] + offs[0], b[1] + offs[1])) for (a, b) in wide]
    gshift = SegGrid(shifted)
    r0 = signed_segment_residuals(T_id, wide[0][0], wide[0][1],
                                  shifted, gshift)
    if r0 is None:
        fails.append("signed_segment_residuals returned None on shifted data")
    else:
        for (ddx, ddy) in r0:
            if not (abs(ddx - offs[0]) < 1e-6 and abs(ddy - offs[1]) < 1e-6):
                fails.append("signed residual sign/magnitude wrong: "
                             "expected %r got %r" % (offs, r0))
                break
    r1 = signed_segment_residuals(T_id, wide[0][0], wide[0][1],
                                  wide, SegGrid(wide))
    if r1 is not None and max(abs(v) for p in r1 for v in p) > 1e-6:
        fails.append("signed residual not ~0 on unshifted data: %r" % (r1,))

    # 9. `search` recovers a known synthetic similarity from PAGE + CAD only.
    #    Built with well-separated geometry so the pair search is well posed.
    r2 = random.Random(99)
    page_segs = []
    for j in range(40):
        y0 = j * 60.0
        L = 120.0 + r2.uniform(0, 700)
        x0 = r2.uniform(0, 300)
        page_segs.append(((x0, y0), (x0 + L, y0)))
        if j % 3 == 0:
            page_segs.append(((x0 + L / 2.0, y0),
                             (x0 + L / 2.0, y0 + 60.0)))
    T_true = (0.176403540756, math.radians(-169.749115222),
              25485383.8694, 6677815.4683, +1)
    cad_segs2 = []
    for (a, b) in page_segs:
        cad_segs2.append((apply_T(T_true, *a), apply_T(T_true, *b)))
    fake = [_FakeEnt(i, "LINE", cad_segs2[i][0], cad_segs2[i][1])
            for i in range(len(cad_segs2))]
    res = search(cad_segs2, page_segs, +1, pool=fake,
                 n_dxf=40, n_pdf=90, probe_segs=110, shortlist=120,
                 max_fit=6000)
    if res["T"] is None:
        fails.append("search returned no hypothesis on synthetic data")
    else:
        if (abs(res["T"][0] - T_true[0]) / T_true[0] > 1e-6
                or abs(res["T"][1] - T_true[1]) > 1e-6):
            fails.append("search recovered the wrong scale/rotation: "
                         "%r vs %r" % (res["T"][:2], T_true[:2]))
        if (abs(res["T"][2] - T_true[2]) > 1e-3
                or abs(res["T"][3] - T_true[3]) > 1e-3):
            fails.append("search recovered the wrong translation: %r"
                         % (res["T"][2:4],))
        if res["consensus"] < len(fake) * 0.9:
            fails.append("search consensus too low on synthetic data: "
                         "%d/%d" % (res["consensus"], len(fake)))
    # 9b. the same scene must yield NO usable hypothesis for eps = -1.
    res_r = search(cad_segs2, page_segs, -1, pool=fake,
                   n_dxf=40, n_pdf=90, probe_segs=110, shortlist=60,
                   max_fit=3000)
    if res_r["T"] is not None and res_r["consensus"] >= len(fake) * 0.5:
        fails.append("wrong reflection eps still matched: %d/%d"
                     % (res_r["consensus"], len(fake)))

    # 10. the signature Hough search recovers a known synthetic similarity,
    #     including a scale factor of 1e-3 and a reflection.
    for tag, Tt in (("plain", T_true),
                    ("mm-scale", (0.000176403540756,
                                  math.radians(-169.749115222),
                                  25485383.8694, 6677815.4683, +1)),
                    ("reflected", (0.176403540756,
                                   math.radians(-169.749115222),
                                   25485383.8694, 6677815.4683, -1))):
        cd = []
        for (a, b) in page_segs:
            cd.append((apply_T(Tt, *a), apply_T(Tt, *b)))
        fe = [_FakeEnt(i, "LINE", cd[i][0], cd[i][1])
              for i in range(len(cd))]
        rr = hough_search(cd, page_segs, Tt[4], pool=fe)
        if not rr:
            fails.append("hough_search found nothing (%s)" % tag)
            continue
        best = rr[0]
        ok = (abs(best["T"][0] - Tt[0]) / Tt[0] < 0.02
              and abs(((best["T"][1] - Tt[1] + math.pi) % (2 * math.pi))
                      - math.pi) < math.radians(1.0)
              and abs(best["T"][2] - Tt[2]) < 50.0
              and abs(best["T"][3] - Tt[3]) < 50.0)
        if not ok:
            fails.append("hough_search wrong transform (%s): s=%.9g "
                         "th=%+.4f t=(%.1f, %.1f)"
                         % (tag, best["T"][0], math.degrees(best["T"][1]),
                            best["T"][2], best["T"][3]))
        if best["consensus"] < len(fe) * 0.8:
            fails.append("hough_search consensus too low (%s): %d/%d"
                         % (tag, best["consensus"], len(fe)))

    # 11. PARTIAL overlap: only 40 % of the CAD geometry exists in the PAGE
    #     scene, plus decoy geometry. The transform must still be recovered.
    rr2 = random.Random(7)
    keep = [sg for i, sg in enumerate(page_segs) if i % 5 < 2]
    keep = [(sg, rr2.uniform(0, 1500), rr2.uniform(0, 800))
            for sg in keep]
    pd_decoy = []
    for i in range(200):
        x0 = rr2.uniform(0, 3000)
        y0 = rr2.uniform(0, 800)
        L = rr2.uniform(10, 600)
        a_ = rr2.uniform(0, math.pi)
        pd_decoy.append(((x0, y0),
                         (x0 + L * math.cos(a_), y0 + L * math.sin(a_))))
    pd_all = [sg for sg, _o, _p in keep] + pd_decoy
    T_p = (0.176403540756, math.radians(-169.749115222),
           25485383.8694, 6677815.4683, +1)
    kept_segs = [sg for sg, _o, _p in keep]
    cp = [(apply_T(T_p, sg[0][0], sg[0][1]),
           apply_T(T_p, sg[1][0], sg[1][1])) for sg in kept_segs]
    fp = [_FakeEnt(i, "LINE", cp[i][0], cp[i][1]) for i in range(len(cp))]
    rp = hough_search(cp, pd_all, +1, pool=fp)
    if not rp:
        fails.append("hough_search found nothing on partial overlap")
    else:
        bt = rp[0]["T"]
        good = (abs(bt[0] - T_p[0]) / T_p[0] < 0.02
                and abs(((bt[1] - T_p[1] + math.pi) % (2 * math.pi))
                        - math.pi) < math.radians(1.0)
                and abs(bt[2] - T_p[2]) < 50.0
                and abs(bt[3] - T_p[3]) < 50.0)
        if not good:
            fails.append("hough_search wrong on partial overlap: "
                         "s=%.9g th=%+.4f t=(%.1f, %.1f)"
                         % (bt[0], math.degrees(bt[1]), bt[2], bt[3]))

    return fails


class _FakeEnt:
    def __init__(self, idx, etype, a, b):
        self.idx = idx
        self.etype = etype
        self.layer = "L"
        self.segs = [(a, b)]
        self.center = None
        self.radius = None


def _reproduces(T, dseg, pseg, tol):
    for k in (0, 1):
        uu = inv_apply(T, dseg[k][0], dseg[k][1])
        if math.hypot(uu[0] - pseg[k][0], uu[1] - pseg[k][1]) > 3.0 * tol:
            return False
    return True


def _cheap_count(T, dsegs, grid, tol):
    """Cheap inlier count: proximity only, no direction test, no sampling.

    Used ONLY to shortlist hypotheses before the expensive entity-level
    consensus. Never reported as a match count.
    """
    n = 0
    for a, b in dsegs:
        pa = inv_apply(T, a[0], a[1])
        pb = inv_apply(T, b[0], b[1])
        ok = False
        for src in (pa, pb):
            for i in grid.near_list(src, tol):
                qa, qb = grid.segs[i]
                if (pseg_dist(pa, qa, qb) <= tol
                        and pseg_dist(pb, qa, qb) <= tol):
                    ok = True
                    break
            if ok:
                break
        if ok:
            n += 1
    return n


def spread_sample(items, k):
    """Evenly spaced subsample. Keeps short and long features represented.

    The Set A/B1 RANSAC probed only the LONGEST segments. On real-world data the
    longest DXF features may simply be absent from the PDF, which drove the
    probe score to 0 even for a correct transform. A spread sample removes
    that blind spot.
    """
    if len(items) <= k:
        return list(items)
    step = len(items) / float(k)
    return [items[int(i * step)] for i in range(k)]


def search(dsegs, psegs, eps, tol=TOL_PT, n_dxf=40, n_pdf=90,
           probe_segs=110, shortlist=160, max_fit=9000,
           log_ratio_tol=0.006, log_bin=0.002,
           pool=None, grid=None, cons_k=140):
    """Exhaustive 2-segment similarity search with a SPREAD probe.

    Stage 1  length-ratio prefilter. Scale, translation and rotation invariant,
             so it assumes no rotation, no scale and no position.
    Stage 2  exact 2-point fit, both endpoint orderings, verified to reproduce
             both source segments.
    Stage 3  cheap proximity shortlist over a SPREAD of DXF segments.
    Stage 4  full entity-level consensus on the shortlist, using exactly the
             same criterion as `entity_matches`.

    Returns the best hypothesis found, with its stage-3 and stage-4 scores.
    """
    grid = grid if grid is not None else SegGrid(psegs)
    dxs = sorted(dsegs, key=lambda s: -seg_len(*s))[:n_dxf]
    pfs = sorted(psegs, key=lambda s: -seg_len(*s))[:n_pdf]
    Ld = [seg_len(*s) for s in dxs]
    Lp = [seg_len(*s) for s in pfs]

    buckets = defaultdict(list)
    for j in range(len(pfs)):
        if Lp[j] <= 0:
            continue
        for m in range(j + 1, len(pfs)):
            if Lp[m] <= 0:
                continue
            lr = math.log(Lp[j] / Lp[m])
            if lr < 0:
                lr, jj, mm = -lr, m, j
            else:
                jj, mm = j, m
            buckets[int(round(lr / log_bin))].append((jj, mm))

    probe = spread_sample(dxs, probe_segs)
    cons_pool = spread_sample(list(pool or []), cons_k)
    span = int(math.ceil(log_ratio_tol / log_bin)) + 1

    cands = []
    tried = fitted = 0
    stop = False
    for i in range(len(dxs)):
        if Ld[i] <= 0:
            continue
        for k in range(i + 1, len(dxs)):
            if Ld[k] <= 0:
                continue
            lr = math.log(Ld[i] / Ld[k])
            if lr < 0:
                lr, ii, kk = -lr, k, i
            else:
                ii, kk = i, k
            centre = int(round(lr / log_bin))
            cand = []
            for b in range(centre - span, centre + span + 1):
                cand.extend(buckets.get(b, ()))
            if not cand:
                continue
            sa, sb = dxs[ii], dxs[kk]
            for jj, mm in cand:
                tried += 1
                p, q = pfs[jj], pfs[mm]
                for flip in (False, True):
                    p0, p1 = (p[1], p[0]) if flip else (p[0], p[1])
                    q0, q1 = (q[1], q[0]) if flip else (q[0], q[1])
                    f = fit_similarity(
                        [(p0[0], p0[1], sa[0][0], sa[0][1]),
                         (p1[0], p1[1], sa[1][0], sa[1][1]),
                         (q0[0], q0[1], sb[0][0], sb[0][1]),
                         (q1[0], q1[1], sb[1][0], sb[1][1])], eps)
                    if f is None:
                        continue
                    s_, th_, tx_, ty_ = f
                    if not (1e-7 < s_ < 1e6):
                        continue
                    T_ = (s_, th_, tx_, ty_, eps)
                    if not _reproduces(T_, sa, (p0, p1), tol):
                        continue
                    if not _reproduces(T_, sb, (q0, q1), tol):
                        continue
                    fitted += 1
                    cands.append((_cheap_count(T_, probe, grid, tol), T_))
                    break
            if fitted >= max_fit:
                stop = True
                break
        if stop:
            break

    if not cands:
        return {"T": None, "cheap": 0, "consensus": 0, "tried": tried,
                "fitted": fitted, "eps": eps, "cands": 0}

    cands.sort(key=lambda z: -z[0])
    uniq = []
    for ch, T_ in cands:
        dup = False
        for _c, U in uniq:
            if (abs(U[0] - T_[0]) / U[0] < 1e-6
                    and abs(U[1] - T_[1]) < 1e-9
                    and abs(U[2] - T_[2]) < 1e-6
                    and abs(U[3] - T_[3]) < 1e-6):
                dup = True
                break
        if not dup:
            uniq.append((ch, T_))
        if len(uniq) >= shortlist:
            break

    best_c = 0
    best_T = None
    ranked = []
    for ch, T_ in uniq:
        if cons_pool:
            c = sum(1 for e in cons_pool
                    if entity_matches(T_, e, psegs, grid) is not None)
        else:
            c = ch
        ranked.append((c, T_))
        if c > best_c:
            best_c, best_T = c, T_
    ranked.sort(key=lambda z: -z[0])
    return {"T": best_T, "cheap": uniq[0][0] if uniq else 0,
            "consensus": best_c, "tried": tried, "fitted": fitted,
            "eps": eps, "cands": len(cands),
            "ranked": [{"consensus": c, "scale": T_[0],
                        "rotation_deg": math.degrees(T_[1]),
                        "tx": T_[2], "ty": T_[3], "eps": T_[4]}
                       for c, T_ in ranked[:8]]}


DBIN_LOG = 0.08        # log10-length bin for the signature
DBIN_DIR = 3.0         # degrees, direction modulo pi
DDIR_N = int(round(180.0 / DBIN_DIR))
DDIAG = 80             # +/- log10 scale offsets scanned


def _signature(segs):
    """Histogram of (log10 length, direction mod 180 deg) for a segment set."""
    h = Counter()
    for a, b in segs:
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L <= 0:
            continue
        th = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0
        bl = int(round(math.log10(L) / DBIN_LOG))
        bd = int(round(th / DBIN_DIR)) % DDIR_N
        h[(bl, bd)] += 1
    return h


def signature_peaks(h_dxf, h_pdf, keep=24, sep_log=2, sep_dir=4):
    """Top (log-scale offset, rotation offset) peaks of the signature match.

    The signature is invariant to translation and rotation-free under similarity:
    a similarity multiplies all lengths by s and rotates all directions by th.
    Correlation over the two offsets therefore locates (s, th) WITHOUT any
    assumption about position, and WITHOUT restricting the hypothesis to the
    longest segments -- which is what limited the Set A/B1 pair search.
    """
    pdf_by_dir = defaultdict(list)
    for (bl, bd), n in h_pdf.items():
        pdf_by_dir[bd].append((bl, n))
    scores = []
    for (bl, bd), nd in h_dxf.items():
        for bd2, entries in pdf_by_dir.items():
            dr = (bd2 - bd) % DDIR_N
            if dr > DDIR_N // 2:
                dr -= DDIR_N
            for bl2, np_ in entries:
                di = bl2 - bl
                if abs(di) > DDIAG:
                    continue
                scores.append((nd * np_, di, dr))
    scores.sort(key=lambda z: -z[0])
    peaks = []
    for sc, di, dr in scores:
        ok = True
        for _s, pi_, pr in peaks:
            if abs(pi_ - di) <= sep_log and abs(pr - dr) <= sep_dir:
                ok = False
                break
        if ok:
            peaks.append((sc, di, dr))
        if len(peaks) >= keep:
            break
    return peaks


def _buckets_pdf(psegs):
    """Bucket PAGE segments by (log10 length bin, direction bin)."""
    b = defaultdict(list)
    for idx, (a, bb) in enumerate(psegs):
        L = math.hypot(bb[0] - a[0], bb[1] - a[1])
        if L <= 0:
            continue
        th = math.degrees(math.atan2(bb[1] - a[1], bb[0] - a[0])) % 180.0
        bl = int(round(math.log10(L) / DBIN_LOG))
        bd = int(round(th / DBIN_DIR)) % DDIR_N
        b[(bl, bd)].append(idx)
    return b


def translation_votes(dsegs, psegs, buckets, s, th, eps, di, dr,
                      tol=TOL_PT, max_bx=8):
    """Vote for the translation implied by length+direction compatible pairs."""
    c = math.cos(th)
    sn = math.sin(th)
    vb = tol * 2.0
    votes = Counter()
    for a, b in dsegs:
        Ld = math.hypot(b[0] - a[0], b[1] - a[1])
        if Ld <= 0:
            continue
        Lp = Ld / s
        bl = int(round(math.log10(Lp) / DBIN_LOG))
        ang = (math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) + math.degrees(th))
        bd = int(round((ang % 180.0) / DBIN_DIR)) % DDIR_N
        cand = []
        for dd in range(-max_bx, max_bx + 1):
            for dl in (-1, 0, 1):
                cand.extend(buckets.get((bl + di + dl, (bd + dr + dd) % DDIR_N),
                                        ()))
        if not cand:
            continue
        mx = (a[0] + b[0]) / 2.0
        my = (a[1] + b[1]) / 2.0
        # PAGE midpoint of the DXF midpoint under (s, th) with translation t:
        #   u = ( eps*(my*cn - mx*sn)/s + tx/s )
        #   v = ( eps*(mx*sn + my*cn)/s + ty/s )
        for idx in cand:
            qa, qb = psegs[idx]
            px = (qa[0] + qb[0]) / 2.0
            py = (qa[1] + qb[1]) / 2.0
            # solve for tx, ty given the PAGE midpoint
            # inverse of a pure similarity without translation:
            ux = eps * (mx * c - my * sn) / s
            uy = eps * (mx * sn + my * c) / s
            tx = (px - ux) * s
            ty = (py - uy) * s
            votes[(int(math.floor(tx / vb)), int(math.floor(ty / vb)))] += 1
    return votes, vb


def pair_fits(dsegs, psegs, eps, s, th, buckets, tol=TOL_PT,
              win_log=1, win_dir=1, both_orders=True, s_acc=0.048,
              th_acc_deg=10.0):
    """Exact 2-point similarity fits for length/direction-compatible pairs.

    `s` and `th` need only be accurate enough to bring compatible pairs into
    the same bucket. The FIT is exact for any pair, so an exact 2-point fit
    alone proves nothing; the filters below are what make the fit meaningful:

      * the fitted scale must agree with the bucket hypothesis to within
        `s_tol_bins` length bins,
      * the fitted rotation must agree to within `d_tol_bins` direction bins.

    Returns only fits that satisfy both.
    """
    out = []
    s_hi = 10.0 ** s_acc
    s_lo = 1.0 / s_hi
    d_hi = math.radians(th_acc_deg)
    for a, b in dsegs:
        Ld = math.hypot(b[0] - a[0], b[1] - a[1])
        if Ld <= 0:
            continue
        Lp = Ld / s
        bl = int(round(math.log10(Lp) / DBIN_LOG))
        # The transform maps PAGE -> CAD, so a CAD direction maps BACK to a
        # PAGE direction by SUBTRACTING the rotation. Adding it instead was a
        # sign error that silently produced an empty hypothesis set.
        ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) - \
            math.degrees(th)
        bd = int(round((ang % 180.0) / DBIN_DIR)) % DDIR_N
        cand = []
        for dl in range(-win_log, win_log + 1):
            for dd in range(-win_dir, win_dir + 1):
                cand.extend(buckets.get((bl + dl, (bd + dd) % DDIR_N), ()))
        if not cand:
            continue
        for idx in cand:
            qa, qb = psegs[idx]
            for p0, p1 in (((qa, qb), (qb, qa)) if both_orders
                           else ((qa, qb),)):
                f = fit_similarity(
                    [(p0[0], p0[1], a[0], a[1]),
                     (p1[0], p1[1], b[0], b[1])], eps)
                if f is None:
                    continue
                s_, th_, tx_, ty_ = f
                if not (s_lo * s < s_ < s_hi * s):
                    continue
                if abs(ang_diff(th_, th)) > d_hi:
                    continue
                if not (1e-9 < s_ < 1e9):
                    continue
                out.append((s_, th_, tx_, ty_, eps))
    return out


def cluster_transforms(Ts, ref=None, tol=TOL_PT, n_bins=90,
                       q_log=0.0015, q_th=0.35):
    """Cluster exact fits; return the largest clusters.

    The translation bin MUST be expressed in PAGE points, not in CAD units.
    Binning tx/ty directly in CAD units collapses when the CAD unit is small
    (millimetre drawings, or any scale well below one unit per point): every
    candidate then falls into a single bin and the cluster mean is meaningless.
    A fixed CAD reference point is therefore mapped to PAGE space with each
    candidate transform and binned there, where 	ol is defined.
    """
    vb = 3.0 * tol
    bins = defaultdict(list)
    for T in Ts:
        if ref is not None:
            u, v = apply_T(T, ref[0], ref[1])
            bu = int(round(u / vb))
            bv = int(round(v / vb))
        else:
            bu = int(round(T[2] / vb))
            bv = int(round(T[3] / vb))
        k = (int(round(math.log10(T[0]) / q_log)),
             int(round(math.degrees(T[1]) / q_th)), bu, bv)
        bins[k].append(T)
    ranked = sorted(bins.items(), key=lambda z: -len(z[1]))
    out = []
    for _k, members in ranked:
        ns = len(members)
        ms = sum(m[0] for m in members) / ns
        mt = sum(m[1] for m in members) / ns
        mx = sum(m[2] for m in members) / ns
        my = sum(m[3] for m in members) / ns
        out.append({"T": (ms, mt, mx, my, members[0][4]),
                    "votes": ns, "T_raw": members[0]})
        if len(out) >= n_bins:
            break
    return out


def length_peaks(h_dxf, h_pdf, keep=8, sep=2):
    """Top log10-scale offsets from the LENGTH histogram alone.

    Engineering drawings are overwhelmingly axis aligned, so the direction
    histogram is nearly degenerate and carries little information about
    rotation. Length is rotation invariant under a similarity and is therefore
    the reliable global signal for scale.
    """
    hd = defaultdict(int)
    for (bl, _bd), n in h_dxf.items():
        hd[bl] += n
    hp = defaultdict(int)
    for (bl, _bd), n in h_pdf.items():
        hp[bl] += n
    bls = sorted(hp)
    scores = []
    for bl, nd in hd.items():
        for bl2, np_ in hp.items():
            di = bl2 - bl
            if abs(di) > DDIAG:
                continue
            scores.append((nd * np_, di))
    scores.sort(key=lambda z: -z[0])
    peaks = []
    for sc, di in scores:
        if any(abs(p - di) <= sep for _s, p in peaks):
            continue
        peaks.append((sc, di))
        if len(peaks) >= keep:
            break
    return peaks


def hough_search(dsegs, psegs, eps, pool=None, grid=None, tol=TOL_PT,
                 sig_cap=4000, di_step=1, dr_step=15.0, win_log=1, win_dir=3,
                 verify_top=400, cons_k=140, fit_segs=80):
    """Similarity search for a PAGE <-> CAD similarity, from geometry alone.

    Design notes, forced by measurement rather than by theory:
      * A histogram peak CANNOT localise the scale. Measured on a synthetic
        scene whose length histogram is nearly uniform, the true log-scale
        offset was absent from the top 12 correlation peaks. The full
        data-derived scale range is therefore scanned with overlapping
        windows, and discrimination is left to the clustering stage.
      * The direction histogram is nearly degenerate in engineering drawings
        (axis aligned), so rotation is covered by an overlapping coarse scan.
      * Each candidate scale/rotation is used only to BUCKET compatible pairs.
        The 2-point fit is exact, and the exact fits are clustered in
        (log s, theta, tx, ty): the true transform is voted by every genuinely
        matching segment, while coincidences scatter.

    No file names, no known coordinates, no hand-picked control points.
    """
    grid = grid if grid is not None else SegGrid(psegs)
    sig_pdf = sorted(psegs, key=lambda s: -seg_len(*s))[:sig_cap]
    buckets = _buckets_pdf(sig_pdf)
    fsegs = sorted(dsegs, key=lambda s: -seg_len(*s))[:fit_segs]
    if not fsegs or not sig_pdf:
        return []

    pl = [seg_len(*s) for s in sig_pdf]
    pl = [v for v in pl if v > 0]
    dl = [seg_len(*s) for s in fsegs]
    dl = [v for v in dl if v > 0]
    if not pl or not dl:
        return []
    # s = cad_length / page_length, so the scan range is
    #   s_min = min(cad) / max(page)   and   s_max = max(cad) / min(page).
    # Using the reciprocals here silently excluded the true scale in testing.
    di_lo = int(math.floor(math.log10(min(dl) / max(pl)) / DBIN_LOG)) - 2
    di_hi = int(math.ceil(math.log10(max(dl) / min(pl)) / DBIN_LOG)) + 2

    n_dr = max(1, int(round(180.0 / dr_step)))
    cons_pool = spread_sample(list(pool or []), cons_k)

    all_T = []
    di = di_lo
    while di <= di_hi:
        s = 10.0 ** (di * DBIN_LOG)
        if 1e-9 < s < 1e9:
            for k in range(n_dr):
                th = math.radians(k * dr_step)
                all_T.extend(pair_fits(fsegs, sig_pdf, eps, s, th, buckets,
                                       tol, win_log, win_dir))
        di += di_step
    if not all_T:
        return []
    mids = [((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0) for a, b in fsegs]
    ref = (sum(m[0] for m in mids) / len(mids),
           sum(m[1] for m in mids) / len(mids)) if mids else None
    clusters = cluster_transforms(all_T, ref, tol, n_bins=verify_top)

    out = []
    for cl in clusters:
        T = cl["T"]
        c = (sum(1 for e in cons_pool
                 if entity_matches(T, e, psegs, grid) is not None)
             if cons_pool else cl["votes"])
        out.append({"T": T, "consensus": c, "votes": cl["votes"],
                    "T_raw": cl["T_raw"]})
    out.sort(key=lambda z: -z["consensus"])
    uniq = []
    for r in out:
        T = r["T"]
        if any(abs(U[0] - T[0]) / U[0] < 5e-4
               and abs(U[1] - T[1]) < 1e-6
               and abs(U[2] - T[2]) < 5.0 * tol
               and abs(U[3] - T[3]) < 5.0 * tol for U in (u["T"] for u in uniq)):
            continue
        uniq.append(r)
        if len(uniq) >= 12:
            break
    return uniq


def run_self_test():
    fails = _self_test()
    if fails:
        print("B2 CORE SELF-TEST: FAILED")
        for f in fails:
            print("   FAIL:", f)
        return False
    print("B2 CORE SELF-TEST: PASSED (14 checks)")
    return True


if __name__ == "__main__":
    sys.exit(0 if run_self_test() else 1)
