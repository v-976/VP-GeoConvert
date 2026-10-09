"""Geometry-only similarity search: blind hypothesis generation.

Search strategy, and why it is shaped this way. Each choice below was forced
by a measurement that contradicted the obvious alternative:

  1. NO file names, NO title block, NO textual coordinates, NO known
     coordinates, NO manually chosen control points. The only inputs are PAGE
     segment geometry and CAD segment geometry.

  2. Candidate scale is scanned over the range DERIVED FROM THE DATA
     (s = cad_length / page_length), not read off a histogram peak. On a
     synthetic scene with a near-uniform length histogram the true log-scale
     offset was absent from the top 12 correlation peaks, so a histogram peak
     cannot be trusted to localise scale.

  3. Candidate rotation is covered by an overlapping coarse scan. The
     direction histogram is nearly degenerate in engineering drawings because
     they are axis aligned, so it carries little information about rotation.

  4. Bucket candidates by length and direction, then produce an EXACT 2-point
     fit for each compatible pair. An exact 2-point fit proves nothing on its
     own, so each fit is required to agree with the bucket hypothesis that
     generated it (scale and rotation acceptance windows).

  5. Cluster the exact fits in (log s, theta, tx, ty). The genuine transform is
     voted by every genuinely matching segment; coincidences scatter. The
     winning cluster is the hypothesis.

  6. The translation cluster bin must be expressed in PAGE points. Binning
     translation in CAD units collapses when the CAD unit is small, which put
     every candidate in one bin and made the cluster mean meaningless.

Reflection and axis swap are separate, explicit hypotheses and are never
normalised away.
"""

import math
import random
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from .geometry import SegmentIndex, consensus_count
from .models import GeometryEntity, SimilarityTransform
from .similarity import (Correspondence, angle_difference_rad,
                         segment_direction_rad, segment_length)

Segment = Tuple[Tuple[float, float], Tuple[float, float]]

# Bucket geometry for the compatibility index. These are INDEXING parameters,
# not acceptance thresholds: a bucket only groups candidates, and the exact fit
# plus the acceptance windows decide what is kept.
LENGTH_LOG_BIN = 0.08
DIRECTION_BIN_DEG = 3.0
DIRECTION_BIN_COUNT = int(round(180.0 / DIRECTION_BIN_DEG))


class MatchConfig:
    """All matching parameters in one place.

    Nothing here is a magic constant hidden inside a function. Every value is
    recorded verbatim in the run output so a result can be reproduced or
    challenged.
    """

    def __init__(self,
                 tolerance_points: float = 1.5,
                 direction_tolerance_deg: float = 1.0,
                 samples: int = 11,
                 cell_size: float = 6.0,
                 scale_step_bins: int = 1,
                 scale_step_log: float = 0.08,
                 rotation_step_deg: float = 15.0,
                 length_window_bins: int = 1,
                 direction_window_bins: int = 3,
                 scale_accept_log: float = 0.048,
                 rotation_accept_deg: float = 10.0,
                 candidate_page_segments: int = 20000,
                 candidate_cad_segments: int = 200,
                 verify_clusters: int = 400,
                 cluster_log_bin: float = 0.0015,
                 cluster_angle_bin_deg: float = 0.35,
                 cluster_translation_points: float = 4.5,
                 consensus_sample: int = 140,
                 random_seed: int = 20261007) -> None:
        if tolerance_points <= 0:
            raise ValueError("tolerance_points must be positive")
        if samples < 2:
            raise ValueError("samples must be >= 2")
        # Acceptance windows must be wider than half the scan step, otherwise
        # the scan leaves gaps and genuinely valid fits get rejected.
        half_scale = 0.5 * scale_step_bins * scale_step_log
        if scale_accept_log <= half_scale:
            raise ValueError("scale_accept_log (%.4f) must exceed half the "
                             "scale step (%.4f)" % (scale_accept_log,
                                                    half_scale))
        half_rot = 0.5 * rotation_step_deg
        if rotation_accept_deg <= half_rot:
            raise ValueError("rotation_accept_deg (%.3f) must exceed half "
                             "the rotation step (%.3f)"
                             % (rotation_accept_deg, half_rot))
        self.tolerance_points = tolerance_points
        self.direction_tolerance_deg = direction_tolerance_deg
        self.samples = samples
        self.cell_size = cell_size
        self.scale_step_bins = scale_step_bins
        self.scale_step_log = scale_step_log
        self.rotation_step_deg = rotation_step_deg
        self.length_window_bins = length_window_bins
        self.direction_window_bins = direction_window_bins
        self.scale_accept_log = scale_accept_log
        self.rotation_accept_deg = rotation_accept_deg
        self.candidate_page_segments = candidate_page_segments
        self.candidate_cad_segments = candidate_cad_segments
        self.verify_clusters = verify_clusters
        self.cluster_log_bin = cluster_log_bin
        self.cluster_angle_bin_deg = cluster_angle_bin_deg
        self.cluster_translation_points = cluster_translation_points
        self.consensus_sample = consensus_sample
        self.random_seed = random_seed

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in sorted(dir(self))
                if not k.startswith("_") and isinstance(getattr(self, k),
                                                         (int, float, str))}

    @property
    def scale_step_factor(self) -> float:
        return 10.0 ** self.scale_step_log


# --------------------------------------------------------------- bucket index

def _length_bin(length: float) -> int:
    return int(round(math.log10(length) / LENGTH_LOG_BIN))


def _direction_bin(direction_deg: float) -> int:
    return int(round((direction_deg % 180.0) / DIRECTION_BIN_DEG)) \
        % DIRECTION_BIN_COUNT


def build_page_buckets(page_segments: Sequence[Segment]
                       ) -> Dict[Tuple[int, int], List[int]]:
    buckets: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for i, (a, b) in enumerate(page_segments):
        length = segment_length(a, b)
        if length <= 0:
            continue
        buckets[(_length_bin(length),
                _direction_bin(math.degrees(segment_direction_rad(a, b))))].append(i)
    return buckets


def _pair_fits(cad_segments: Sequence[Segment],
               page_segments: Sequence[Segment],
               eps: int,
               scale: float,
               rotation_rad: float,
               buckets: Dict[Tuple[int, int], List[int]],
               config: MatchConfig) -> List[SimilarityTransform]:
    """Exact 2-point fits for bucket-compatible pairs, filtered by acceptance.

    The transform maps PAGE to CAD, so a CAD direction maps back to a PAGE
    direction by SUBTRACTING the rotation. Adding it instead is a sign error
    that silently yields an empty hypothesis set.
    """
    from .similarity import fit_similarity

    scale_hi = 10.0 ** config.scale_accept_log
    scale_lo = 1.0 / scale_hi
    rot_tol = math.radians(config.rotation_accept_deg)
    rotation_deg = math.degrees(rotation_rad)
    out: List[SimilarityTransform] = []

    for ca, cb in cad_segments:
        cad_length = segment_length(ca, cb)
        if cad_length <= 0:
            continue
        expected_page_length = cad_length / scale
        lb = _length_bin(expected_page_length)
        # CAD -> PAGE direction is the inverse rotation.
        direction_deg = (math.degrees(segment_direction_rad(ca, cb))
                         - rotation_deg)
        db = _direction_bin(direction_deg)

        candidates: List[int] = []
        for dl in range(-config.length_window_bins,
                        config.length_window_bins + 1):
            for dd in range(-config.direction_window_bins,
                            config.direction_window_bins + 1):
                candidates.extend(
                    buckets.get((lb + dl, (db + dd) % DIRECTION_BIN_COUNT),
                                ()))
        if not candidates:
            continue

        for idx in candidates:
            pa, pb = page_segments[idx]
            for first, second in ((pa, pb), (pb, pa)):
                fit = fit_similarity(
                    [(first[0], first[1], ca[0], ca[1]),
                     (second[0], second[1], cb[0], cb[1])], eps)
                if fit is None:
                    continue
                if not (scale_lo * scale < fit.scale < scale_hi * scale):
                    continue
                if abs(angle_difference_rad(fit.rotation_rad,
                                            rotation_rad)) > rot_tol:
                    continue
                out.append(fit)
    return out


def _cluster_fits(fits: Sequence[SimilarityTransform],
                  reference_cad: Optional[Tuple[float, float]],
                  config: MatchConfig) -> List[Tuple[SimilarityTransform, int]]:
    """Cluster exact fits and return representatives, largest cluster first.

    The translation bin is computed from a fixed CAD reference point mapped
    into PAGE space, so the bin width is in PAGE points and does not depend on
    the DXF unit.
    """
    bin_points = config.cluster_translation_points
    bins: Dict[Tuple[int, int, int, int], List[SimilarityTransform]] = \
        defaultdict(list)
    for t in fits:
        if reference_cad is None:
            bu = int(round(t.tx / bin_points))
            bv = int(round(t.ty / bin_points))
        else:
            u, v = t.apply_point(reference_cad)
            bu = int(round(u / bin_points))
            bv = int(round(v / bin_points))
        key = (int(round(math.log10(t.scale) / config.cluster_log_bin)),
               int(round(math.degrees(t.rotation_rad)
                         / config.cluster_angle_bin_deg)),
               bu, bv)
        bins[key].append(t)

    ranked = sorted(bins.items(), key=lambda kv: -len(kv[1]))
    out: List[Tuple[SimilarityTransform, int]] = []
    for _key, members in ranked[:config.verify_clusters]:
        n = len(members)
        mean = SimilarityTransform(
            sum(m.scale for m in members) / n,
            sum(m.rotation_rad for m in members) / n,
            sum(m.tx for m in members) / n,
            sum(m.ty for m in members) / n,
            members[0].eps)
        out.append((mean, n))
    return out


class Hypothesis:
    """One candidate transform with the measurements that produced it."""

    __slots__ = ("transform", "cluster_votes", "search_consensus",
                 "verified_consensus")

    def __init__(self, transform: SimilarityTransform, cluster_votes: int,
                 search_consensus: int) -> None:
        self.transform = transform
        self.cluster_votes = cluster_votes
        self.search_consensus = search_consensus
        self.verified_consensus: Optional[int] = None


def _dedupe(hypotheses: Sequence[Hypothesis], tolerance: float
            ) -> List[Hypothesis]:
    kept: List[Hypothesis] = []
    for h in hypotheses:
        t = h.transform
        duplicate = False
        for k in kept:
            u = k.transform
            if (abs(u.scale - t.scale) / u.scale < 5e-4
                    and abs(angle_difference_rad(u.rotation_rad,
                                                t.rotation_rad)) < 1e-6
                    and abs(u.tx - t.tx) < 5.0 * tolerance
                    and abs(u.ty - t.ty) < 5.0 * tolerance):
                duplicate = True
                break
        if not duplicate:
            kept.append(h)
    return kept


def search_similarity(cad_segments: Sequence[Segment],
                      page_segments: Sequence[Segment],
                      eps: int,
                      config: MatchConfig,
                      index: Optional[SegmentIndex] = None,
                      verification_entities: Optional[Sequence[GeometryEntity]] = None,
                      max_hypotheses: int = 12) -> List[Hypothesis]:
    """Blind search. Returns ranked hypotheses, best first.

    `verification_entities` is used only to score candidates; when None, no
    entity-level verification is performed and cluster votes are reported as
    the ranking key. Callers MUST pass CONTROL entities only.
    """
    if not cad_segments or not page_segments:
        return []
    index = index or SegmentIndex(page_segments, config.cell_size)

    candidate_page = sorted(page_segments, key=lambda s: -segment_length(*s))[
        :config.candidate_page_segments]
    candidate_cad = sorted(cad_segments, key=lambda s: -segment_length(*s))[
        :config.candidate_cad_segments]
    if not candidate_page or not candidate_cad:
        return []

    buckets = build_page_buckets(candidate_page)
    page_lengths = [segment_length(*s) for s in candidate_page]
    page_lengths = [v for v in page_lengths if v > 0]
    cad_lengths = [segment_length(*s) for s in candidate_cad]
    cad_lengths = [v for v in cad_lengths if v > 0]
    if not page_lengths or not cad_lengths:
        return []

    # s = cad_length / page_length, so the scan range is
    #   s_min = min(cad) / max(page)   and   s_max = max(cad) / min(page).
    lo = int(math.floor(math.log10(min(cad_lengths) / max(page_lengths))
                        / config.scale_step_log)) - 2
    hi = int(math.ceil(math.log10(max(cad_lengths) / min(page_lengths))
                       / config.scale_step_log)) + 2

    all_fits: List[SimilarityTransform] = []
    rotation_steps = max(1, int(round(180.0 / config.rotation_step_deg)))
    step = config.scale_step_bins
    for di in range(lo, hi + 1, step):
        scale = 10.0 ** (di * config.scale_step_log)
        if scale <= 0:
            continue
        for k in range(rotation_steps):
            all_fits.extend(_pair_fits(candidate_cad, candidate_page, eps,
                                       scale,
                                       math.radians(k * config.rotation_step_deg),
                                       buckets, config))
    if not all_fits:
        return []

    mids = [((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            for a, b in candidate_cad]
    reference = (sum(m[0] for m in mids) / len(mids),
                 sum(m[1] for m in mids) / len(mids))

    clusters = _cluster_fits(all_fits, reference, config)
    pool = None
    if verification_entities:
        from .split import spread_sample
        pool = spread_sample(list(verification_entities),
                             config.consensus_sample)

    hypotheses: List[Hypothesis] = []
    for transform, votes in clusters:
        score = (consensus_count(pool, transform, index,
                                 config.tolerance_points, config.samples,
                                 config.direction_tolerance_deg)
                 if pool else votes)
        h = Hypothesis(transform, votes, score)
        h.verified_consensus = score if pool else None
        hypotheses.append(h)

    hypotheses.sort(key=lambda h: (-h.search_consensus, -h.cluster_votes))
    return _dedupe(hypotheses, config.tolerance_points)[:max_hypotheses]


def random_transforms(seed: int, count: int,
                      reference: SimilarityTransform) -> List[SimilarityTransform]:
    """Deterministic random wrong transforms for the selectivity floor.

    The perturbation amplitude is derived from the reference translation so it
    scales with the dataset instead of being tuned per dataset.
    """
    rng = random.Random(seed)
    span = max(1.0, abs(reference.tx) * 5e-5)
    out = []
    for _ in range(count):
        scale = reference.scale * math.exp(rng.uniform(-0.6, 0.6))
        rotation = reference.rotation_rad + rng.uniform(-math.pi, math.pi)
        tx = reference.tx + rng.uniform(-3 * span, 3 * span)
        ty = reference.ty + rng.uniform(-3 * span, 3 * span)
        eps = rng.choice((1, -1))
        out.append(SimilarityTransform(scale, rotation, tx, ty, eps))
    return out
