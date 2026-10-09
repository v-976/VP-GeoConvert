"""DXF extraction. Read-only, 2D, entity-level.

Scope:
  * Entities with straight 2D geometry become matching features: LINE,
    LWPOLYLINE, POLYLINE (with its VERTEX run), SOLID/3DFACE corner rings.
  * CURVE_TYPES (CIRCLE, ARC, ELLIPSE) and INSERT/TEXT/MTEXT/POINT are
    inventoried and their centre carried on the entity, but they are NOT used
    as matching features in this harness version.
  * Group codes in the Z family (30-39) are never read as geometry. Z is
    ignored, strictly 2D.

No CAD to SURVEY conversion happens anywhere in this module. The only claim
made about the coordinates is that they are DXF CAD values.
"""

import hashlib
import os
import re
from collections import Counter
from typing import Dict, Iterator, List, Optional, Tuple

from .models import DxfDocument, Domain, GeometryEntity, UnitInfo
from .units import unit_from_insunits

Z_CODES = frozenset(range(30, 40))

CURVE_TYPES = ("CIRCLE", "ARC", "ELLIPSE")
ANCHOR_TYPES = ("INSERT", "TEXT", "MTEXT", "POINT")
POLYLINE_TYPES = ("LWPOLYLINE", "POLYLINE", "POLYLINE2D", "POLYLINE3D")
INVENTORIED_TYPES = ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE",
                     "HATCH", "INSERT", "TEXT", "MTEXT", "SPLINE", "ELLIPSE",
                     "POINT", "SOLID", "3DFACE", "TRACE", "LEADER",
                     "MULTILEADER", "DIMENSION")

_HEADER_VARS = ("$ACADVER", "$ACADMAINTVER", "$DWGCODEPAGE", "$LASTSAVEDBY",
                "$INSUNITS", "$MEASUREMENT", "$LUNITS", "$LIMMIN", "$LIMMAX",
                "$EXTMIN", "$EXTMAX")


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pairs(path: str) -> Iterator[Tuple[int, str]]:
    """Stream (group_code, value) pairs.

    DXF is a flat text format of alternating code line and value line. A line
    whose code is not an integer means the file is not in the expected shape,
    so it is skipped rather than aborting the whole inventory.
    """
    with open(path, "r", encoding="latin-1", errors="replace") as fh:
        while True:
            code_line = fh.readline()
            if code_line == "":
                return
            value_line = fh.readline()
            if value_line == "":
                return
            stripped = code_line.strip()
            if not re.match(r"^-?\d+$", stripped):
                continue
            yield int(stripped), value_line.rstrip("\n").rstrip("\r")


def _f(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class _RawEntity:
    """Accumulated group codes for one entity, before geometry assembly."""

    __slots__ = ("etype", "layer", "xs", "ys", "x2s", "y2s", "radius",
                 "a0", "a1", "flags", "text")

    def __init__(self, etype: str) -> None:
        self.etype = etype
        self.layer: Optional[str] = None
        self.xs: List[float] = []
        self.ys: List[float] = []
        self.x2s: List[float] = []
        self.y2s: List[float] = []
        self.radius: Optional[float] = None
        self.a0: Optional[float] = None
        self.a1: Optional[float] = None
        self.flags: Optional[int] = None
        self.text: Optional[str] = None

    def points(self) -> List[Tuple[float, float]]:
        n = min(len(self.xs), len(self.ys))
        return [(self.xs[i], self.ys[i]) for i in range(n)]


def _chain(points: List[Tuple[float, float]], closed: bool
           ) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    segs: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    for i in range(len(points) - 1):
        segs.append((points[i], points[i + 1]))
    if closed and len(points) > 2 and points[0] != points[-1]:
        segs.append((points[-1], points[0]))
    return segs


def load_dxf(path: str) -> DxfDocument:
    """Parse a DXF into a DxfDocument. The file is never modified."""
    header: Dict[str, List[Tuple[int, str]]] = {}
    sections: List[str] = []
    section: Optional[str] = None
    last_var: Optional[str] = None
    raws: List[_RawEntity] = []
    current: Optional[_RawEntity] = None
    polyline: Optional[List[Tuple[float, float]]] = None
    current_poly_raw: Optional[_RawEntity] = None
    counts: Counter = Counter()

    for code, value in _pairs(path):
        if code == 0:
            keyword = value.strip()
            if keyword == "SECTION":
                section = None
                current = None
                continue
            if keyword in ("ENDSEC", "EOF"):
                section = None
                current = None
                current_poly_raw = None
                polyline = None
                continue
            if keyword == "SEQEND":
                current = None
                current_poly_raw = None
                polyline = None
                continue
            if keyword in ("BLOCK", "ENDBLK", "TABLE", "ENDTAB", "CLASS",
                           "ENDCLASS", "SECTION"):
                current = None
                continue
            if section == "ENTITIES":
                if keyword == "VERTEX":
                    if current_poly_raw is None:
                        current = _RawEntity(keyword)
                        current_poly_raw = current
                        raws.append(current)
                        counts[keyword] += 1
                    else:
                        current = _RawEntity(keyword)
                        raws.append(current)
                        counts[keyword] += 1
                    continue
                if keyword == "POLYLINE":
                    current = _RawEntity(keyword)
                    current_poly_raw = current
                    polyline = []
                    raws.append(current)
                    counts[keyword] += 1
                    continue
                current = _RawEntity(keyword)
                current_poly_raw = None
                polyline = None
                raws.append(current)
                counts[keyword] += 1
            continue

        if section is None and code == 2:
            section = value.strip()
            sections.append(section)
            continue

        if section == "HEADER":
            if code == 9:
                last_var = value.strip()
            elif last_var:
                header.setdefault(last_var, []).append((code, value.strip()))
            continue

        if section != "ENTITIES" or current is None:
            continue
        if code in Z_CODES:
            continue                       # strictly 2D
        if code == 8:
            current.layer = value.strip()
        elif code == 10:
            v = _f(value)
            if v is not None:
                current.xs.append(v)
        elif code == 20:
            v = _f(value)
            if v is not None:
                current.ys.append(v)
        elif code == 11:
            v = _f(value)
            if v is not None:
                current.x2s.append(v)
        elif code == 21:
            v = _f(value)
            if v is not None:
                current.y2s.append(v)
        elif code == 40 and current.etype in CURVE_TYPES:
            current.radius = _f(value)
        elif code == 50:
            current.a0 = _f(value)
        elif code == 51:
            current.a1 = _f(value)
        elif code == 70:
            try:
                current.flags = int(float(value))
            except ValueError:
                current.flags = None
        elif code == 1:
            current.text = value

    def hv(name: str) -> Optional[str]:
        rows = header.get(name)
        return rows[0][1] if rows else None

    def hn(name: str) -> Optional[float]:
        rows = header.get(name)
        return _f(rows[0][1]) if rows else None

    def hxy(name: str) -> Optional[Tuple[float, float]]:
        """Read only the 2D components of a header point.

        Codes 10 and 20 are cad_x/cad_y. Code 30, if present, is deliberately
        ignored: the harness is strictly 2D and does not invent a Z=0 value.
        """
        rows = header.get(name, ())
        cad_x = next((_f(v) for code, v in rows if code == 10), None)
        cad_y = next((_f(v) for code, v in rows if code == 20), None)
        if cad_x is None or cad_y is None:
            return None
        return (cad_x, cad_y)

    units = unit_from_insunits(hv("$INSUNITS"))

    extmin = hxy("$EXTMIN")
    extmax = hxy("$EXTMAX")

    entities: List[GeometryEntity] = []
    entity_counts: Counter = Counter()
    poly_points: Dict[int, List[Tuple[float, float]]] = {}
    poly_ids: Dict[int, str] = {}

    # First pass: assign ids and gather vertices per POLYLINE.
    seq = 0
    for raw in raws:
        if raw.etype == "VERTEX":
            continue
        eid = "%d:%s" % (seq, raw.etype)
        seq += 1
        entity_counts[raw.etype] += 1
        pts = raw.points()
        segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        center = None
        closed = bool(raw.flags is not None and (raw.flags & 1))

        if raw.etype == "LINE":
            if pts and raw.x2s and raw.y2s:
                segments = [(pts[0], (raw.x2s[0], raw.y2s[0]))]
        elif raw.etype == "LWPOLYLINE":
            segments = _chain(pts, closed)
        elif raw.etype == "POLYLINE":
            poly_points[id(raw)] = []
            poly_ids[id(raw)] = eid
        elif raw.etype in ("SOLID", "3DFACE", "TRACE"):
            segments = _chain(pts, closed)
        elif raw.etype in CURVE_TYPES:
            center = pts[0] if pts else None
        elif raw.etype in ANCHOR_TYPES:
            center = pts[0] if pts else None

        entities.append(GeometryEntity(
            entity_id=eid, entity_type=raw.etype, layer=raw.layer,
            segments=segments, domain=Domain.CAD, center=center,
            radius=raw.radius, start_angle=raw.a0, end_angle=raw.a1,
            closed=closed, text=raw.text))

    # Attach each VERTEX run to its POLYLINE.
    vertex_buffer = None
    by_poly_points: Dict[str, List[Tuple[float, float]]] = {}
    for raw in raws:
        if raw.etype == "POLYLINE":
            vertex_buffer = []
            eid = poly_ids.get(id(raw))
            if eid is not None:
                by_poly_points[eid] = vertex_buffer
            continue
        if raw.etype == "VERTEX" and vertex_buffer is not None:
            vertex_buffer.extend(raw.points())

    for e in entities:
        if e.entity_type == "POLYLINE":
            pts = by_poly_points.get(e.entity_id, [])
            e.segments = _chain(pts, e.closed)

    all_x: List[float] = []
    all_y: List[float] = []
    for e in entities:
        bb = e.bbox()
        if bb:
            all_x.extend((bb[0], bb[1]))
            all_y.extend((bb[2], bb[3]))

    name = os.path.basename(path)
    return DxfDocument(
        path=os.path.abspath(path), name=name,
        sha256=sha256_of(path), size_bytes=os.path.getsize(path),
        acad_version=hv("$ACADVER"), last_saved_by=hv("$LASTSAVEDBY"),
        measurement=None if hv("$MEASUREMENT") is None
        else int(float(hv("$MEASUREMENT"))),
        units=units, sections=sections,
        layers=sorted({e.layer for e in entities if e.layer}),
        extmin=extmin, extmax=extmax,
        entity_counts=dict(entity_counts), entities=entities,
        cad_x_span=(max(all_x) - min(all_x)) if all_x else None,
        cad_y_span=(max(all_y) - min(all_y)) if all_y else None)
