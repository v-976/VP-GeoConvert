"""Immutable 2D entities with source provenance and domain-safe transforms."""

from dataclasses import dataclass
from typing import Optional, Tuple, Union

from .geometry import CoordinateDomain, Point2, point_components
from .transform import DomainMismatchError, SimilarityTransform


class EntityGeometryError(ValueError):
    pass


def _required_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("%s must be a non-empty string" % name)


@dataclass(frozen=True)
class SourceProvenance:
    """Identity of the source geometry; retained after transformation."""

    source_id: str
    source_object_id: str
    source_domain: CoordinateDomain
    source_path_id: Optional[str] = None
    source_segment_index: Optional[int] = None

    def __post_init__(self) -> None:
        _required_text(self.source_id, "source_id")
        _required_text(self.source_object_id, "source_object_id")
        if not isinstance(self.source_domain, CoordinateDomain):
            raise TypeError("source_domain must be a CoordinateDomain")
        if self.source_path_id is not None:
            _required_text(self.source_path_id, "source_path_id")
        if self.source_segment_index is not None:
            if self.source_path_id is None:
                raise ValueError("segment linkage requires source_path_id")
            if isinstance(self.source_segment_index, bool) \
                    or not isinstance(self.source_segment_index, int) \
                    or self.source_segment_index < 0:
                raise ValueError(
                    "source_segment_index must be a non-negative integer")


@dataclass(frozen=True)
class LayerMetadata:
    """Optional source layer/OCG references; extraction is outside RH2.2."""

    layer_name: Optional[str] = None
    ocg_id: Optional[str] = None
    ocg_name: Optional[str] = None

    def __post_init__(self) -> None:
        for name, value in (("layer_name", self.layer_name),
                            ("ocg_id", self.ocg_id),
                            ("ocg_name", self.ocg_name)):
            if value is not None:
                _required_text(value, name)


def _entity_domain(points: Tuple[Point2, ...]) -> CoordinateDomain:
    if not points:
        raise EntityGeometryError("entity geometry must contain points")
    for point in points:
        point_components(point)
    domain = points[0].domain
    if any(point.domain is not domain for point in points[1:]):
        raise EntityGeometryError("one entity cannot mix coordinate domains")
    return domain


def _entity_id(value: str) -> None:
    _required_text(value, "entity_id")


def _metadata(provenance: SourceProvenance,
              layer: Optional[LayerMetadata]) -> None:
    if not isinstance(provenance, SourceProvenance):
        raise TypeError("provenance must be SourceProvenance")
    if layer is not None and not isinstance(layer, LayerMetadata):
        raise TypeError("layer must be LayerMetadata or None")


@dataclass(frozen=True)
class LineEntity:
    entity_id: str
    start: Point2
    end: Point2
    provenance: SourceProvenance
    layer: Optional[LayerMetadata] = None

    def __post_init__(self) -> None:
        _entity_id(self.entity_id)
        _metadata(self.provenance, self.layer)
        _entity_domain((self.start, self.end))

    @property
    def domain(self) -> CoordinateDomain:
        return self.start.domain


@dataclass(frozen=True)
class PolylineEntity:
    entity_id: str
    vertices: Tuple[Point2, ...]
    provenance: SourceProvenance
    closed: bool = False
    layer: Optional[LayerMetadata] = None

    def __post_init__(self) -> None:
        _entity_id(self.entity_id)
        _metadata(self.provenance, self.layer)
        object.__setattr__(self, "vertices", tuple(self.vertices))
        if len(self.vertices) < 2:
            raise EntityGeometryError("POLYLINE requires at least two vertices")
        _entity_domain(self.vertices)
        if not isinstance(self.closed, bool):
            raise TypeError("closed must be bool")

    @property
    def domain(self) -> CoordinateDomain:
        return self.vertices[0].domain


@dataclass(frozen=True)
class CubicBezierEntity:
    entity_id: str
    start: Point2
    control1: Point2
    control2: Point2
    end: Point2
    provenance: SourceProvenance
    layer: Optional[LayerMetadata] = None

    def __post_init__(self) -> None:
        _entity_id(self.entity_id)
        _metadata(self.provenance, self.layer)
        _entity_domain((self.start, self.control1, self.control2, self.end))

    @property
    def domain(self) -> CoordinateDomain:
        return self.start.domain


Entity2D = Union[LineEntity, PolylineEntity, CubicBezierEntity]


def transform_entity(entity: Entity2D,
                     transform: SimilarityTransform) -> Entity2D:
    """Return transformed geometry while retaining immutable source metadata."""
    if not isinstance(entity, (LineEntity, PolylineEntity, CubicBezierEntity)):
        raise TypeError("unsupported 2D entity: %r" % (entity,))
    if entity.domain is not transform.source_domain:
        raise DomainMismatchError(
            "entity is in %s but transform expects %s"
            % (entity.domain.value, transform.source_domain.value))

    if isinstance(entity, LineEntity):
        return LineEntity(entity.entity_id,
                          transform.forward(entity.start),
                          transform.forward(entity.end),
                          entity.provenance, entity.layer)
    if isinstance(entity, PolylineEntity):
        return PolylineEntity(
            entity.entity_id,
            tuple(transform.forward(point) for point in entity.vertices),
            entity.provenance, entity.closed, entity.layer)
    return CubicBezierEntity(
        entity.entity_id,
        transform.forward(entity.start),
        transform.forward(entity.control1),
        transform.forward(entity.control2),
        transform.forward(entity.end),
        entity.provenance, entity.layer)
