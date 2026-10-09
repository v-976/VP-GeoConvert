"""PAGE-space segment index and the canonical segment correspondence test.

The correspondence criterion lives here and is used by every other module.
Its two defining properties were both forced by measurement, not by taste:

  * the tolerance is ENFORCED. A Set B1 defect had a nearest-point helper that
    returned the closest candidate found in adjacent grid cells WITHOUT testing
    the distance, so a 100 m translation error still "matched" hundreds of
    entities. `nearest_directed_deviation` returns None when nothing is within
    tolerance, and callers cannot mistake that for a hit.

  * proximity alone is not sufficient; direction must agree. A Set B1
    calibration showed that proximity alone is satisfied by points that are
    numerically close to unrelated geometry. Direction compatibility (modulo
    pi, because lines are undirected) is therefore part of the test.

Sampling along the segment rather than demanding one PDF segment cover a whole
CAD segment is what allows fragmented PDF geometry to match without weakening
the tolerance: a long CAD line split into collinear fragments still matches,
while a coincidental nearby line does not.

Coordinate domains
------------------
`SegmentIndex` is built over PAGE segments only. CAD coordinates enter solely
through a SimilarityTransform, which maps them into PAGE space before any
distance is measured. No distance is ever computed across domains.
"""

import math
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .models import GeometryEntity, SimilarityTransform
from .similarity import (angle_difference_rad, interpolate_point,
                         project_on_segment, segment_direction_rad,
                         segment_length)

Point = Tuple[float, float]
Segment = Tuple[Point, Point]

DEFAULT_CELL_SIZE = 6.0        # PAGE points; matches the historic grids
DEFAULT_SAMPLES = 11
DEFAULT_DIRECTION_TOLERANCE_DEG = 1.0


class SegmentIndex:
    """Uniform grid over PAGE segments for nearest-neighbour queries.

    Cell size is in PAGE points and must be at least as large as the largest
    tolerance the index will be queried with, otherwise a query can miss a
    segment that lies just outside the scanned cells. `query` widens the scan
    by one extra cell on each side to make that safe regardless.
    """

    def __init__(self, segments: Sequence[Segment],
                 cell_size: float = DEFAULT_CELL_SIZE) -> None:
        if cell_size <= 0:
            raise ValueError("cell_size must be positive")
        self.segments: List[Segment] = list(segments)
        self.cell_size = cell_size
        self._grid: Dict[Tuple[int, int], List[int]] = defaultdict(list)
        for i, (a, b) in enumerate(self.segments):
            x0 = int(math.floor(min(a[0], b[0]) / cell_size))
            x1 = int(math.floor(max(a[0], b[0]) / cell_size))
            y0 = int(math.floor(min(a[1], b[1]) / cell_size))
            y1 = int(math.floor(max(a[1], b[1]) / cell_size))
            for gx in range(x0, x1 + 1):
                for gy in range(y0, y1 + 1):
                    self._grid[(gx, gy)].append(i)

    def __len__(self) -> int:
        return len(self.segments)

    def candidates(self, point: Point, radius: float) -> List[int]:
        c = self.cell_size
        gx0 = int(math.floor((point[0] - radius) / c)) - 1
        gx1 = int(math.floor((point[0] + radius) / c)) + 1
        gy0 = int(math.floor((point[1] - radius) / c)) - 1
        gy1 = int(math.floor((point[1] + radius) / c)) + 1
        found: Dict[int, None] = {}
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                for i in self._grid.get((gx, gy), ()):
                    found[i] = None
        return list(found)

    def nearest_directed_deviation(self, point: Point, direction_rad: float,
                                   direction_tolerance_rad: float,
                                   tolerance: float
                                   ) -> Optional[Tuple[float, int]]:
        """Smallest deviation to a direction-compatible PAGE segment.

        Returns (deviation, segment_index), or None when no PAGE segment is
        both within `tolerance` and direction compatible. The None is the
        contract: it means "no match", not "nearest found somewhere".
        """
        best: Optional[Tuple[float, int]] = None
        for i in self.candidates(point, tolerance):
            a, b = self.segments[i]
            if segment_length(a, b) <= 0:
                continue
            d = angle_difference_rad(segment_direction_rad(a, b),
                                     direction_rad)
            if abs(d) > direction_tolerance_rad:
                continue
            proj = project_on_segment(point, a, b)
            dev = math.hypot(point[0] - proj[0], point[1] - proj[1])
            if dev > tolerance:
                continue
            if best is None or dev < best[0]:
                best = (dev, i)
        return best

    def nearest_deviation_any_direction(self, point: Point, tolerance: float
                                        ) -> Optional[float]:
        """Direction-agnostic proximity only.

        Provided for shortlisting and for selectivity probes. It is NEVER used
        to declare a match: proximity alone is not sufficient evidence, which
        was demonstrated on real data.
        """
        best: Optional[float] = None
        for i in self.candidates(point, tolerance):
            a, b = self.segments[i]
            proj = project_on_segment(point, a, b)
            dev = math.hypot(point[0] - proj[0], point[1] - proj[1])
            if dev <= tolerance and (best is None or dev < best):
                best = dev
        return best


class SegmentMatch:
    """Result of testing one CAD segment against the PAGE index."""

    __slots__ = ("matched", "mean_deviation", "max_deviation", "page_indices")

    def __init__(self, matched: bool, mean_deviation: Optional[float] = None,
                 max_deviation: Optional[float] = None,
                 page_indices: Optional[List[int]] = None) -> None:
        self.matched = matched
        self.mean_deviation = mean_deviation
        self.max_deviation = max_deviation
        self.page_indices = page_indices or []


def match_cad_segment(segment: Segment, transform: SimilarityTransform,
                      index: SegmentIndex, tolerance: float,
                      samples: int = DEFAULT_SAMPLES,
                      direction_tolerance_deg: float
                      = DEFAULT_DIRECTION_TOLERANCE_DEG) -> SegmentMatch:
    """Test one CAD segment against the PAGE index under a transform.

    `tolerance` and `direction_tolerance_deg` are in PAGE units. `samples`
    controls how finely the CAD segment is probed; the endpoints are always
    included so a short segment is not skipped.
    """
    if samples < 2:
        raise ValueError("samples must be >= 2")
    ca, cb = segment
    if segment_length(ca, cb) <= 0:
        return SegmentMatch(False)
    pa = transform.inverse_point(ca)
    pb = transform.inverse_point(cb)
    if pa == pb:
        return SegmentMatch(False)

    direction = math.atan2(pb[1] - pa[1], pb[0] - pa[0])
    dir_tol = math.radians(direction_tolerance_deg)

    deviations: List[float] = []
    used: List[int] = []
    for k in range(samples):
        t = k / float(samples - 1)
        probe = interpolate_point(pa, pb, t)
        hit = index.nearest_directed_deviation(probe, direction, dir_tol,
                                                tolerance)
        if hit is None:
            return SegmentMatch(False)
        deviations.append(hit[0])
        used.append(hit[1])

    return SegmentMatch(True, sum(deviations) / len(deviations),
                        max(deviations), used)


def match_entity(entity: GeometryEntity, transform: SimilarityTransform,
                 index: SegmentIndex, tolerance: float,
                 samples: int = DEFAULT_SAMPLES,
                 direction_tolerance_deg: float
                 = DEFAULT_DIRECTION_TOLERANCE_DEG) -> Optional[List[SegmentMatch]]:
    """All-or-nothing entity match.

    An entity matches only when EVERY one of its segments matches. Returning
    None makes an entity a single atomic unit of evidence, which is what keeps
    CONTROL / CHECK splits honest at entity level.
    """
    if not entity.has_segments:
        return None
    out: List[SegmentMatch] = []
    for seg in entity.segments:
        m = match_cad_segment(seg, transform, index, tolerance, samples,
                              direction_tolerance_deg)
        if not m.matched:
            return None
        out.append(m)
    return out


def matched_entity_ids(entity: GeometryEntity,
                       transform: SimilarityTransform,
                       index: SegmentIndex, tolerance: float,
                       samples: int = DEFAULT_SAMPLES,
                       direction_tolerance_deg: float
                       = DEFAULT_DIRECTION_TOLERANCE_DEG) -> bool:
    return match_entity(entity, transform, index, tolerance, samples,
                        direction_tolerance_deg) is not None


def consensus_count(entities: Iterable[GeometryEntity],
                    transform: SimilarityTransform, index: SegmentIndex,
                    tolerance: float, samples: int = DEFAULT_SAMPLES,
                    direction_tolerance_deg: float
                    = DEFAULT_DIRECTION_TOLERANCE_DEG) -> int:
    """Number of entities fully matched. The single comparable score."""
    return sum(1 for e in entities
               if matched_entity_ids(e, transform, index, tolerance, samples,
                                     direction_tolerance_deg))


def entity_max_deviation(entity: GeometryEntity,
                         transform: SimilarityTransform,
                         index: SegmentIndex, tolerance: float,
                         samples: int = DEFAULT_SAMPLES,
                         direction_tolerance_deg: float
                         = DEFAULT_DIRECTION_TOLERANCE_DEG) -> Optional[float]:
    """Worst sample deviation over a matched entity, in PAGE points.

    Returns None when the entity is not matched.
    """
    matches = match_entity(entity, transform, index, tolerance, samples,
                           direction_tolerance_deg)
    if matches is None:
        return None
    return max(m.max_deviation for m in matches)
