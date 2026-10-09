"""Direction-restricted 1D profile matching.

Research tool. Matching only segments that share the dominant direction removes
the noise contributed by hatch fills and curve flattening, which dominate the
full point cloud but carry no orientation information.
"""

import math
from collections import defaultdict

import profile_est as PR


def directional_profiles(dx_segs, pdf_segs, ds, dp, band_deg=0.75,
                         min_len_dxf=0.0, min_len_pdf=0.0):
    """1D projections of direction-matched segment endpoints.

    ds : DXF dominant direction (rad, mod pi)
    dp : PAGE direction that ds maps to under the hypothesis
    """
    band = math.radians(band_deg)
    ca_s, sa_s = math.cos(ds), math.sin(ds)
    ca_p, sa_p = math.cos(dp), math.sin(dp)

    A = []   # SURVEY projections along ds
    for a, b in dx_segs:
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L < min_len_dxf:
            continue
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        if abs(ang_diff(ang, ds)) > band:
            continue
        A.append(a[0] * ca_s + a[1] * sa_s)
        A.append(b[0] * ca_s + b[1] * sa_s)

    B = []   # PAGE projections along dp
    for a, b in pdf_segs:
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L < min_len_pdf:
            continue
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        if abs(ang_diff(ang, dp)) > band:
            continue
        B.append(a[0] * ca_p + a[1] * sa_p)
        B.append(b[0] * ca_p + b[1] * sa_p)
    return A, B


def ang_diff(x, y):
    d = (x - y) % math.pi
    if d > math.pi / 2:
        d -= math.pi
    return d


def estimate(dx_segs, pdf_segs, ds, dp, band_deg=0.75,
             min_len_dxf=0.0, min_len_pdf=0.0):
    """Two-axis estimate. Returns dict with scales along both axes."""
    A, B = directional_profiles(dx_segs, pdf_segs, ds, dp, band_deg,
                                min_len_dxf, min_len_pdf)
    if len(A) < 8 or len(B) < 8:
        return None
    r1 = PR.hist_match(B, A)
    ds2 = ds + math.pi / 2.0
    dp2 = dp + math.pi / 2.0
    A2, B2 = directional_profiles(dx_segs, pdf_segs, ds2, dp2, band_deg * 2.0,
                                   min_len_dxf, min_len_pdf)
    if len(A2) < 8 or len(B2) < 8:
        r2 = None
    else:
        r2 = PR.hist_match(B2, A2)
    if r1 is None:
        return None
    return {"s_along": r1[0], "c_along": r1[1], "score_along": r1[2],
            "n_along": len(A),
            "s_perp": (r2[0] if r2 else None),
            "c_perp": (r2[1] if r2 else None),
            "score_perp": (r2[2] if r2 else None),
            "ds": ds, "dp": dp}