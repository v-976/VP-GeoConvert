"""Strict 2D coordinate domains and lossless PDF path commands."""

import math
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Tuple, Type, Union


class CoordinateDomain(Enum):
    PDF_OBJECT = "PDF_OBJECT"
    PDF_PAGE = "PDF_PAGE"
    DXF_CAD = "DXF_CAD"
    SURVEY = "SURVEY"


def _finite(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("%s must be finite" % name)
    return value


@dataclass(frozen=True)
class ObjectPoint:
    """PDF object coordinates; never PAGE, CAD, or SURVEY coordinates."""

    p: float
    q: float
    domain: ClassVar[CoordinateDomain] = CoordinateDomain.PDF_OBJECT

    def __post_init__(self) -> None:
        object.__setattr__(self, "p", _finite(self.p, "p"))
        object.__setattr__(self, "q", _finite(self.q, "q"))


@dataclass(frozen=True)
class PagePoint:
    """PDF PAGE coordinates."""

    u: float
    v: float
    domain: ClassVar[CoordinateDomain] = CoordinateDomain.PDF_PAGE

    def __post_init__(self) -> None:
        object.__setattr__(self, "u", _finite(self.u, "u"))
        object.__setattr__(self, "v", _finite(self.v, "v"))


@dataclass(frozen=True)
class CadPoint:
    """Native planar DXF CAD coordinates; not SURVEY X/Y."""

    cad_x: float
    cad_y: float
    domain: ClassVar[CoordinateDomain] = CoordinateDomain.DXF_CAD

    def __post_init__(self) -> None:
        object.__setattr__(self, "cad_x", _finite(self.cad_x, "cad_x"))
        object.__setattr__(self, "cad_y", _finite(self.cad_y, "cad_y"))


@dataclass(frozen=True)
class SurveyPoint:
    """SURVEY coordinates: X = Northing, Y = Easting."""

    X: float
    Y: float
    domain: ClassVar[CoordinateDomain] = CoordinateDomain.SURVEY

    def __post_init__(self) -> None:
        object.__setattr__(self, "X", _finite(self.X, "X"))
        object.__setattr__(self, "Y", _finite(self.Y, "Y"))


Point2 = Union[ObjectPoint, PagePoint, CadPoint, SurveyPoint]
PdfPathPoint = Union[ObjectPoint, PagePoint]


def point_components(point: Point2) -> Tuple[float, float]:
    if isinstance(point, ObjectPoint):
        return point.p, point.q
    if isinstance(point, PagePoint):
        return point.u, point.v
    if isinstance(point, CadPoint):
        return point.cad_x, point.cad_y
    if isinstance(point, SurveyPoint):
        return point.X, point.Y
    raise TypeError("not a VP 2D point: %r" % (point,))


def point_type(domain: CoordinateDomain) -> Type[Point2]:
    return {
        CoordinateDomain.PDF_OBJECT: ObjectPoint,
        CoordinateDomain.PDF_PAGE: PagePoint,
        CoordinateDomain.DXF_CAD: CadPoint,
        CoordinateDomain.SURVEY: SurveyPoint,
    }[domain]


def make_point(domain: CoordinateDomain, first: float, second: float) -> Point2:
    return point_type(domain)(first, second)


def _pdf_point(point: PdfPathPoint) -> None:
    if not isinstance(point, (ObjectPoint, PagePoint)):
        raise TypeError("PDF path points must be OBJECT or PAGE points")


@dataclass(frozen=True)
class Move:
    to: PdfPathPoint

    def __post_init__(self) -> None:
        _pdf_point(self.to)


@dataclass(frozen=True)
class Line:
    to: PdfPathPoint

    def __post_init__(self) -> None:
        _pdf_point(self.to)


@dataclass(frozen=True)
class CubicBezier:
    control1: PdfPathPoint
    control2: PdfPathPoint
    to: PdfPathPoint

    def __post_init__(self) -> None:
        _pdf_point(self.control1)
        _pdf_point(self.control2)
        _pdf_point(self.to)


@dataclass(frozen=True)
class Close:
    pass


PathCommand = Union[Move, Line, CubicBezier, Close]


@dataclass(frozen=True)
class LineSegment:
    command_index: int
    start: PdfPathPoint
    end: PdfPathPoint


@dataclass(frozen=True)
class CubicBezierSegment:
    command_index: int
    start: PdfPathPoint
    control1: PdfPathPoint
    control2: PdfPathPoint
    end: PdfPathPoint


@dataclass(frozen=True)
class CloseSegment:
    command_index: int
    start: PdfPathPoint
    end: PdfPathPoint


PathSegment = Union[LineSegment, CubicBezierSegment, CloseSegment]


@dataclass(frozen=True)
class PdfPath:
    """Ordered raw PDF commands in exactly one OBJECT or PAGE domain."""

    commands: Tuple[PathCommand, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "commands", tuple(self.commands))
        if not self.commands:
            raise ValueError("a PDF path must contain at least one command")
        if not isinstance(self.commands[0], Move):
            raise ValueError("a PDF path must begin with MOVE")

        expected_type = type(self.commands[0].to)
        active_subpath = False
        for command in self.commands:
            if isinstance(command, Move):
                active_subpath = True
                points = (command.to,)
            elif isinstance(command, Line):
                if not active_subpath:
                    raise ValueError("LINE requires an active subpath")
                points = (command.to,)
            elif isinstance(command, CubicBezier):
                if not active_subpath:
                    raise ValueError("CUBIC_BEZIER requires an active subpath")
                points = (command.control1, command.control2, command.to)
            elif isinstance(command, Close):
                if not active_subpath:
                    raise ValueError("CLOSE requires an active subpath")
                points = ()
            else:
                raise TypeError("unsupported PDF path command: %r" % (command,))
            if any(type(point) is not expected_type for point in points):
                raise ValueError("one PDF path cannot mix OBJECT and PAGE points")

    @property
    def domain(self) -> CoordinateDomain:
        first = self.commands[0]
        assert isinstance(first, Move)
        return first.to.domain

    def segments(self) -> Tuple[PathSegment, ...]:
        """Materialise segments without changing the source command sequence."""
        result = []
        current = None
        subpath_start = None
        for index, command in enumerate(self.commands):
            if isinstance(command, Move):
                current = command.to
                subpath_start = command.to
            elif isinstance(command, Line):
                assert current is not None
                result.append(LineSegment(index, current, command.to))
                current = command.to
            elif isinstance(command, CubicBezier):
                assert current is not None
                result.append(CubicBezierSegment(
                    index, current, command.control1, command.control2,
                    command.to))
                current = command.to
            else:
                assert current is not None and subpath_start is not None
                result.append(CloseSegment(index, current, subpath_start))
                current = subpath_start
        return tuple(result)
