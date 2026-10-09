"""PDF extraction via the existing PDFium probe.

The probe in `poc/pdfium_probe` is the approved PDFium integration and is
reused unchanged. This module runs it and parses its stdout. The PDFium
extraction engine is NOT reimplemented here.

Two consequences worth stating explicitly:

  * The harness inherits the probe's limits. The probe prints a bounded number
    of segments per path; when a path is truncated the probe emits a notice and
    this module records the count, so a reader knows the geometry is a lower
    bound rather than complete.

  * When the probe binary is missing or fails to run, this module raises
    ProbeUnavailable carrying build instructions. It never silently falls back
    to a weaker reader.

Coordinate domains: the probe reports each object with its own matrix, and this
module applies that matrix to produce PAGE (u, v) coordinates. RAW PDF object
coordinates (p, q) are never used as geometry.
"""

import os
import re
import subprocess
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .models import Domain, PdfDocument, PdfPage

DEFAULT_PROBE_RELPATHS = (
    os.path.join("poc", "pdfium_probe", "build", "Release", "pdfium_probe.exe"),
    os.path.join("poc", "pdfium_probe", "build", "pdfium_probe"),
    os.path.join("poc", "pdfium_probe", "build", "Debug", "pdfium_probe.exe"),
)

# Recorded for transparency only: the probe prints a bounded number of
# segments per path and emits a notice when it truncates.
PROBE_SEGMENT_PRINT_CAP = 64


class ProbeUnavailable(RuntimeError):
    """Raised with build instructions when the PDFium probe cannot be used."""


def locate_probe(repo_root: str, explicit: Optional[str] = None) -> str:
    """Find the probe executable, or raise with instructions."""
    candidates: List[str] = []
    if explicit:
        candidates.append(explicit)
    candidates.extend(os.path.join(repo_root, rel)
                      for rel in DEFAULT_PROBE_RELPATHS)
    for c in candidates:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    raise ProbeUnavailable(
        "PDFium probe executable not found.\n"
        "This harness reuses poc/pdfium_probe and does not reimplement PDFium "
        "extraction.\nBuild it first:\n"
        "  call vcvars64.bat\n"
        "  cmake -S poc/pdfium_probe -B poc/pdfium_probe/build "
        "-DPDFium_DIR=<path-to-pdfium>\n"
        "  cmake --build poc/pdfium_probe/build --config Release\n"
        "Looked in:\n  " + "\n  ".join(candidates))


_RE_VERSION = re.compile(r"document file version\s+=\s+(\d+)")
_RE_PAGECOUNT = re.compile(r"document page count\s+=\s+(\d+)")
_RE_PAGE = re.compile(r"^PAGE index\s+=\s+(\d+)\s*$")
_RE_PAGESIZE = re.compile(r"page size \(points\)\s+=\s*u=(\S+)\s+v=(\S+)")
_RE_ROT = re.compile(r"page rotation\s+=\s+(-?\d+)")
_RE_PAGEOBJ = re.compile(r"page object count\s+=\s+(\d+)")
_RE_TRANSP = re.compile(r"page has transparency\s+=\s*(\w+)")
_RE_OBJTYPE = re.compile(r"^\s*\[obj\]\s+type\s+=\s+(\w+)")
_RE_MATRIX = re.compile(
    r"object matrix \(PAGE\)\s*=\s*\[a=(\S+)\s+b=(\S+)\s+c=(\S+)\s+d=(\S+)"
    r"\s+e=(\S+)\s+f=(\S+)\]")
_RE_SEGCOUNT = re.compile(r"^\s*segments\s+=\s+(\d+)")
_RE_SEG = re.compile(
    r"seg\[(\d+)\]\s+(\w+)\s+u=(\S+)\s+v=(\S+)\s+close=(\w+)")
_RE_TRUNC = re.compile(r"further segment\(s\) not shown")


def _detect_encoding(path: str) -> str:
    with open(path, "rb") as fh:
        head = fh.read(2)
    return "utf-16" if head in (b"\xff\xfe", b"\xfe\xff") else "latin-1"


def run_probe(probe_exe: str, pdf_path: str, output_path: str,
              timeout: int = 7200) -> str:
    """Run the probe, capturing stdout to `output_path`."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8", errors="replace") as out:
        proc = subprocess.run([probe_exe, pdf_path], stdout=out,
                              stderr=subprocess.PIPE, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("latin-1", "replace")[-2000:]
        raise ProbeUnavailable(
            "pdfium_probe exited with code %d for %s\n%s"
            % (proc.returncode, pdf_path, tail))
    return output_path


def parse_probe_output(path: str) -> Tuple[Optional[int], List[PdfPage]]:
    """Parse probe stdout into page records carrying PAGE-space LINE segments.

    Each object's own matrix is applied to its raw points before they are
    stored, so every stored coordinate is a PAGE coordinate. Only LINE
    primitives produce straight segments; MOVE starts a subpath and
    CUBIC_BEZIER is counted for forensics but deliberately NOT converted into
    arcs or circles, because premature primitive recognition is exactly what
    the approved geometry model forbids.
    """
    encoding = _detect_encoding(path)
    pages: List[PdfPage] = []
    file_version: Optional[int] = None
    raw_points: List[List[Tuple[Tuple[float, float], str]]] = []
    matrices: List[Set[Tuple[float, ...]]] = []

    matrix: Optional[Tuple[float, ...]] = None
    segcount_declared = 0
    segcount_printed = 0
    obj_type: Optional[str] = None

    def flush_object(page: Optional[PdfPage]) -> None:
        nonlocal segcount_declared, segcount_printed, obj_type
        if page is None:
            segcount_declared = segcount_printed = 0
            obj_type = None
            return
        page.declared_segment_count += segcount_declared
        page.printed_segment_count += segcount_printed
        segcount_declared = 0
        segcount_printed = 0
        obj_type = None

    with open(path, "r", encoding=encoding, errors="replace") as fh:
        for line in fh:
            m = _RE_VERSION.search(line)
            if m:
                file_version = int(m.group(1))
                continue
            m = _RE_PAGECOUNT.search(line)
            if m:
                continue
            m = _RE_PAGE.match(line)
            if m:
                flush_object(pages[-1] if pages else None)
                pages.append(PdfPage(page_index=int(m.group(1)),
                                     width_points=0.0, height_points=0.0))
                raw_points.append([])
                matrices.append(set())
                continue
            if not pages:
                continue
            page = pages[-1]

            m = _RE_PAGESIZE.search(line)
            if m:
                page.width_points = float(m.group(1))
                page.height_points = float(m.group(2))
                continue
            m = _RE_ROT.search(line)
            if m:
                page.rotation_deg = int(m.group(1))
                continue
            m = _RE_PAGEOBJ.search(line)
            if m:
                page.object_count = int(m.group(1))
                continue
            m = _RE_TRANSP.search(line)
            if m:
                page.has_transparency = (m.group(1).lower() == "true")
                continue
            m = _RE_MATRIX.search(line)
            if m:
                matrix = tuple(float(g) for g in m.groups())
                matrices[-1].add(matrix)
                continue
            m = _RE_OBJTYPE.match(line)
            if m:
                flush_object(page)
                obj_type = m.group(1)
                # A matrix belongs to one object. Never reuse the preceding
                # object's matrix if malformed probe output omits this one.
                matrix = None
                if obj_type == "PATH":
                    page.path_count += 1
                elif obj_type == "TEXT":
                    page.text_count += 1
                elif obj_type == "IMAGE":
                    page.image_count += 1
                elif obj_type == "FORM":
                    page.form_count += 1
                continue
            m = _RE_SEGCOUNT.match(line)
            if m:
                segcount_declared += int(m.group(1))
                continue
            m = _RE_SEG.search(line)
            if m:
                kind = m.group(2)
                u = float(m.group(3))
                v = float(m.group(4))
                if kind == "MOVE":
                    page.move_count += 1
                elif kind == "LINE":
                    page.line_count += 1
                elif kind == "CUBIC_BEZIER":
                    page.cubic_count += 1
                if m.group(5).lower() == "true":
                    page.close_flag_count += 1
                if matrix is not None:
                    a, b, c, d, e, f = matrix
                    raw_points[-1].append(
                        ((a * u + c * v + e, b * u + d * v + f), kind))
                segcount_printed += 1
                continue
            if _RE_TRUNC.search(line):
                page.truncation_notices += 1
        flush_object(pages[-1] if pages else None)

    for page, points in zip(pages, raw_points):
        rebuilt: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        current: Optional[Tuple[float, float]] = None
        for point, kind in points:
            if kind == "MOVE":
                current = point
            elif kind == "LINE":
                if current is not None and point != current:
                    rebuilt.append((current, point))
                current = point
            else:
                # PDFium exposes one endpoint per CUBIC_BEZIER segment in the
                # PoC output. The curve is not converted into a line, but its
                # endpoint becomes the current path position, so a following
                # LINE must begin there. Resetting current here discarded every
                # line that followed a curve and caused the RH1 regression to
                # lose the previously confirmed B2 relationship.
                current = point
        page.segments = rebuilt
        page.segment_domain = Domain.PAGE

    for page, ms in zip(pages, matrices):
        page.distinct_matrix_count = len(ms)

    return file_version, pages


def build_pdf_document(path: str, probe_exe: str, work_dir: str,
                       sha256: Optional[str] = None,
                       size_bytes: Optional[int] = None) -> PdfDocument:
    """Run the probe on a PDF and assemble a PdfDocument.

    `work_dir` receives the raw probe stdout, which is large and must live
    outside the repository and outside the results directory.
    """
    from .dxf_extract import sha256_of
    os.makedirs(work_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    probe_out = os.path.join(work_dir, stem + ".probe.txt")
    run_probe(probe_exe, path, probe_out)
    file_version, pages = parse_probe_output(probe_out)

    from .forensics import structural_scan
    scan = structural_scan(path)
    return PdfDocument(
        path=os.path.abspath(path), name=os.path.basename(path),
        sha256=sha256 or sha256_of(path),
        size_bytes=size_bytes if size_bytes is not None
        else os.path.getsize(path),
        pdf_version=scan["pdf_version"], creator=scan["creator"],
        producer=scan["producer"], title=scan["title"],
        creation_date=scan["creation_date"], mod_date=scan["mod_date"],
        encrypted=scan["encrypted"], uses_xref_stream=scan["xref_stream"],
        uses_object_streams=scan["object_streams"],
        media_boxes=scan["media_boxes"],
        crop_box_count=scan["crop_box_count"],
        trim_box_count=scan["trim_box_count"],
        ocg_status=scan["ocg_status"], ocg_evidence=scan["ocg_evidence"],
        ocg_object_count=scan["ocg_object_count"],
        pdfium_file_version=file_version, probe_exe=probe_exe, pages=pages)
