"""Canonical 2D similarity (Helmert) estimation and application.

One implementation only. There is deliberately NO affine variant here: a
similarity is never silently replaced by a more flexible model, and affine was
never needed to select a correspondence in any completed benchmark stage.

Reflection is an explicit integer `eps` (+1 direct, -1 mirrored), never folded
into the rotation.

Coordinate domains
------------------
Fits consume (u, v, cad_x, cad_y) tuples: PAGE in, CAD out.
The reverse direction is available through `SimilarityTransform.inverse_apply`
and is used to place DXF features into PAGE space for comparison.
"""

import math
from typing import List, Optional, Sequence, Tuple

from .models import SimilarityTransform

# A correspondence is a 4-tuple (u, v, cad_x, cad_y).
Correspondence = Tuple[float, float, float, float]


def fit_similarity(pairs: Sequence[Correspondence], eps: int
                   ) -> Optional[SimilarityTransform]:
    """Least-squares similarity from PAGE -> CAD correspondences.

    Model, with p = s*cos(th), q = s*sin(th):

        cad_x =  p*u - eps*q*v + tx
        cad_y =  q*u + eps*p*v + ty

    After centring on the centroids, tx and ty drop out and the problem is
    LINEAR in (p, q). Each observation supplies two rows, giving 2n equations
    for two unknowns, with a diagonal normal matrix.

    Returns None when the problem is degenerate (fewer than two pairs, or all
    pairs coincident).
    """
    n = len(pairs)
    if n < 2:
        return None
    e = eps
    cu = sum(p[0] for p in pairs) / n
    cv = sum(p[1] for p in pairs) / n
    cx = sum(p[2] for p in pairs) / n
    cy = sum(p[3] for p in pairs) / n

    den = 0.0
    rp = 0.0
    rq = 0.0
    for u, v, x, y in pairs:
        du = u - cu
        dv = v - cv
        dx = x - cx
        dy = y - cy
        den += du * du + dv * dv
        rp += du * dx + e * dv * dy
        rq += -e * dv * dx + du * dy
    if abs(den) < 1e-30:
        return None

    p = rp / den
    q = rq / den
    scale = math.hypot(p, q)
    if scale <= 0 or not math.isfinite(scale):
        return None
    return SimilarityTransform(scale, math.atan2(q, p),
                               cx - (p * cu - e * q * cv),
                               cy - (q * cu + e * p * cv),
                               eps)


def angle_difference_rad(a: float, b: float) -> float:
    """Signed difference a - b for angles modulo pi, in (-pi/2, pi/2].

    Line directions are undirected, so a difference of pi means "parallel".
    """
    d = (a - b) % math.pi
    if d > math.pi / 2:
        d -= math.pi
    return d


def segment_length(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def segment_direction_rad(a: Tuple[float, float],
                          b: Tuple[float, float]) -> float:
    return math.atan2(b[1] - a[1], b[0] - a[0])


def segment_perpendicular_distance(p: Tuple[float, float],
                                   a: Tuple[float, float],
                                   b: Tuple[float, float]) -> float:
    """Distance from p to the infinite line through a and b."""
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2
    if t < 0:
        t = 0.0
    elif t > 1:
        t = 1.0
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def interpolate_point(a: Tuple[float, float], b: Tuple[float, float],
                      t: float) -> Tuple[float, float]:
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))


def project_on_segment(p: Tuple[float, float],
                       a: Tuple[float, float],
                       b: Tuple[float, float]) -> Tuple[float, float]:
    """Closest point to p on segment a-b, with the parameter clamped to [0, 1]."""
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return a
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    return (a[0] + t * dx, a[1] + t * dy)


def transform_reproduces(transform: SimilarityTransform,
                         cad_segment: Tuple[Tuple[float, float],
                                            Tuple[float, float]],
                         page_segment: Tuple[Tuple[float, float],
                                             Tuple[float, float]],
                         tolerance: float,
                         orientations: Sequence[bool] = (False, True)
                         ) -> bool:
    """True when the transform maps the CAD segment onto the PAGE segment.

    `tolerance` is in PAGE points, which is the only space in which a
    geometric tolerance is defined for this comparison.

    Both endpoint orderings are tried because PDF vectorisation may reverse a
    path while leaving the geometry identical.
    """
    inv = transform.inverse_point
    for flip in orientations:
        p0, p1 = (page_segment[1], page_segment[0]) if flip else page_segment
        for k, target in ((0, p0), (1, p1)):
            got = inv(cad_segment[k])
            if math.hypot(got[0] - target[0], got[1] - target[1]) > tolerance:
                break
        else:
            return True
    return False
