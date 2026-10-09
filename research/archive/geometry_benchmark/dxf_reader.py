"""Read-only DXF parser for the VP GeoConvert geometry benchmark.

Research tool. Not production code.

COORDINATE CONVENTION (never mix these):
    DXF / SURVEY space:  X = Northing, Y = Easting  (metres, as authored)
    PDF  / PAGE   space:  u = horizontal, v = vertical

This module only ever produces SURVEY-space (X, Y). It never emits PAGE u/v.

Strictly 2D: group codes 30/31/32 (Z) are ignored everywhere.
"""

import math
import re
from collections import OrderedDict

# DXF group codes we care about (2D only).
G_X = 10   # X / first point X
G_Y = 20   # Y / first point Y
G_X2 = 11  # second point X
G_Y2 = 21  # second point Y
G_RADIUS = 40
G_START_ANGLE = 50
G_END_ANGLE = 51
G_COUNT = 90       # LWPOLYLINE vertex count
G_FLAGS = 70       # LWPOLYLINE flags (1 = closed)
G_BULGE = 42       # LWPOLYLINE bulge (arc segment)
G_TEXT = 1
G_NAME = 2
G_INSERT = 2
G_ISC = 1
G_ISC2 = 2
G_ISC3 = 3


class Entity:
    """A 2D DXF entity in SURVEY space."""

    __slots__ = ("etype", "layer", "pts", "closed", "center", "radius",
                 "a0", "a1", "name", "sx", "sy", "rot", "raw")

    def __init__(self, etype, layer):
        self.etype = etype
        self.layer = layer
        self.pts = []        # SURVEY (X, Y) vertices
        self.closed = False
        self.center = None   # SURVEY (X, Y)
        self.radius = None
        self.a0 = None       # start angle, degrees, CCW from +X axis
        self.a1 = None       # end angle, degrees
        self.name = None
        self.sx = 1.0
        self.sy = 1.0
        self.rot = 0.0
        self.raw = {}

    def __repr__(self):
        return "<%s layer=%s n=%d>" % (self.etype, self.layer, len(self.pts))


def read_pairs(path):
    """Yield (group_code:int, value:str) pairs from a DXF file, read-only."""
    with open(path, "r", encoding="latin-1") as fh:
        while True:
            code_line = fh.readline()
            if code_line == "":
                return
            val_line = fh.readline()
            if val_line == "":
                return
            code = code_line.strip()
            if not re.match(r"^-?\d+$", code):
                # Malformed pair; resynchronise on the next numeric line.
                continue
            yield int(code), val_line.rstrip("\n").rstrip("\r")


def _f(value):
    try:
        return float(value)
    except ValueError:
        return None


def parse(path):
    """Parse a DXF. Returns (entities, header, blocks, tables).

    entities : list[Entity]  in SURVEY space
    header   : dict
    blocks   : dict name -> list[Entity]
    """
    header = {}
    blocks = OrderedDict()
    entities = []

    section = None
    in_header = False
    cur = None            # current Entity
    cur_block = None      # block name currently being read
    cur_block_key = None

    pairs = list(read_pairs(path))
    n = len(pairs)

    # --- helper closures -------------------------------------------------
    def flush():
        nonlocal cur
        if cur is not None:
            if section == "ENTITIES":
                entities.append(cur)
            elif section == "BLOCKS" and cur_block is not None:
                blocks.setdefault(cur_block, []).append(cur)
        cur = None

    i = 0
    last_header_var = None

    while i < n:
        code, value = pairs[i]

        if code == 0:
            keyword = value.strip()

            if keyword == "SECTION":
                section = None
                cur = None
                i += 1
                continue

            if keyword == "ENDSEC":
                flush()
                if section == "ENTITIES":
                    entities_done = True
                section = None
                cur_block = None
                cur_block_key = None
                i += 1
                continue

            if keyword == "EOF":
                flush()
                break

            if keyword == "BLOCK":
                flush()
                cur = None
                i += 1
                continue

            if keyword == "ENDBLK":
                flush()
                cur_block = None
                cur_block_key = None
                i += 1
                continue

            # A real entity record begins here.
            flush()
            if keyword == "VERTEX":
                # VERTEX records belong to the preceding POLYLINE; we do not
                # implement 2D POLYLINE (the benchmark DXF has none).
                cur = Entity("VERTEX", None)
            elif keyword in ("SEQEND", "ATTRIB", "ATTDEF"):
                cur = None
            else:
                cur = Entity(keyword, None)
            i += 1
            continue

        if code == 2 and cur is None and section is None:
            # SECTION name: the pair before any entity.
            section = value.strip()
            in_header = (section == "HEADER")
            i += 1
            continue

        if in_header and code == 9:
            last_header_var = value.strip()
            i += 1
            continue

        if in_header and last_header_var is not None:
            if code in (10, 20, 40, 3, 1) and last_header_var not in header:
                val = _f(value)
                header[last_header_var] = val if val is not None else value.strip()
            if code == 2 and last_header_var == "$ACADVER":
                header[last_header_var] = value.strip()
            i += 1
            continue

        if cur is None:
            # Records outside entity context that we still want: BLOCK name.
            if code == 2 and section == "BLOCKS" and cur_block is None:
                cur_block = value.strip()
                cur_block_key = value.strip()
            i += 1
            continue

        # --- inside an entity ------------------------------------------
        if code == 8:
            cur.layer = value.strip()
        elif code == G_X:
            cur.raw.setdefault("x", []).append(_f(value))
        elif code == G_Y:
            cur.raw.setdefault("y", []).append(_f(value))
        elif code == G_X2:
            cur.raw.setdefault("x2", []).append(_f(value))
        elif code == G_Y2:
            cur.raw.setdefault("y2", []).append(_f(value))
        elif code == G_RADIUS:
            cur.radius = _f(value)
        elif code == G_START_ANGLE:
            cur.a0 = _f(value)
        elif code == G_END_ANGLE:
            cur.a1 = _f(value)
        elif code == G_FLAGS:
            try:
                cur.closed = (int(float(value)) & 1) == 1
            except ValueError:
                pass
        elif code == G_BULGE:
            cur.raw.setdefault("bulge", []).append(_f(value))
        elif code == G_NAME and cur.etype == "INSERT":
            cur.name = value.strip()
        elif code == 41 and cur.etype == "INSERT":
            cur.sx = _f(value) if _f(value) is not None else 1.0
        elif code == 42 and cur.etype == "INSERT":
            sy = _f(value)
            if sy is not None:
                cur.sy = sy
        elif code == 50 and cur.etype == "INSERT":
            cur.rot = _f(value) or 0.0

        i += 1

    _finalise(entities, header)
    for name, lst in blocks.items():
        _finalise(lst, header)
    return entities, header, blocks, pairs


def _finalise(entities, header):
    """Convert raw group-code arrays into typed geometry. 2D only."""
    for e in entities:
        xs = e.raw.get("x", [])
        ys = e.raw.get("y", [])
        pairs_xy = list(zip(xs, ys))

        if e.etype == "LINE":
            x2 = e.raw.get("x2", [None])[0]
            y2 = e.raw.get("y2", [None])[0]
            if pairs_xy and x2 is not None and y2 is not None:
                p0 = pairs_xy[0]
                p1 = (x2, y2)
                e.pts = [p0, p1]

        elif e.etype == "LWPOLYLINE":
            e.pts = [p for p in pairs_xy if p[0] is not None and p[1] is not None]

        elif e.etype == "POLYLINE" or e.etype == "VERTEX":
            e.pts = [p for p in pairs_xy if p[0] is not None and p[1] is not None]

        elif e.etype in ("CIRCLE", "ARC"):
            if pairs_xy:
                e.center = pairs_xy[0]
            # radius already captured; angles for ARC only

        elif e.etype == "INSERT":
            if pairs_xy:
                e.center = pairs_xy[0]

        elif e.etype == "TEXT" or e.etype == "MTEXT":
            if pairs_xy:
                e.center = pairs_xy[0]


def entity_counts(entities):
    counts = OrderedDict()
    for e in entities:
        counts[e.etype] = counts.get(e.etype, 0) + 1
    return counts