"""Result serialisation and human-readable reporting.

The JSON is the primary artefact: it holds every measurement so that later
benchmark stages can be compared without re-parsing 30-50 MB of probe stdout.
The Markdown report is a rendering of that JSON, never a separate calculation.

No production language appears in either output. There is no verdict, no
confidence score and no acceptance threshold. Candidate rows carry descriptive
research labels together with the number behind each one.
"""

import json
import os
import platform
import sys
from typing import Dict, List, Optional, Sequence

from . import HARNESS_VERSION
from .forensics import marker_lines
from .matching import MatchConfig
from .models import (CandidateEvidence, Dataset, DxfDocument, PdfDocument,
                     ResidualStats)
from .units import residual_unit_label
from .validation import (residual_report_lines, threshold_pass_rates,
                         tolerance_in_cad_units)


def environment_block() -> dict:
    return {
        "harness_version": HARNESS_VERSION,
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
    }


def inventory_to_dict(dataset: Dataset) -> dict:
    return {
        "dataset": dataset.provenance(),
        "skipped_files": dataset.skipped_files,
        "pdfs": [],
        "dxfs": [],
    }


def pdf_to_dict(pdf: PdfDocument) -> dict:
    return {
        "name": pdf.name, "sha256": pdf.sha256, "size_bytes": pdf.size_bytes,
        "pdf_version": pdf.pdf_version, "creator": pdf.creator,
        "producer": pdf.producer, "title": pdf.title,
        "creation_date": pdf.creation_date, "mod_date": pdf.mod_date,
        "encrypted": pdf.encrypted, "xref_stream": pdf.uses_xref_stream,
        "object_streams": pdf.uses_object_streams,
        "media_boxes": [list(b) for b in pdf.media_boxes],
        "crop_box_count": pdf.crop_box_count,
        "trim_box_count": pdf.trim_box_count,
        "ocg_status": pdf.ocg_status, "ocg_evidence": pdf.ocg_evidence,
        "ocg_object_count": pdf.ocg_object_count,
        "pdfium_file_version": pdf.pdfium_file_version,
        "page_count": pdf.page_count,
        "pages": [{
            "page_index": p.page_index,
            "width_points": p.width_points, "height_points": p.height_points,
            "rotation_deg": p.rotation_deg, "object_count": p.object_count,
            "path_count": p.path_count, "text_count": p.text_count,
            "image_count": p.image_count, "form_count": p.form_count,
            "move_count": p.move_count, "line_count": p.line_count,
            "cubic_count": p.cubic_count,
            "close_flag_count": p.close_flag_count,
            "declared_segment_count": p.declared_segment_count,
            "printed_segment_count": p.printed_segment_count,
            "distinct_matrix_count": p.distinct_matrix_count,
            "truncation_notices": p.truncation_notices,
            "has_transparency": p.has_transparency,
            "page_line_segments": len(p.segments),
            "segment_domain": p.segment_domain.value,
        } for p in pdf.pages],
    }


def dxf_to_dict(dxf: DxfDocument) -> dict:
    return {
        "name": dxf.name, "sha256": dxf.sha256, "size_bytes": dxf.size_bytes,
        "acad_version": dxf.acad_version, "last_saved_by": dxf.last_saved_by,
        "measurement": dxf.measurement,
        "units_code": dxf.units.code, "units_name": dxf.units.name,
        "units_declared": dxf.units.declared,
        "metres_per_unit": dxf.units.metres_per_unit,
        "sections": dxf.sections,
        "layer_count": len(dxf.layers), "layers": dxf.layers,
        "extmin": list(dxf.extmin) if dxf.extmin else None,
        "extmax": list(dxf.extmax) if dxf.extmax else None,
        "cad_x_span": dxf.cad_x_span, "cad_y_span": dxf.cad_y_span,
        "entity_counts": dxf.entity_counts,
        "entity_total": len(dxf.entities),
        "entities_with_straight_geometry": len(dxf.matching_entities()),
    }


def write_inventory(path: str, dataset: Dataset, config: MatchConfig,
                    probe_exe: Optional[str]) -> dict:
    doc = {
        "environment": environment_block(),
        "parameters": config.to_dict(),
        "probe_executable": probe_exe,
        "coordinate_domains": {
            "pdf_object": "p, q  (raw, before the object matrix; never used as "
                          "geometry)",
            "pdf_page": "u, v  (PAGE points; matching tolerance lives here)",
            "dxf_cad": "cad_x, cad_y  (DXF native units, declared by "
                       "$INSUNITS or undeclared)",
            "vp_survey": "X = Northing, Y = Easting  NOT USED; the CAD to "
                         "SURVEY mapping is not established by this harness",
            "strictly_2d": True,
        },
        "dataset": dataset.provenance(),
        "skipped_files": dataset.skipped_files,
        "pdfs": [pdf_to_dict(p) for p in dataset.pdfs],
        "dxfs": [dxf_to_dict(d) for d in dataset.dxfs],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, default=str)
    return doc


def write_matrix(path: str, dataset: Dataset, results: Sequence[CandidateEvidence],
                 config: MatchConfig, extra: Optional[dict] = None) -> dict:
    doc = {
        "environment": environment_block(),
        "parameters": config.to_dict(),
        "dataset": dataset.provenance(),
        "pairs_evaluated": len(results),
        "pairs_expected": dataset.n_pairs,
        "candidates": [e.to_dict() for e in results],
    }
    if extra:
        doc.update(extra)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, default=str)
    return doc


# ------------------------------------------------------------------- markdown

def _table(headers: Sequence[str], rows: Sequence[Sequence]) -> List[str]:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return out


def render_report(dataset: Dataset, results: Sequence[CandidateEvidence],
                  config: MatchConfig, inventory: dict,
                  units_by_dxf: Dict[str, UnitInfo],
                  cross: Optional[List[dict]] = None,
                  run_meta: Optional[dict] = None) -> str:
    """???????????? ??????? ???????????????? ????????????????? ?????."""
    env = inventory["environment"]
    lines: List[str] = []
    a = lines.append

    a("# ????? benchmark harness (????????????, ?? production)")
    a("")
    a("????? ???????? ????????? ????????? evidence. ?? ?? ???????? "
      "production verdict, confidence score ??? ?????? ???????? ???????.")
    a("")
    a("## ?????????? ???????")
    a("")
    a("- ?????? harness: `%s`" % env["harness_version"])
    a("- Python: %s (%s), %s" % (env["python_version"],
                                 env["python_implementation"], env["platform"]))
    if run_meta:
        for key in sorted(run_meta):
            a("- %s: `%s`" % (key, run_meta[key]))
    a("- dataset: `%s`" % dataset.folder)
    a("- deterministic seed: `%d`" % config.random_seed)
    a("- tolerance: **%.3f PAGE points**" % config.tolerance_points)
    a("- direction tolerance: **%.2f?** modulo 180?" %
      config.direction_tolerance_deg)
    a("- samples per segment: **%d**" % config.samples)
    a("")

    a("## ???????????? ????????????")
    a("")
    a("- PDF object: `p, q`; ???????????? ?????? ??? ?????????? object matrix.")
    a("- PDF PAGE: `u, v`; tolerance ????? ? PAGE points.")
    a("- DXF CAD: `cad_x, cad_y`; residuals ??????????? ?????? ? CAD domain.")
    a("- VP SURVEY: `X = Northing`, `Y = Easting`; **?? ????????????**.")
    a("- Strictly 2D: Z/H ?? ????????, ?? ??????????? ? ?? ???????????.")
    a("")

    a("## ?????????????? ??????")
    a("")
    a("### PDF (%d)" % len(dataset.pdfs))
    a("")
    rows = []
    for pdf in dataset.pdfs:
        rows.append([pdf.name, pdf.page_count,
                     sum(len(pg.segments) for pg in pdf.pages),
                     pdf.ocg_status, pdf.sha256])
    lines.extend(_table(["????", "???????", "PAGE LINE segments", "OCG",
                         "SHA-256"], rows))
    a("")
    for pdf in dataset.pdfs:
        a("- **%s**: OCG `%s`; %s" %
          (pdf.name, pdf.ocg_status, pdf.ocg_evidence))
    a("")
    a("### DXF (%d)" % len(dataset.dxfs))
    a("")
    rows = []
    for dxf in dataset.dxfs:
        rows.append([dxf.name, dxf.units.name,
                     "??" if dxf.units.declared else "???",
                     len(dxf.entities), len(dxf.matching_entities()),
                     len(dxf.layers), dxf.sha256])
    lines.extend(_table(["????", "$INSUNITS", "units declared", "entities",
                         "? straight geometry", "layers", "SHA-256"], rows))
    a("")

    a("## ?????? PDF ? DXF ???????")
    a("")
    a("????????? ??? **%d** ??????????; early exit ?? ??????????." % len(results))
    a("CHECK ?? ?????????? ? hypothesis generation, ?????? reflection, "
      "???????????? ??? fitting.")
    a("")
    rows = []
    for e in results:
        t = e.transform
        sel = e.selectivity
        cells = ("%d/%d" % e.cell_coverage if e.cell_coverage else "?")
        rows.append([
            "%s p%d" % (e.pdf_name, e.pdf_page_index), e.dxf_name,
            "%d/%d" % (e.control_matched, e.dxf_offered_control),
            "%d/%d" % (e.check_matched, e.dxf_offered_check), cells,
            ("%.0f%%" % (100.0 * e.top_layer_share)
             if e.top_layer_share is not None else "?"),
            (sel.random_max if sel else "?"),
            ("%.12f" % t.scale if t else "?"),
            ("%+.6f?" % t.rotation_deg if t else "?"),
            ("mirror" if t and t.eps < 0 else ("direct" if t else "?")),
        ])
    lines.extend(_table(["PDF", "DXF", "CONTROL", "CHECK", "cells",
                         "top layer", "random max", "scale", "rotation",
                         "reflection"], rows))
    a("")

    a("## ????????? ?? ?????? ??????????")
    a("")
    for e in results:
        a("### %s p%d ? %s" %
          (e.pdf_name, e.pdf_page_index, e.dxf_name))
        a("")
        if e.transform is None:
            a("- similarity hypothesis ?? ???????")
            a("")
            continue
        t = e.transform
        a("- transform PAGE?CAD: `s=%.12f`, `theta=%+.9f?`, "
          "`tx=%.6f`, `ty=%.6f`, `eps=%+d`" %
          (t.scale, t.rotation_deg, t.tx, t.ty, t.eps))
        a("- CONTROL: **%d/%d (%.2f%%)**" %
          (e.control_matched, e.dxf_offered_control,
           100.0 * e.control_fraction))
        a("- ??????????? CHECK: **%d/%d (%.2f%%)**" %
          (e.check_matched, e.dxf_offered_check, 100.0 * e.check_fraction))
        if e.cell_coverage:
            a("- spatial coverage: **%d/%d** occupied cells" % e.cell_coverage)
        a("- entity types: %d/%d; layers: %d/%d" %
          (e.entity_types_matched, e.entity_types_offered,
           e.layers_matched, e.layers_offered))
        if e.top_layer_share is not None:
            a("- top layer: `%s`, %.2f%% matched entities" %
              (e.top_layer_name, 100.0 * e.top_layer_share))
        if e.residuals_check and e.residuals_check.n_features:
            r = e.residuals_check
            a("- CHECK residuals [CAD units]: n=%d; radial RMS %.9f; "
              "median %.9f; p95 %.9f; max %.9f; bias cad_x %+.9f; "
              "bias cad_y %+.9f" %
              (r.n_features, r.radial_rms, r.radial_median, r.radial_p95,
               r.radial_max, r.cad_x_bias, r.cad_y_bias))
            unit = units_by_dxf[e.dxf_name]
            factor = unit.to_millimetres(1.0)
            if factor is not None:
                a("- CHECK residuals [mm from declared units]: radial RMS "
                  "%.6f; median %.6f; p95 %.6f; max %.6f" %
                  (r.radial_rms * factor, r.radial_median * factor,
                   r.radial_p95 * factor, r.radial_max * factor))
        if e.selectivity:
            s = e.selectivity
            a("- selectivity (spread CONTROL sample %d): baseline %d; "
              "random median %s; random max %s; shift at 5 tolerances %s; "
              "axis swap %s; opposite reflection %s; +90? %s" %
              (s.sample_size, s.baseline, s.random_median, s.random_max,
               s.localisation_reference, s.axis_swap,
               s.opposite_reflection, s.rotation_90))
        a("- descriptive research labels (?? verdict):")
        for label in e.research_labels:
            a("  - `%s`" % label)
        a("")

    a("## ?????????????")
    a("")
    a("- ??????? consensus ??? ?? ???? ?? ???????? ????????????. ??? ??????? "
      "???????????? ? spatial coverage, layer concentration, random floor ? "
      "response ?? perturbations.")
    a("- ???????????? ? ????? ????????????? layer ??? ????? ????? cells ? "
      "??????????? ??????? ??????? ??????????????? ??????????.")
    a("- ?????? CONTROL residual ?? ???????? ??????????? QA. ??????????? "
      "?????????? ????? ?????? ?????? ????????? CHECK pool.")
    a("- Harness ?? ????????? production PASS/FAIL ??????? ? ?? ???????? "
      "reference ?????????????.")
    a("")
    a("## ???????????")
    a("")
    a("- PDFium probe ???????? ???????????? ????? primitives ?? PATH; ????? "
      "truncation notices ????????? ? inventory.json.")
    a("- CUBIC_BEZIER ??????????? ? forensic counts, ?? ?? ???????????? ??? "
      "ARC/CIRCLE ? ?? ???????????? ??? straight matching segment.")
    a("- ARC/CIRCLE/ELLIPSE ? DXF INSERT ?????????????????, ?? ?? ????????? ? "
      "matching RH1.")
    a("- CAD?SURVEY mapping, CRS ? elevation ?? ???????????????.")
    a("- OCG `PRESENT` ??????? ?? ??????????? PDF tokens; ?????? ? layer ????? "
      "public PDFium API ???????? NOT VERIFIED.")
    a("")
    return "\n".join(lines)

def write_report(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
