"""DXF geometry extraction for the Set B1 benchmark.

Research tool. Read-only.

Coordinate domain: DXF raw CAD coordinates  cad_x, cad_y.
Units are read from $INSUNITS and reported as metadata only; this module makes
no claim that cad_x == SURVEY X.

Strictly 2D. Z group codes are ignored.
"""

import math
import re
from collections import Counter


def pairs(path):
    with open(path, "r", encoding="latin-1") as fh:
        while True:
            cl = fh.readline()
            if cl == "":
                return
            vl = fh.readline()
            if vl == "":
                return
            c = cl.strip()
            if not re.match(r"^-?\d+$", c):
                continue
            yield int(c), vl.rstrip("\n").rstrip("\r")


def f(v):
    try:
        return float(v)
    except ValueError:
        return None


class Entity:
    __slots__ = ("idx", "etype", "layer", "segs", "center", "radius",
                 "a0", "a1", "closed", "text", "pts", "xs", "ys", "x2s",
                 "y2s", "seq")

    def __init__(self, idx, etype, layer=None):
        self.idx = idx
        self.etype = etype
        self.layer = layer
        self.segs = []
        self.center = None
        self.radius = None
        self.a0 = None
        self.a1 = None
        self.closed = False
        self.text = None
        self.pts = []
        self.xs = []
        self.ys = []
        self.x2s = []
        self.y2s = []
        self.seq = None

    def __repr__(self):
        return "<#%d %s %s segs=%d>" % (self.idx, self.etype, self.layer,
                                        len(self.segs))


CURVE_TYPES = ("CIRCLE", "ARC")
POINT_TYPES = ("INSERT", "TEXT", "MTEXT", "POINT")


def load(path):
    """Return (entities, header). Entities carry 2D CAD geometry."""
    header = {}
    entities = []
    cur = None
    section = None
    last_var = None

    for code, value in pairs(path):
        if code == 0:
            kw = value.strip()
            if kw == "SECTION":
                section = None
                cur = None
                continue
            if kw in ("ENDSEC", "EOF"):
                section = None
                cur = None
                continue
            if kw == "SEQEND":
                cur = None
                continue
            if kw in ("BLOCK", "ENDBLK", "TABLE", "ENDTAB", "CLASS",
                      "ENDCLASS", "SECTION"):
                continue
            if section == "ENTITIES":
                cur = Entity(len(entities), kw)
                entities.append(cur)
                if kw == "POLYLINE":
                    cur.seq = []
            continue

        if section is None and code == 2:
            section = value.strip()
            continue

        if section == "HEADER":
            if code == 9:
                last_var = value.strip()
            elif last_var:
                header.setdefault(last_var, []).append((code, value.strip()))
            continue

        if section != "ENTITIES" or cur is None:
            continue

        if code == 8:
            cur.layer = value.strip()
        elif code == 10:
            v = f(value)
            if v is not None:
                cur.xs.append(v)
        elif code == 20:
            v = f(value)
            if v is not None:
                cur.ys.append(v)
        elif code == 11:
            v = f(value)
            if v is not None:
                cur.x2s.append(v)
        elif code == 21:
            v = f(value)
            if v is not None:
                cur.y2s.append(v)
        elif code == 40 and cur.etype in CURVE_TYPES:
            cur.radius = f(value)
        elif code == 50:
            cur.a0 = f(value)
        elif code == 51:
            cur.a1 = f(value)
        elif code == 70:
            try:
                cur.closed = (int(float(value)) & 1) == 1
            except ValueError:
                pass
        elif code == 1:
            cur.text = value

    _finalise(entities)
    return entities, header


def _polyline_segments(pts, closed):
    segs = []
    for i in range(len(pts) - 1):
        segs.append((pts[i], pts[i + 1]))
    if closed and len(pts) > 2 and pts[0] != pts[-1]:
        segs.append((pts[-1], pts[0]))
    return segs


def _finalise(entities):
    # attach VERTEX runs to their owning POLYLINE
    poly = None
    kept = []
    for e in entities:
        if e.etype == "POLYLINE":
            poly = e
            e.pts = []
            kept.append(e)
            continue
        if e.etype == "VERTEX" and poly is not None:
            poly.pts.extend(zip(e.xs, e.ys))
            continue
        kept.append(e)
    entities[:] = kept

    for e in entities:
        pts = list(zip(e.xs, e.ys))
        if e.etype == "LINE":
            if pts and e.x2s and e.y2s:
                e.segs = [(pts[0], (e.x2s[0], e.y2s[0]))]
        elif e.etype == "LWPOLYLINE":
            e.segs = _polyline_segments(pts, e.closed)
        elif e.etype == "POLYLINE":
            e.segs = _polyline_segments(e.pts, e.closed)
        elif e.etype in CURVE_TYPES:
            if pts:
                e.center = pts[0]
        elif e.etype in POINT_TYPES:
            if pts:
                e.center = pts[0]


def polyline_entities(entities):
    return [e for e in entities if e.segs]


def all_segments(entities):
    out = []
    for e in entities:
        for a, b in e.segs:
            out.append((a, b, e))
    return out


def stats(segs):
    lens = sorted((math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in segs),
                  reverse=True)
    if not lens:
        return None
    return {"n": len(lens), "max": lens[0], "min": lens[-1],
            "median": lens[len(lens) // 2], "sum": sum(lens)}


def counts(entities):
    return Counter(e.etype for e in entities)