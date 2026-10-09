"""Residual computation. CAD domain only, by construction.

The historic defect this module exists to prevent: an unmatched-PDF metric
built a spatial index over CAD-space DXF segments and then queried it with
PAGE-space PDF coordinates, comparing the result against a tolerance expressed
in PAGE points. Two different domains in one comparison silently produced
meaningless numbers.

Rules enforced here:

  * Every residual is measured in CAD coordinates. A DXF vertex is transformed
    into PAGE space to find the nearest PDF feature, the matched PAGE point is
    transformed BACK into CAD, and the difference is taken in CAD. No distance
    is ever reported by mixing a CAD coordinate with a PAGE coordinate.
  * `tolerance_points` is used only as a match gate in PAGE space, and the
    conversion CAD-per-point is the transform scale. It is never used as a CAD
    distance.
  * Values are stored in CAD native units. A millimetre view is added only
    when the DXF declared units, and is explicitly flagged as derived.

Strictly 2D. No elevation, no Z, no H.
"""

import math
from typing import List, Optional, Sequence, Tuple

from .geometry import (SegmentIndex, SegmentMatch, match_entity)
from .models import (Domain, GeometryEntity, ResidualStats, SimilarityTransform,
                     UnitInfo)
from .similarity import project_on_segment, segment_direction_rad
from .units import residual_unit_label


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    low = int(math.floor(pos))
    high = min(low + 1, len(sorted_values) - 1)
    frac = pos - low
    return sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac


def _rms(values: Sequence[float]) -> float:
    if not values:
        return float("nan")
    return math.sqrt(sum(v * v for v in values) / len(values))


def segment_axis_residuals(entity: GeometryEntity,
                           transform: SimilarityTransform,
                           index: SegmentIndex,
                           config,
                           matches: Optional[List[SegmentMatch]] = None
                           ) -> Optional[List[Tuple[float, float]]]:
    """Signed (d_cad_x, d_cad_y) per segment of a MATCHED entity.

    For each CAD segment endpoint the nearest direction-compatible PAGE point
    is found, mapped back into CAD with the transform, and subtracted from the
    CAD endpoint. Returns None when the entity is not matched.
    """
    if matches is None:
        matches = match_entity(entity, transform, index,
                               config.tolerance_points, config.samples,
                               config.direction_tolerance_deg)
    if matches is None:
        return None

    direction_tol = math.radians(config.direction_tolerance_deg)
    out: List[Tuple[float, float]] = []
    for segment, _m in zip(entity.segments, matches):
        ca, cb = segment
        pa = transform.inverse_point(ca)
        pb = transform.inverse_point(cb)
        direction = math.atan2(pb[1] - pa[1], pb[0] - pa[0])
        for cad_point, page_point in ((ca, pa), (cb, pb)):
            hit = index.nearest_directed_deviation(page_point, direction,
                                                   direction_tol,
                                                   config.tolerance_points)
            if hit is None:
                return None
            page_seg = index.segments[hit[1]]
            nearest_page = project_on_segment(page_point, page_seg[0],
                                              page_seg[1])
            mapped_cad = transform.apply_point(nearest_page)
            out.append((mapped_cad[0] - cad_point[0],
                        mapped_cad[1] - cad_point[1]))
    return out


def entity_residuals(entity: GeometryEntity,
                     transform: SimilarityTransform,
                     index: SegmentIndex,
                     config) -> Optional[ResidualStats]:
    """Residuals for one entity, or None when it is not matched."""
    matches = match_entity(entity, transform, index, config.tolerance_points,
                           config.samples, config.direction_tolerance_deg)
    if matches is None:
        return None
    axes = segment_axis_residuals(entity, transform, index, config, matches)
    if axes is None:
        return None
    dx = [a[0] for a in axes]
    dy = [a[1] for a in axes]
    radial = [math.hypot(a[0], a[1]) for a in axes]
    radial_sorted = sorted(radial)
    return ResidualStats(
        n_features=len(radial), n_entities=1,
        radial_rms=_rms(radial), radial_median=_quantile(radial_sorted, 0.5),
        radial_p95=_quantile(radial_sorted, 0.95), radial_max=max(radial),
        cad_x_rms=_rms(dx), cad_y_rms=_rms(dy),
        cad_x_bias=sum(dx) / len(dx), cad_y_bias=sum(dy) / len(dy),
        cad_x_p95=_quantile(sorted(abs(v) for v in dx), 0.95),
        cad_y_p95=_quantile(sorted(abs(v) for v in dy), 0.95))


def pool_residuals(entities: Sequence[GeometryEntity],
                   transform: SimilarityTransform,
                   index: SegmentIndex,
                   config,
                   units: UnitInfo,
                   tolerance_points: Optional[float] = None
                   ) -> ResidualStats:
    """Aggregate residuals over a matched subset, in CAD domain.

    Unmatched entities are excluded, so callers must report the matched count
    alongside these numbers. `tolerance_points` defaults to the config value
    and is used only to build the threshold table.
    """
    all_dx: List[float] = []
    all_dy: List[float] = []
    radial: List[float] = []
    n_entities = 0
    for e in entities:
        stats = entity_residuals(e, transform, index, config)
        if stats is None:
            continue
        n_entities += 1
        matches = match_entity(e, transform, index, config.tolerance_points,
                               config.samples, config.direction_tolerance_deg)
        axes = segment_axis_residuals(e, transform, index, config, matches)
        if axes is None:
            continue
        all_dx.extend(a[0] for a in axes)
        all_dy.extend(a[1] for a in axes)
        radial.extend(math.hypot(a[0], a[1]) for a in axes)

    if not radial:
        return ResidualStats(0, 0, None, None, None, None, None, None,
                             None, None, None, None,
                             units_declared=units.declared,
                             residual_unit=residual_unit_label(units),
                             raw_radial=[])
    rs = sorted(radial)
    return ResidualStats(
        n_features=len(radial), n_entities=n_entities,
        radial_rms=_rms(radial), radial_median=_quantile(rs, 0.5),
        radial_p95=_quantile(rs, 0.95), radial_max=max(radial),
        cad_x_rms=_rms(all_dx), cad_y_rms=_rms(all_dy),
        cad_x_bias=sum(all_dx) / len(all_dx),
        cad_y_bias=sum(all_dy) / len(all_dy),
        cad_x_p95=_quantile(sorted(abs(v) for v in all_dx), 0.95),
        cad_y_p95=_quantile(sorted(abs(v) for v in all_dy), 0.95),
        units_declared=units.declared,
        residual_unit=residual_unit_label(units),
        raw_radial=radial)


def threshold_pass_rates(stats: ResidualStats, transform: SimilarityTransform,
                         units: UnitInfo,
                         thresholds_mm=(1, 5, 10, 20, 50, 100, 250, 500, 1000)
                         ) -> List[Tuple[float, Optional[int], Optional[int]]]:
    """Pass-rate table as a MEASUREMENT, not a product decision.

    Returns (threshold_mm, n_passed_or_None, n_total). When the DXF does not
    declare units the millimetre thresholds are meaningless, so None is
    returned and no mm label is emitted.
    """
    values = stats.raw_radial or []
    out: List[Tuple[float, Optional[int], int]] = []
    for t in thresholds_mm:
        if not units.declared or not values:
            # Units not proven: a millimetre threshold would be a fiction.
            out.append((float(t), None, len(values)))
            continue
        cad_threshold = units.from_millimetres(float(t))
        if cad_threshold is None:
            out.append((float(t), None, len(values)))
            continue
        passed = sum(1 for v in values if v <= cad_threshold)
        out.append((float(t), passed, len(values)))
    return out


def residual_report_lines(stats: ResidualStats, transform: SimilarityTransform,
                          units: UnitInfo) -> List[str]:
    """Human-readable residuals. CAD native units always; mm only if declared."""
    lines: List[str] = []
    to_mm = units.to_millimetres(1.0) if units.declared else None
    suffix = " [CAD units; units not declared]" if to_mm is None else ""
    if to_mm is None:
        lines.append("  radial  n=%d RMS=%.6f median=%.6f p95=%.6f max=%.6f%s"
                     % (stats.n_features, stats.radial_rms,
                        stats.radial_median, stats.radial_p95,
                        stats.radial_max, suffix))
        lines.append("  cad_x   bias=%+.6f RMS=%.6f p95|e|=%.6f%s"
                     % (stats.cad_x_bias, stats.cad_x_rms, stats.cad_x_p95,
                        suffix))
        lines.append("  cad_y   bias=%+.6f RMS=%.6f p95|e|=%.6f%s"
                     % (stats.cad_y_bias, stats.cad_y_rms, stats.cad_y_p95,
                        suffix))
        return lines

    f = to_mm
    lines.append("  radial  n=%d RMS=%.4f mm median=%.4f mm p95=%.4f mm "
                 "max=%.4f mm   (CAD: %.6f / %.6f / %.6f / %.6f)"
                 % (stats.n_features, stats.radial_rms * f,
                    stats.radial_median * f, stats.radial_p95 * f,
                    stats.radial_max * f, stats.radial_rms,
                    stats.radial_median, stats.radial_p95, stats.radial_max))
    lines.append("  cad_x   bias=%+.4f mm RMS=%.4f mm p95|e|=%.4f mm   "
                 "(CAD: %+.6f / %.6f / %.6f)"
                 % (stats.cad_x_bias * f, stats.cad_x_rms * f,
                    stats.cad_x_p95 * f, stats.cad_x_bias, stats.cad_x_rms,
                    stats.cad_x_p95))
    lines.append("  cad_y   bias=%+.4f mm RMS=%.4f mm p95|e|=%.4f mm   "
                 "(CAD: %+.6f / %.6f / %.6f)"
                 % (stats.cad_y_bias * f, stats.cad_y_rms * f,
                    stats.cad_y_p95 * f, stats.cad_y_bias, stats.cad_y_rms,
                    stats.cad_y_p95))
    return lines


def tolerance_in_cad_units(transform: SimilarityTransform,
                            tolerance_points: float) -> float:
    """PAGE-point tolerance expressed as a CAD-native distance.

    Provided for interpretation only: it says how wide the match gate was in
    the DXF's own units. It is not a residual and must never be compared to a
    residual without saying so.
    """
    return tolerance_points * transform.scale
