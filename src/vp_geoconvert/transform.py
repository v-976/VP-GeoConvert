"""Domain-safe 2D similarity fitting and independent residual evaluation."""

import math
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Optional, Sequence, Tuple

from .geometry import (CoordinateDomain, Point2, make_point, point_components)


class TransformError(ValueError):
    pass


class DomainMismatchError(TransformError):
    pass


class UnsupportedRouteError(TransformError):
    pass


class InsufficientControlError(TransformError):
    pass


class DegenerateControlError(TransformError):
    pass


class InvalidTransformError(TransformError):
    pass


class NumericalStabilityError(TransformError):
    pass


class ObservationRole(Enum):
    CONTROL = "CONTROL"
    CHECK = "CHECK"
    DISABLED = "DISABLED"


class Reflection(IntEnum):
    DIRECT = 1
    REFLECTED = -1


ALLOWED_ROUTES = frozenset({
    (CoordinateDomain.PDF_PAGE, CoordinateDomain.DXF_CAD),
    (CoordinateDomain.PDF_PAGE, CoordinateDomain.SURVEY),
    (CoordinateDomain.DXF_CAD, CoordinateDomain.SURVEY),
})


def _validate_route(source: CoordinateDomain,
                    target: CoordinateDomain) -> None:
    if not isinstance(source, CoordinateDomain) \
            or not isinstance(target, CoordinateDomain):
        raise TypeError("source and target must be CoordinateDomain values")
    if (source, target) not in ALLOWED_ROUTES:
        raise UnsupportedRouteError(
            "unsupported transformation route %s -> %s"
            % (source.value, target.value))


@dataclass(frozen=True)
class Correspondence:
    observation_id: str
    source: Point2
    target: Point2
    role: ObservationRole

    def __post_init__(self) -> None:
        if not self.observation_id:
            raise ValueError("observation_id must not be empty")
        if not isinstance(self.role, ObservationRole):
            raise TypeError("role must be an ObservationRole")
        point_components(self.source)
        point_components(self.target)
        _validate_route(self.source_domain, self.target_domain)

    @property
    def source_domain(self) -> CoordinateDomain:
        return self.source.domain

    @property
    def target_domain(self) -> CoordinateDomain:
        return self.target.domain


@dataclass(frozen=True)
class SimilarityTransform:
    """One explicit similarity route with optional source-axis reflection.

    For source components (a, b), target components are:

        first  = scale * (cos(rotation)*a - eps*sin(rotation)*b) + offset_first
        second = scale * (sin(rotation)*a + eps*cos(rotation)*b) + offset_second

    where eps is +1 for DIRECT and -1 for REFLECTED.
    """

    source_domain: CoordinateDomain
    target_domain: CoordinateDomain
    scale: float
    rotation_rad: float
    target_offset_first: float
    target_offset_second: float
    reflection: Reflection = Reflection.DIRECT
    fitted_control_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_route(self.source_domain, self.target_domain)
        if not isinstance(self.reflection, Reflection):
            raise InvalidTransformError("reflection must be explicit")
        values = (self.scale, self.rotation_rad, self.target_offset_first,
                  self.target_offset_second)
        if not all(math.isfinite(value) for value in values):
            raise InvalidTransformError("transform parameters must be finite")
        if self.scale <= 0.0:
            raise InvalidTransformError("scale must be positive")
        if not math.isfinite(1.0 / self.scale):
            raise NumericalStabilityError("scale cannot be inverted safely")
        object.__setattr__(self, "fitted_control_ids",
                           tuple(self.fitted_control_ids))

    @property
    def is_reflected(self) -> bool:
        return self.reflection is Reflection.REFLECTED

    def forward(self, point: Point2) -> Point2:
        if point.domain is not self.source_domain:
            raise DomainMismatchError(
                "expected %s point, got %s"
                % (self.source_domain.value, point.domain.value))
        first, second = point_components(point)
        c = math.cos(self.rotation_rad)
        s = math.sin(self.rotation_rad)
        eps = int(self.reflection)
        out_first = self.scale * (c * first - eps * s * second)
        out_second = self.scale * (s * first + eps * c * second)
        out_first += self.target_offset_first
        out_second += self.target_offset_second
        if not math.isfinite(out_first) or not math.isfinite(out_second):
            raise NumericalStabilityError("forward transform overflowed")
        return make_point(self.target_domain, out_first, out_second)

    def inverse(self, point: Point2) -> Point2:
        if point.domain is not self.target_domain:
            raise DomainMismatchError(
                "expected %s point, got %s"
                % (self.target_domain.value, point.domain.value))
        first, second = point_components(point)
        first -= self.target_offset_first
        second -= self.target_offset_second
        c = math.cos(self.rotation_rad)
        s = math.sin(self.rotation_rad)
        eps = int(self.reflection)
        source_first = (c * first + s * second) / self.scale
        source_second = (-eps * s * first + eps * c * second) / self.scale
        if not math.isfinite(source_first) or not math.isfinite(source_second):
            raise NumericalStabilityError("inverse transform overflowed")
        return make_point(self.source_domain, source_first, source_second)


def _centroid(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def _check_resolvable_spread(first: Sequence[float], second: Sequence[float],
                             centre_first: float,
                             centre_second: float, label: str) -> None:
    radii = [math.hypot(a - centre_first, b - centre_second)
             for a, b in zip(first, second)]
    spread = max(radii)
    if not math.isfinite(spread):
        raise NumericalStabilityError("%s coordinate spread is not finite"
                                      % label)
    if spread == 0.0:
        raise DegenerateControlError("CONTROL %s points are coincident" % label)
    resolution = max(math.ulp(centre_first), math.ulp(centre_second))
    if spread <= 8.0 * resolution:
        raise NumericalStabilityError(
            "CONTROL %s spread is too small relative to coordinate magnitude"
            % label)


def fit_similarity(observations: Sequence[Correspondence],
                   reflection: Reflection = Reflection.DIRECT
                   ) -> SimilarityTransform:
    """Fit one explicit route using CONTROL observations only.

    Two CONTROL observations give the exact determined solution. Three or more
    use the same centred least-squares solution. CHECK and DISABLED observations
    are not inspected for fitting or route selection.
    """
    if not isinstance(reflection, Reflection):
        raise InvalidTransformError("reflection must be selected explicitly")
    controls = [item for item in observations
                if item.role is ObservationRole.CONTROL]
    if len(controls) < 2:
        raise InsufficientControlError("at least two CONTROL observations required")

    source_domain = controls[0].source_domain
    target_domain = controls[0].target_domain
    for item in controls[1:]:
        if (item.source_domain is not source_domain
                or item.target_domain is not target_domain):
            raise DomainMismatchError(
                "all CONTROL observations must use one transformation route")

    source_components = [point_components(item.source) for item in controls]
    target_components = [point_components(item.target) for item in controls]
    source_first = [item[0] for item in source_components]
    source_second = [item[1] for item in source_components]
    target_first = [item[0] for item in target_components]
    target_second = [item[1] for item in target_components]

    cs_first = _centroid(source_first)
    cs_second = _centroid(source_second)
    ct_first = _centroid(target_first)
    ct_second = _centroid(target_second)
    if not all(math.isfinite(value) for value in
               (cs_first, cs_second, ct_first, ct_second)):
        raise NumericalStabilityError("coordinate centroid is not finite")
    _check_resolvable_spread(source_first, source_second,
                             cs_first, cs_second, "source")
    _check_resolvable_spread(target_first, target_second,
                             ct_first, ct_second, "target")

    eps = int(reflection)
    denominator_terms = []
    p_terms = []
    q_terms = []
    for (src_first, src_second), (dst_first, dst_second) in zip(
            source_components, target_components):
        da = src_first - cs_first
        db = src_second - cs_second
        d_first = dst_first - ct_first
        d_second = dst_second - ct_second
        denominator_terms.append(da * da + db * db)
        p_terms.append(da * d_first + eps * db * d_second)
        q_terms.append(-eps * db * d_first + da * d_second)

    denominator = math.fsum(denominator_terms)
    if denominator <= 0.0 or not math.isfinite(denominator):
        raise DegenerateControlError("invalid CONTROL normal equation")
    p = math.fsum(p_terms) / denominator
    q = math.fsum(q_terms) / denominator
    scale = math.hypot(p, q)
    if not math.isfinite(scale) or scale <= 0.0:
        raise InvalidTransformError(
            "CONTROL targets imply an invalid similarity scale")
    rotation = math.atan2(q, p)
    offset_first = ct_first - (p * cs_first - eps * q * cs_second)
    offset_second = ct_second - (q * cs_first + eps * p * cs_second)
    if not all(math.isfinite(value) for value in
               (rotation, offset_first, offset_second)):
        raise NumericalStabilityError("fitted transform is not finite")

    return SimilarityTransform(
        source_domain, target_domain, scale, rotation,
        offset_first, offset_second, reflection,
        tuple(item.observation_id for item in controls))


@dataclass(frozen=True)
class Residual:
    observation_id: str
    role: ObservationRole
    target_domain: CoordinateDomain
    component_first: float
    component_second: float
    radial: float


@dataclass(frozen=True)
class ResidualReport:
    role: ObservationRole
    target_domain: CoordinateDomain
    residuals: Tuple[Residual, ...]
    radial_rms: Optional[float]
    radial_median: Optional[float]
    radial_p95: Optional[float]
    radial_max: Optional[float]


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _evaluate(transform: SimilarityTransform,
              observations: Sequence[Correspondence],
              role: ObservationRole) -> ResidualReport:
    residuals = []
    for item in observations:
        if item.role is not role:
            continue
        if (item.source_domain is not transform.source_domain
                or item.target_domain is not transform.target_domain):
            raise DomainMismatchError(
                "%s observation %s does not match transform route"
                % (role.value, item.observation_id))
        predicted = transform.forward(item.source)
        predicted_first, predicted_second = point_components(predicted)
        observed_first, observed_second = point_components(item.target)
        first = observed_first - predicted_first
        second = observed_second - predicted_second
        radial = math.hypot(first, second)
        residuals.append(Residual(item.observation_id, role,
                                  transform.target_domain,
                                  first, second, radial))

    values = [item.radial for item in residuals]
    if values:
        rms = math.sqrt(math.fsum(value * value for value in values)
                        / len(values))
        median = _percentile(values, 0.5)
        p95 = _percentile(values, 0.95)
        maximum = max(values)
    else:
        rms = median = p95 = maximum = None
    return ResidualReport(role, transform.target_domain, tuple(residuals),
                          rms, median, p95, maximum)


def evaluate_control(transform: SimilarityTransform,
                     observations: Sequence[Correspondence]) -> ResidualReport:
    return _evaluate(transform, observations, ObservationRole.CONTROL)


def evaluate_check(transform: SimilarityTransform,
                   observations: Sequence[Correspondence]) -> ResidualReport:
    """Evaluate CHECK without changing or selecting the fitted transform."""
    return _evaluate(transform, observations, ObservationRole.CHECK)
