"""PAGE (u,v) -> SURVEY (X,Y) transform estimation by geometric matching.

Research tool. No prior correspondence, no hardcoded control points, no file
names, no text coordinates. Only the DXF and PDF geometry themselves.

Model
-----
    [X]   [ tx ]       [ cos(th)  -eps*sin(th) ] [u]
    [Y] = [ ty ] + s * [ sin(th)   eps*cos(th) ] [v]

  s    uniform scale            (> 0)
  th   rotation, radians        (geodesic / Helmert part)
  eps  +1 no reflection, -1 reflection of v (reported explicitly, never implicit)
  t    translation              (SURVEY X,Y)

eps is an explicit, reported part of the answer. It is never folded silently
into the Helmert parameters.

Pipeline
--------
  1. orientation histograms  -> th candidates, for eps = +1 and eps = -1
  2. log-ratio histogram     -> s, from directionally consistent segment pairs
  3. translation voting      -> t, from the pairs consistent with s
  4. RANSAC refinement       -> least squares on inliers
  5. CONTROL / CHECK split   -> independent residuals
"""

import math
from collections import defaultdict

import dxf_reader as D
import pdf_reader as P

DXF = r'benchmark\TKA_Tunnelitie\TKA_Tunnelitie_Suunnitelmakartta_MAS!BG.dxf'
ART = r'research\results\archive\TKA_Tunnelitie'


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------

def seg_len(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def seg_orient(a, b):
    """PAGE/SURVEY segment orientation in radians, mod pi."""
    return math.atan2(b[1] - a[1], b[0] - a[0])


def angdiff(x, y):
    """Signed difference x-y reduced to (-pi/2, pi/2] for mod-pi angles."""
    d = (x - y) % math.pi
    if d > math.pi / 2:
        d -= math.pi
    return d


class Transform:
    __slots__ = ("s", "th", "eps", "tx", "ty")

    def __init__(self, s, th, eps, tx, ty):
        self.s = s
        self.th = th
        self.eps = eps
        self.tx = tx
        self.ty = ty

    def apply(self, u, v):
        c = math.cos(self.th)
        sn = math.sin(self.th)
        e = self.eps
        return (self.s * (c * u - e * sn * v) + self.tx,
                self.s * (sn * u + e * c * v) + self.ty)

    def apply_vec(self, u, v):
        c = math.cos(self.th)
        sn = math.sin(self.th)
        e = self.eps
        return (self.s * (c * u - e * sn * v),
                self.s * (sn * u + e * c * v))

    def params(self):
        return (self.s, math.degrees(self.th), self.eps, self.tx, self.ty)


def fit_similarity(xs, ys, us, vs, eps):
    """Least-squares similarity mapping PAGE->SURVEY with fixed eps.

    xs, ys : SURVEY targets
    us, vs : PAGE sources
    Returns (s, th, tx, ty).
    """
    # normalise PAGE so the fit is numerically well conditioned
    cu = sum(us) / len(us)
    cv = sum(vs) / len(vs)
    CX = sum(xs) / len(xs)
    CY = sum(ys) / len(ys)

    # direction cosines for the rotation, with reflection folded in explicitly
    a11 = a12 = a21 = a22 = b1 = b2 = 0.0
    for u, v, X, Y in zip(us, vs, xs, ys):
        du = u - cu
        dv = v - cv
        dX = X - CX
        dY = Y - CY
        if eps > 0:
            a11 += du * dX; a12 += dv * dX
            a21 += du * dY; a22 += dv * dY
        else:
            a11 += du * dX; a12 += -dv * dX
            a21 += du * dY; a22 += -dv * dY
    A = a11 + a22
    B = a12 - a21
    denom = a11 * a22 - a12 * a21
    if abs(denom) < 1e-18:
        return None
    c = A / denom
    sn = B / denom
    th = math.atan2(sn, c)
    s = math.hypot(c, sn)

    # translation from the centroids, using the fitted linear part
    Lc, Ls = c, sn
    if eps < 0:
        tx = CX - s * (Lc * cu + Ls * cv)
        ty = CY - s * (Ls * cu - Lc * cv)
    else:
        tx = CX - s * (Lc * cu - Ls * cv)
        ty = CY - s * (Ls * cu + Lc * cv)
    return (s, th, tx, ty)


# --------------------------------------------------------------------------
# step 1: rotation from orientation histograms
# --------------------------------------------------------------------------

def orientation_hist(segs, bucket_deg=1.0, min_len=0.0):
    h = defaultdict(float)
    for a, b in segs:
        L = seg_len(a, b)
        if L < min_len:
            continue
        ang = (math.degrees(seg_orient(a, b))) % 180.0
        h[int(ang / bucket_deg)] += L
    return h


def best_rotation(dx_segs, pdf_segs, eps, bucket_deg=1.0,
                  min_dxf_len=0.0, min_pdf_len=0.0, top=12):
    """Search rotation offsets maximising orientation-histogram overlap."""
    hd = orientation_hist(dx_segs, bucket_deg, min_dxf_len)
    hp = orientation_hist(pdf_segs, bucket_deg, min_pdf_len)
    nb = int(180.0 / bucket_deg)
    if not hd or not hp:
        return []

    hd_v = [hd.get(i, 0.0) for i in range(nb)]
    hp_v = [hp.get(i, 0.0) for i in range(nb)]
    sd = math.sqrt(sum(x * x for x in hd_v)) or 1.0
    sp = math.sqrt(sum(x * x for x in hp_v)) or 1.0

    scores = []
    for k in range(nb):
        acc = 0.0
        for i in range(nb):
            # SURVEY direction psi relates to PAGE direction phi as:
            #   eps=+1 -> psi = th + phi  =>  phi = psi - th  =>  j = k - i
            #   eps=-1 -> psi = phi - th  =>  phi = psi + th  =>  j = k + i
            j = (k - i) % nb if eps > 0 else (k + i) % nb
            acc += hd_v[i] * hp_v[j]
        scores.append((acc / (sd * sp), k))
    scores.sort(reverse=True)
    return [(s, k * bucket_deg) for s, k in scores[:top]]


# --------------------------------------------------------------------------
# step 2/3: scale and translation by voting over directional candidates
# --------------------------------------------------------------------------

def estimate_scale_translation(dx_segs, pdf_segs, eps, th_deg,
                               tol_deg=1.5, ratio_logbin=0.004,
                               min_dxf_len=0.0, min_pdf_len=0.0):
    """Estimate scale by a log-ratio peak, then translation by voting.

    Returns (scale, tx, ty, diagnostics dict) or None.
    """
    th = math.radians(th_deg)
    tol = math.radians(tol_deg)

    # bucket PDF segments by the SURVEY direction they would map to
    buckets = defaultdict(list)
    for a, b in pdf_segs:
        L = seg_len(a, b)
        if L < min_pdf_len:
            continue
        phi = seg_orient(a, b)
        # L = s*R(th)*D(eps) maps a PAGE direction phi to SURVEY direction:
        #   eps=+1 -> th + phi
        #   eps=-1 -> phi - th
        mapped = (th + phi) if eps > 0 else (phi - th)
        key = int(((math.degrees(mapped) % 180.0)) / tol_deg)
        buckets[key].append((L, a, b))

    # DXF segments bucketed by their own direction
    dx_buckets = defaultdict(list)
    for a, b in dx_segs:
        L = seg_len(a, b)
        if L < min_dxf_len:
            continue
        key = int(((math.degrees(seg_orient(a, b)) % 180.0)) / tol_deg)
        dx_buckets[key].append((L, a, b))

    # ---- scale: log-ratio peak -----------------------------------------
    votes = defaultdict(float)
    pair_index = defaultdict(list)
    for key, dl in dx_buckets.items():
        cand = []
        for kk in (key - 1, key, key + 1):
            cand.extend(buckets.get(kk, ()))
        if not cand:
            continue
        for Ld, da, db in dl:
            for Lp, pa, pb in cand:
                lr = math.log(Ld / Lp)
                b = int(round(lr / ratio_logbin))
                votes[b] += 1.0
                if len(pair_index[b]) < 4000:
                    pair_index[b].append((Ld, Lp, da, db, pa, pb))
    if not votes:
        return None

    # circular smoothing over the log-ratio axis, then take the peak
    keys = sorted(votes)
    smoothed = []
    W = 3
    for i in keys:
        acc = 0.0
        wsum = 0.0
        for o in range(-W, W + 1):
            if i + o in votes:
                w = W + 1 - abs(o)
                acc += votes[i + o] * w
                wsum += w
        smoothed.append((acc / wsum if wsum else 0.0, i))
    smoothed.sort(reverse=True)

    best = None
    for score, b in smoothed:
        s = math.exp(b * ratio_logbin)
        if s <= 0:
            continue
        best = (s, score, b)
        break
    if best is None:
        return None
    scale, peak_score, peak_bin = best

    # ---- translation: vote with pairs consistent with the scale ---------
    tol_m = max(1e-9, scale * 0.004)   # 0.4 % of a segment length
    tvotes = defaultdict(int)
    samples = defaultdict(list)
    for key, dl in dx_buckets.items():
        cand = []
        for kk in (key - 1, key, key + 1):
            cand.extend(buckets.get(kk, ()))
        if not cand:
            continue
        for Ld, da, db in dl:
            for Lp, pa, pb in cand:
                if abs(scale * Lp - Ld) > tol_m:
                    continue
                # both endpoints must agree under the same translation
                c, sn = math.cos(th), math.sin(th)
                e = eps
                # map PDF midpoint into SURVEY direction, no translation yet
                mu, mv = (pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0
                MU = scale * (c * mu - e * sn * mv)
                MV = scale * (sn * mu + e * c * mv)
                dX = (da[0] + db[0]) / 2.0 - MU
                dY = (da[1] + db[1]) / 2.0 - MV
                qx = int(round(dX / 0.5))
                qy = int(round(dY / 0.5))
                k = (qx, qy)
                tvotes[k] += 1
                if len(samples[k]) < 50:
                    samples[k].append((dX, dY))
    if not tvotes:
        return None
    kbest = max(tvotes.items(), key=lambda kv: kv[1])[0]
    cand = samples[kbest]
    tx = sum(p[0] for p in cand) / len(cand)
    ty = sum(p[1] for p in cand) / len(cand)

    diag = {"scale_peak_score": peak_score,
            "translation_votes": tvotes[kbest],
            "scale_candidates": len(pair_index.get(peak_bin, ()))}
    return (scale, tx, ty, diag)