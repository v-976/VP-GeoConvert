"""Final geometry benchmark: transform recovery, CONTROL/CHECK residuals,
affine diagnostic, and PDFium float precision.

Research tool. Read-only with respect to the DXF/PDF source data.

Method summary
--------------
1. theta (rotation) candidates come from orientation-histogram cross-correlation
   between DXF and PAGE straight segments. Cheap and correspondence-free.
2. For each (eps, theta) hypothesis, one segment correspondence fixes
   (s, tx, ty) exactly, so the remaining search is over segment pairs only.
3. A hypothesis is accepted only if it produces a large number of verified
   segment inliers over the whole DXF set.
4. Inliers are split into CONTROL (used to refit) and independent CHECK.
5. Affine is computed only as a diagnostic comparison after Helmert.

PAGE   space: u = horizontal, v = vertical
SURVEY space: X = Northing,    Y = Easting
Strictly 2D. No Z anywhere.
"""

import math
from collections import defaultdict

import dxf_reader as D
import pdf_reader as P
import matcher as M
import transform_est as T

DXF = r'benchmark\TKA_Tunnelitie\TKA_Tunnelitie_Suunnitelmakartta_MAS!BG.dxf'
ART = r'research\results\archive\TKA_Tunnelitie'

FILES = [("3D-Win", "3dwin_probe.txt"),
         ("ProgeCAD Export", "proge_export_probe.txt"),
         ("ProgeCAD Print", "proge_print_probe.txt")]


# ------------------------------------------------------------------ helpers

def dxf_segments(ents):
    out = []
    for e in ents:
        if e.etype not in ("LINE", "LWPOLYLINE"):
            continue
        p = e.pts
        if len(p) < 2:
            continue
        for i in range(len(p) - 1):
            out.append((p[i], p[i + 1]))
        if e.closed and p[0] != p[-1]:
            out.append((p[-1], p[0]))
    return out


def pdf_segments(paths):
    out = []
    for p in paths:
        out.extend(P.segments_of(p))
    return out


def solve_from_pair(a, b, p, q, eps, th):
    """With eps and th fixed, one segment correspondence gives (s, tx, ty)."""
    La = M.seg_len(a, b)
    Lp = M.seg_len(p, q)
    if La <= 0 or Lp <= 0:
        return None
    s = Lp / La
    if not (1e-6 < s < 1e4):
        return None
    c = math.cos(th)
    sn = math.sin(th)
    # X = s*(c*u - e*sn*v) + tx   ->   tx = X - s*(c*u - e*sn*v)
    tx = a[0] - s * (c * p[0] - eps * sn * p[1])
    ty = a[1] - s * (sn * p[0] + eps * c * p[1])
    # verify the second endpoint agrees (guards against wrong pairing)
    Xb = s * (c * q[0] - eps * sn * q[1]) + tx
    Yb = s * (sn * q[0] + eps * c * q[1]) + ty
    if math.hypot(Xb - b[0], Yb - b[1]) > 0.02 * La:
        return None
    return (s, th, tx, ty, eps)


def hypotheses(dx, ps, eps, th_deg, ang_tol_deg=0.35, n_dxf=60, n_pdf_dur=None):
    """Yield candidate transforms from direction-consistent segment pairs."""
    th = math.radians(th_deg)
    tol = math.radians(ang_tol_deg)
    dxs = sorted(dx, key=lambda s: -M.seg_len(*s))[:n_dxf]

    # PAGE direction that the DXF direction psi maps to under this hypothesis
    # eps=+1 : psi = th + phi -> phi = psi - th
    # eps=-1 : psi = phi - th -> phi = psi + th
    def page_dir(psi):
        return (psi - th) if eps > 0 else (psi + th)

    buckets = defaultdict(list)
    for a, b in ps:
        L = M.seg_len(a, b)
        if L <= 0:
            continue
        phi = math.atan2(b[1] - a[1], b[0] - a[0])
        k = int(round(math.degrees(page_dir(phi)) / (ang_tol_deg * 2)))
        buckets[k].append((a, b, L))

    out = []
    for a, b in dxs:
        La = M.seg_len(a, b)
        if La <= 0:
            continue
        psi = math.atan2(b[1] - a[1], b[0] - a[0])
        k = int(round(math.degrees(psi) / (ang_tol_deg * 2)))
        cand = []
        for kk in (k - 1, k, k + 1):
            cand.extend(buckets.get(kk, ()))
        for (p, q, Lp) in cand:
            # length-ratio sanity: only consider if the ratio is plausible
            r = Lp / La
            if not (1e-5 < r < 1e4):
                continue
            t = solve_from_pair(a, b, p, q, eps, th)
            if t is not None:
                out.append(t)
    return out


def inliers_for(Tt, dx, grid, tol, want=None):
    res = []
    for a, b in dx:
        m = M.match_one(Tt, a, b, grid, tol)
        if m is not None:
            res.append(((a, b), m[0], m[1]))
        if want and len(res) >= want:
            break
    return res


def probe_score(Tt, dx_top, grid, tol):
    hits = 0
    for a, b in dx_top:
        if M.match_one(Tt, a, b, grid, tol) is not None:
            hits += 1
    return hits


def fit_affine(pairs):
    """Least-squares affine PAGE->SURVEY. Returns (a,b,c,d,e,f,tx,ty)."""
    n = len(pairs)
    cu = sum(p[0] for p in pairs) / n
    cv = sum(p[1] for p in pairs) / n
    CX = sum(p[2] for p in pairs) / n
    CY = sum(p[3] for p in pairs) / n
    Suu = Suv = Svv = Rux = Rvx = Ruy = Rvy = 0.0
    for u, v, X, Y in pairs:
        du = u - cu
        dv = v - cv
        dX = X - CX
        dY = Y - CY
        Suu += du * du
        Suv += du * dv
        Svv += dv * dv
        Rux += du * dX
        Rvx += dv * dX
        Ruy += du * dY
        Rvy += dv * dY
    det = Suu * Svv - Suv * Suv
    if abs(det) < 1e-20:
        return None
    # inverse of [[Suu, Suv], [Suv, Svv]] is 1/det * [[Svv, -Suv], [-Suv, Suu]]
    a = (Svv * Rux - Suv * Rvx) / det
    b = (Suu * Rvx - Suv * Rux) / det
    c = (Svv * Ruy - Suv * Rvy) / det
    d = (Suu * Rvy - Suv * Ruy) / det
    tx = CX - (a * cu + b * cv)
    ty = CY - (c * cu + d * cv)
    return (a, b, c, d, tx, ty)


def apply_affine(A, u, v):
    a, b, c, d, tx, ty = A
    return (a * u + b * v + tx, c * u + d * v + ty)


def stats(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    rms = math.sqrt(sum(x * x for x in s) / n)
    med = s[n // 2]
    p95 = s[min(n - 1, int(round(0.95 * (n - 1))))]
    return {"n": n, "rms": rms, "median": med, "p95": p95, "max": s[-1]}