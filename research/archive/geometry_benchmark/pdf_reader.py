"""Parse pdfium_probe stdout into PAGE-space geometry. Research tool.

COORDINATE CONVENTION (never mix these):
    PAGE   space: u = horizontal, v = vertical   (points)
    SURVEY space: X = Northing,  Y = Easting    (metres)

This module only ever produces PAGE-space (u, v). It never emits SURVEY X/Y.

The probe reports segment coordinates in the object's own (local) space together
with that object's transformation matrix. Applying the matrix is what produces
true PAGE coordinates, so that is done here:

    u' = a*u + c*v + e
    v' = b*u + d*v + f
"""

import math
import re
from collections import OrderedDict

RE_OBJ = re.compile(r"^\s*\[obj\]\s+type\s+=\s+(\w+)")
RE_MATRIX = re.compile(
    r"object matrix \(PAGE\)\s*=\s*\[a=(\S+)\s+b=(\S+)\s+c=(\S+)\s+d=(\S+)\s+e=(\S+)\s+f=(\S+)\]")
RE_SEGCOUNT = re.compile(r"^\s*segments\s+=\s+(\d+)")
RE_SEG = re.compile(
    r"seg\[(\d+)\]\s+(\w+)\s+u=(\S+)\s+v=(\S+)\s+close=(\w+)")
RE_PAGE = re.compile(r"^PAGE index\s+=\s+(\d+)")
RE_PAGESIZE = re.compile(r"page size \(points\)\s+=\s*u=(\S+)\s+v=(\S+)")
RE_PAGEOBJ = re.compile(r"page object count\s+=\s+(\d+)")


class PdfPath:
    __slots__ = ("page", "index", "matrix", "segs", "declared_segments",
                 "truncated", "kind", "matrix_missing")

    def __init__(self, page, index, matrix):
        self.page = page
        self.index = index
        self.matrix = matrix          # (a, b, c, d, e, f)
        self.segs = []                # list of (kind, u, v, close) in PAGE space
        self.declared_segments = 0
        self.truncated = False
        self.kind = None
        self.matrix_missing = False

    def apply(self, u, v):
        a, b, c, d, e, f = self.matrix
        return (a * u + c * v + e, b * u + d * v + f)


def parse(path_txt):
    """Return (paths, pages). Segments already transformed to PAGE space."""
    paths = []
    pages = []

    page_idx = -1
    cur = None
    obj_no = None
    last_matrix = None
    last_page = None
    pending_page = None

    # The probe artifacts were captured through a PowerShell redirect and are
    # therefore UTF-16LE with a BOM. Detect and handle both encodings.
    with open(path_txt, "rb") as raw:
        head = raw.read(2)
    enc = "utf-16" if head in (b"\xff\xfe", b"\xfe\xff") else "latin-1"

    with open(path_txt, "r", encoding=enc) as fh:
        for line in fh:
            m = RE_PAGE.match(line)
            if m:
                page_idx = int(m.group(1))
                pages.append({"index": page_idx, "size": None,
                              "object_count": None})
                cur = None
                continue
            m = RE_PAGESIZE.search(line)
            if m and pages:
                pages[-1]["size"] = (float(m.group(1)), float(m.group(2)))
                continue
            m = RE_PAGEOBJ.search(line)
            if m and pages:
                pages[-1]["object_count"] = int(m.group(1))
                continue
            m = RE_MATRIX.search(line)
            if m:
                last_matrix = tuple(float(g) for g in m.groups())
                continue
            m = RE_OBJ.match(line)
            if m:
                cur = None
                kind = m.group(1)
                cur_kind = kind
                if kind == "PATH":
                    cur = PdfPath(page_idx, obj_no, last_matrix)
                    cur.kind = cur_kind
                    if cur.matrix is None:
                        cur.matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
                        cur.matrix_missing = True
                    paths.append(cur)
                else:
                    cur = None
                obj_no = (obj_no + 1) if obj_no is not None else 0
                continue
            m = RE_SEGCOUNT.match(line)
            if m and cur is not None:
                cur.declared_segments = int(m.group(1))
                continue
            m = RE_SEG.search(line)
            if m and cur is not None:
                kind = m.group(2)
                lu = float(m.group(3))
                lv = float(m.group(4))
                close = (m.group(5) == "true")
                u, v = cur.apply(lu, lv)
                cur.segs.append((kind, u, v, close))
                continue

    for p in paths:
        if len(p.segs) < p.declared_segments:
            p.truncated = True
        if p.matrix is None:
            # No matrix line was seen for this object; fall back to identity
            # but record it so the caller can quantify the impact.
            p.matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
            p.matrix_missing = True
    return paths, pages


def segments_of(path_obj):
    """Yield PAGE-space straight segments (a,b) from a parsed path."""
    out = []
    cur_pt = None
    for kind, u, v, close in path_obj.segs:
        if kind == "MOVE":
            cur_pt = (u, v)
        elif kind == "LINE":
            if cur_pt is not None:
                out.append((cur_pt, (u, v)))
            cur_pt = (u, v)
        elif kind == "CUBIC_BEZIER":
            # Segment carries one control point; the curve continues.
            cur_pt = (u, v)
        else:
            cur_pt = (u, v)
    return out


def bezier_runs(path_obj):
    """Yield runs of consecutive CUBIC_BEZIER segment points."""
    runs = []
    run = []
    for kind, u, v, _close in path_obj.segs:
        if kind == "CUBIC_BEZIER":
            run.append((u, v))
        else:
            if run:
                runs.append(run)
            run = []
    if run:
        runs.append(run)
    return runs


def matrix_of(path_obj):
    return path_obj.matrix


def det(m):
    a, b, c, d = m[0], m[1], m[2], m[3]
    return a * d - b * c