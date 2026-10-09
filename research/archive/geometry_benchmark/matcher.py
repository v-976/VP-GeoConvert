"""Decisive geometry matcher: 2-segment RANSAC for a full similarity.

Research tool.

For a candidate correspondence of two DXF segments onto two PDF segments, the
four endpoint correspondences over-determine a 4-DOF similarity
(s, th, tx, ty), so the hypothesis is both solvable and testable. Only the
hypothesis with a large verified inlier count is accepted.

Correspondence is discovered from geometry alone. No file names, no known
coordinates, no hand-picked control points, no text.

PAGE   space: u = horizontal, v = vertical
SURVEY space: X = Northing,    Y = Easting
2D only.
"""

import math
import random
from collections import defaultdict


# --------------------------------------------------------------- utilities

def seg_len(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def ang_diff(x, y):
    """Signed x-y for angles mod pi, in (-pi/2, pi/2]."""
    d = (x - y) % math.pi
    if d > math.pi / 2:
        d -= math.pi
    return d


def fit_similarity(pairs, eps):
    """Least-squares similarity PAGE->SURVEY from (u,v)<->(X,Y) pairs.

    Model:
        X = p*u - eps*q*v + tx
        Y = q*u + eps*p*v + ty
    with p = s*cos(th), q = s*sin(th).

    After centring on the centroids, tx and ty drop out and the problem is
    LINEAR in (p, q). Two rows per observation (one for X, one for Y) give 2n
    equations for 2 unknowns.

    Returns (s, th, tx, ty) or None.
    """
    n = len(pairs)
    if n < 2:
        return None
    e = eps
    cu = sum(p[0] for p in pairs) / n
    cv = sum(p[1] for p in pairs) / n
    CX = sum(p[2] for p in pairs) / n
    CY = sum(p[3] for p in pairs) / n

    # Two rows per observation:
    #   dX =  du*p + (-e*dv)*q
    #   dY = (e*dv)*p +  du*q
    # The normal matrix A^T A is diagonal: sum(du^2 + dv^2) on both diagonals.
    den = 0.0
    Rp = 0.0
    Rq = 0.0
    for u, v, X, Y in pairs:
        du = u - cu
        dv = v - cv
        dX = X - CX
        dY = Y - CY
        den += du * du + dv * dv
        Rp += du * dX + e * dv * dY
        Rq += -e * dv * dX + du * dY
    if abs(den) < 1e-20:
        return None
    p = Rp / den
    q = Rq / den
    s = math.hypot(p, q)
    if s <= 0 or not math.isfinite(s):
        return None
    th = math.atan2(q, p)
    tx = CX - (p * cu - e * q * cv)
    ty = CY - (q * cu + e * p * cv)
    return (s, th, tx, ty)


class Grid:
    """Uniform grid over PAGE-space segments."""

    def __init__(self, segs, cell=6.0):
        self.segs = segs
        self.cell = cell
        self.grid = defaultdict(list)
        for i, (a, b) in enumerate(segs):
            x0 = int(min(a[0], b[0]) // cell)
            x1 = int(max(a[0], b[0]) // cell)
            y0 = int(min(a[1], b[1]) // cell)
            y1 = int(max(a[1], b[1]) // cell)
            for gx in range(x0, x1 + 1):
                for gy in range(y0, y1 + 1):
                    self.grid[(gx, gy)].append(i)

    def near(self, p, r):
        out = set()
        c = self.cell
        gx0 = int((p[0] - r) // c)
        gx1 = int((p[0] + r) // c)
        gy0 = int((p[1] - r) // c)
        gy1 = int((p[1] + r) // c)
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                g = self.grid.get((gx, gy))
                if g:
                    out.update(g)
        return out


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


def apply_T(T, u, v):
    """PAGE (u,v) -> SURVEY (X,Y)."""
    s, th, tx, ty, e = T
    c = math.cos(th)
    sn = math.sin(th)
    return (s * (c * u - e * sn * v) + tx,
            s * (sn * u + e * c * v) + ty)


def inv_apply(T, X, Y):
    """SURVEY (X,Y) -> PAGE (u,v). Exact inverse of apply_T."""
    s, th, tx, ty, e = T
    c = math.cos(th)
    sn = math.sin(th)
    dx = X - tx
    dy = Y - ty
    return ((c * dx + sn * dy) / s,
            (-e * sn * dx + e * c * dy) / s)


def match_one(T, a, b, grid, tol, rel_len=0.015):
    """Best PDF segment for DXF segment (a,b) under T. None if no match.

    |tol| is in PAGE points.
    """
    s, th, tx, ty, e = T
    Ld = seg_len(a, b)
    if Ld <= 0:
        return None
    pa = inv_apply(T, *a)     # SURVEY endpoint -> PAGE
    pb = inv_apply(T, *b)
    cand = grid.near(pa, tol) | grid.near(pb, tol)
    target = Ld / s           # expected PAGE length
    best = None
    for i in cand:
        qa, qb = grid.segs[i]
        Lp = seg_len(qa, qb)
        if abs(Lp - target) > max(tol, rel_len * target):
            continue
        d = pseg_dist(pa, qa, qb) + pseg_dist(pb, qa, qb)
        if best is None or d < best[0]:
            best = (d, i)
    if best is None or best[0] > 2.0 * tol:
        return None
    return (grid.segs[best[1]], best[0] / 2.0)


def ransac(dx_segs, pdf_segs, eps, tol=1.5, n_dxf=26, n_pdf=44,
           probe=10, log_ratio_tol=0.004, log_bin=0.002, max_fit=60000):
    """Exhaustive 2-segment search with a length-ratio prefilter.

    A similarity preserves segment length ratios, so a DXF segment pair can
    only match a PDF segment pair whose length ratio agrees. This prefilter
    cuts the combinatorial space by orders of magnitude WITHOUT assuming any
    rotation, scale or position. Only the hypothesis with the largest verified
    inlier count is returned.
    """
    grid = Grid(pdf_segs)
    dxs = sorted(dx_segs, key=lambda s: -seg_len(*s))[:n_dxf]
    pfs = sorted(pdf_segs, key=lambda s: -seg_len(*s))[:n_pdf]
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
                lr = -lr
                jj, mm = m, j
            else:
                jj, mm = j, m
            buckets[int(round(lr / log_bin))].append((jj, mm))

    best = (0, None)
    tried = 0
    fitted = 0
    span = int(math.ceil(log_ratio_tol / log_bin)) + 1
    for i in range(len(dxs)):
        if Ld[i] <= 0:
            continue
        for k in range(i + 1, len(dxs)):
            if Ld[k] <= 0:
                continue
            lr = math.log(Ld[i] / Ld[k])
            if lr < 0:
                lr = -lr
                ii, kk = k, i
            else:
                ii, kk = i, k
            centre = int(round(lr / log_bin))
            cand = []
            for b in range(centre - span, centre + span + 1):
                cand.extend(buckets.get(b, ()))
            if not cand:
                continue
            sa = dxs[ii]
            sb = dxs[kk]
            for jj, mm in cand:
                tried += 1
                p = pfs[jj]
                q = pfs[mm]
                fit = fit_similarity(
                    [(p[0][0], p[0][1], sa[0][0], sa[0][1]),
                     (p[1][0], p[1][1], sa[1][0], sa[1][1]),
                     (q[0][0], q[0][1], sb[0][0], sb[0][1]),
                     (q[1][0], q[1][1], sb[1][0], sb[1][1])], eps)
                if fit is None:
                    continue
                s_, th_, tx_, ty_ = fit
                if not (1e-6 < s_ < 1e4):
                    continue
                T_ = (s_, th_, tx_, ty_, eps)
                # the two-point fit must reproduce both segments
                ok = True
                for segDX, segPDF in ((sa, p), (sb, q)):
                    for k in (0, 1):
                        uu = inv_apply(T_, segDX[k][0], segDX[k][1])
                        if math.hypot(uu[0] - segPDF[k][0],
                                      uu[1] - segPDF[k][1]) > 3.0 * tol:
                            ok = False
                            break
                    if not ok:
                        break
                if not ok:
                    continue
                fitted += 1
                hits = 0
                for ss in dxs[:probe]:
                    if match_one(T_, ss[0], ss[1], grid, tol) is not None:
                        hits += 1
                if hits > best[0]:
                    best = (hits, T_)
                    if hits >= probe:
                        return {"T": T_, "hits": hits, "tried": tried,
                                "fitted": fitted, "early": True}
            if fitted >= max_fit:
                break
        if fitted >= max_fit:
            break
    return {"T": best[1], "hits": best[0], "tried": tried,
            "fitted": fitted, "early": False}
