"""PAGE (u,v) -> SURVEY (X,Y) transform estimation by 1D profile registration.

Research tool. Robust, correspondence-free.

Idea
----
The drawings have a dominant edge direction. Under a similarity, a DXF
direction maps to a specific PAGE direction. Projecting every vertex onto
those two directions gives two 1D point profiles. A uniform scale plus
translation turns one profile into the other, so scale and offset can be
recovered by histogram cross-correlation - no point correspondence needed.

This is deliberately insensitive to fragmentation: extra PDF vertices only
change profile density, not profile extent or shape.

PAGE   space: u = horizontal, v = vertical
SURVEY space: X = Northing,    Y = Easting
2D only.
"""

import math

import pdf_reader as P


# ---------------------------------------------------------------- profiles

def dominant_direction(segs, bucket_deg=0.5):
    """Return (radians mod pi, length_share) of the dominant edge direction."""
    hist = {}
    for a, b in segs:
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L <= 0:
            continue
        ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0
        k = int(ang / bucket_deg)
        hist[k] = hist.get(k, 0.0) + L
    if not hist:
        return None, 0.0
    k = max(hist, key=hist.get)
    return math.radians(k * bucket_deg), hist[k]


def project(points, ang):
    ca, sa = math.cos(ang), math.sin(ang)
    return [p[0] * ca + p[1] * sa for p in points]


def hist_match(src, dst, bins=400, s_lo=0.05, s_hi=50.0, steps=500,
               max_src=2500):
    """Find s, c with  src*s + c ~= dst  by histogram cross-correlation.

    Both lists are centred first, so c is recovered afterwards from the means.
    src (PAGE projection) is subsampled for speed; dst (SURVEY projection) is
    kept whole because it is small.

    Returns (s, c, score) or None.
    """
    if len(src) < 8 or len(dst) < 8:
        return None
    mu_s = sum(src) / len(src)
    mu_d = sum(dst) / len(dst)
    src_c = [t - mu_s for t in src]
    dst_c = [t - mu_d for t in dst]

    lo, hi = min(dst_c), max(dst_c)
    span_d = hi - lo
    if span_d <= 0:
        return None
    span_s = max(src_c) - min(src_c)
    if span_s <= 0:
        return None

    # subsample the PAGE profile uniformly
    step = max(1, len(src_c) // max_src)
    src_c = src_c[::step]

    w = span_d / bins
    dn = [0.0] * bins
    for t in dst_c:
        i = int((t - lo) / w)
        if 0 <= i < bins:
            dn[i] += 1.0
    nzd = sum(1 for x in dn if x > 0)
    if nzd == 0:
        return None
    dn = [x / nzd for x in dn]

    # scale must map the PAGE span onto roughly the SURVEY span
    base = span_d / span_s
    s_lo = max(s_lo, base * 0.80)
    s_hi = min(s_hi, base * 1.25)

    def score_at(s):
        sh = [0.0] * bins
        for t in src_c:
            j = int((s * t - lo) / w)
            if 0 <= j < bins:
                sh[j] += 1.0
        nzs = sum(1 for x in sh if x > 0)
        if nzs == 0:
            return None
        acc = 0.0
        for i in range(bins):
            if sh[i]:
                acc += (sh[i] / nzs) * dn[i]
        return acc

    best = None
    for i in range(steps):
        lg = math.log(s_lo) + (math.log(s_hi) - math.log(s_lo)) * i / (steps - 1.0)
        s = math.exp(lg)
        sc = score_at(s)
        if sc is None:
            continue
        if best is None or sc > best[1]:
            best = (s, sc)
    if best is None:
        return None

    # local refinement
    s0 = best[0]
    lo2, hi2 = s0 * 0.995, s0 * 1.005
    for i in range(200):
        s = lo2 + (hi2 - lo2) * i / 199.0
        sc = score_at(s)
        if sc is not None and sc > best[1]:
            best = (s, sc)
    return (best[0], mu_d - best[0] * mu_s, best[1])


def estimate_from_profiles(dx_pts, pdf_pts, dxf_dominant_deg, eps, th_deg):
    """Estimate (s, tx, ty) for a given (eps, th) hypothesis.

    Uses two projections: along the dominant edge direction, and perpendicular
    to it. The perpendicular offset follows from the secondary profile.
    """
    th = math.radians(th_deg)
    ds = math.radians(dxf_dominant_deg)

    # PAGE direction that the DXF dominant direction maps to
    if eps > 0:
        dp = ds - th          # psi = th + phi  =>  phi = psi - th
    else:
        dp = ds + th          # psi = phi - th  =>  phi = psi + th

    p_sur = project(dx_pts, ds)
    p_pag = project(pdf_pts, dp)
    r1 = hist_match(p_pag, p_sur)
    if r1 is None:
        return None
    s, c1, score = r1

    # perpendicular direction
    ds2 = ds + math.pi / 2.0
    dp2 = dp + math.pi / 2.0
    q_sur = project(dx_pts, ds2)
    q_pag = project(pdf_pts, dp2)
    r2 = hist_match(q_pag, q_sur)
    if r2 is None:
        return None
    s2, c2, score2 = r2
    # keep the scale from the dominant direction, report the other for diagnosis
    return {"s_along": s, "c_along": c1, "score_along": score,
            "s_perp": s2, "c_perp": c2, "score_perp": score2,
            "ds": ds, "dp": dp}