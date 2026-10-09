"""VP GeoConvert production geometry foundation.

The package is strictly two-dimensional and intentionally has no dependency on
the research benchmark harness.
"""

from .geometry import (CadPoint, Close, CloseSegment, CoordinateDomain,
                       CubicBezier, CubicBezierSegment, Line, LineSegment,
                       Move, ObjectPoint, PagePoint, PdfPath, SurveyPoint)
from .entities import (CubicBezierEntity, EntityGeometryError, LayerMetadata,
                       LineEntity, PolylineEntity, SourceProvenance,
                       transform_entity)
from .transform import (Correspondence, DegenerateControlError,
                        DomainMismatchError, InsufficientControlError,
                        InvalidTransformError, NumericalStabilityError,
                        ObservationRole, Reflection, ResidualReport,
                        SimilarityTransform, UnsupportedRouteError,
                        evaluate_check, evaluate_control, fit_similarity)

__all__ = [
    "CadPoint", "Close", "CloseSegment", "CoordinateDomain",
    "Correspondence", "CubicBezier", "CubicBezierEntity",
    "CubicBezierSegment",
    "DegenerateControlError", "DomainMismatchError", "InsufficientControlError",
    "EntityGeometryError", "InvalidTransformError", "LayerMetadata", "Line",
    "LineEntity", "LineSegment", "Move",
    "NumericalStabilityError", "ObjectPoint", "ObservationRole", "PagePoint",
    "PdfPath", "PolylineEntity", "Reflection", "ResidualReport",
    "SimilarityTransform", "SourceProvenance", "SurveyPoint",
    "UnsupportedRouteError", "evaluate_check", "evaluate_control",
    "fit_similarity", "transform_entity",
]
