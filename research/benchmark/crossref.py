"""Cross-reference comparison within a PDF.

Answers, by measurement only, whether more than one DXF shows a geometric
relationship with the same PDF, and whether those relationships are mutually
compatible. Nothing is merged: two DXF files are never combined, and no
preference between them is expressed.

Compatibility is reported on scale ratio, rotation difference (modulo 180,
because a drawing is undirected) and reflection flag. When these disagree, the
two candidates cannot describe the same sheet, and that fact is stated rather
than resolved.
"""

import math
from typing import Dict, List, Optional, Sequence, Tuple

from .models import CandidateEvidence, SimilarityTransform
from .similarity import angle_difference_rad


def _overlap(b1: Optional[Tuple[float, float, float, float]],
            b2: Optional[Tuple[float, float, float, float]]) -> Optional[float]:
    """Intersection area as a fraction of the smaller box area."""
    if not b1 or not b2:
        return None
    ix = max(0.0, min(b1[1], b2[1]) - max(b1[0], b2[0]))
    iy = max(0.0, min(b1[3], b2[3]) - max(b1[2], b2[2]))
    a1 = (b1[1] - b1[0]) * (b1[3] - b1[2])
    a2 = (b2[1] - b2[0]) * (b2[3] - b2[2])
    if a1 <= 0 or a2 <= 0:
        return None
    return (ix * iy) / min(a1, a2)


def build_cross_reference(results: Sequence[CandidateEvidence]
                          ) -> List[str]:
    """Markdown section comparing the DXF candidates of each PDF.

    Ordering is by measured CHECK count, which is a fact rather than a
    preference; the section deliberately states that ordering by CHECK count
    alone was measured to be misleading, so no ordering is called a winner.
    """
    by_pdf: Dict[Tuple[str, int], List[CandidateEvidence]] = {}
    for e in results:
        by_pdf.setdefault((e.pdf_name, e.pdf_page_index), []).append(e)

    lines: List[str] = []
    for key in sorted(by_pdf):
        pdf_name, page_index = key
        rows = sorted(by_pdf[key], key=lambda e: -e.check_matched)
        with_transform = [e for e in rows if e.transform is not None]
        lines.append("### %s p%d" % (pdf_name, page_index))
        lines.append("")
        lines.append("| DXF | CHECK matched | CONTROL matched | "
                     "localisation ratio | random floor ratio | "
                     "largest layer share |")
        lines.append("|---|---|---|---|---|---|")
        for e in rows:
            loc = (e.selectivity.localisation_ratio
                   if e.selectivity else None)
            floor = (e.selectivity.random_floor_ratio
                     if e.selectivity else None)
            lines.append("| %s | %d | %d | %s | %s | %s |"
                         % (e.dxf_name, e.check_matched, e.control_matched,
                            "%.2f" % loc if loc else "n/a",
                            "%.2f" % floor if floor else "n/a",
                            "%.0f%%" % (100.0 * e.top_layer_share)
                            if e.top_layer_share is not None else "n/a"))
        lines.append("")
        if not with_transform:
            lines.append("No candidate produced a transform for this page.")
            lines.append("")
            continue
        lines.append("Ordering above is by measured CHECK count. It is NOT a "
                     "recommendation: measured on real data, CHECK count "
                     "alone ranked the wrong DXF first for several pages, "
                     "because a repeated-geometry layer can produce more "
                     "coincidences than a genuine but partial correspondence.")
        lines.append("")
        if len(with_transform) >= 2:
            lines.append("Mutual compatibility of the candidates that produced "
                         "a transform:")
            lines.append("")
            for i in range(len(with_transform)):
                for j in range(i + 1, len(with_transform)):
                    a = with_transform[i]
                    b = with_transform[j]
                    ta: SimilarityTransform = a.transform
                    tb: SimilarityTransform = b.transform
                    ratio = tb.scale / ta.scale
                    dth = math.degrees(
                        angle_difference_rad(tb.rotation_rad,
                                             ta.rotation_rad))
                    ov = _overlap(a.matched_bbox_cad, b.matched_bbox_cad)
                    same_eps = (ta.eps == tb.eps)
                    lines.append(
                        "- `%s` vs `%s`: scale ratio %.6f, rotation "
                        "difference %+.4f deg, reflection flags %s, matched "
                        "area overlap %s"
                        % (a.dxf_name, b.dxf_name, ratio, dth,
                           "same" if same_eps else "different",
                           "%.1f%% of the smaller area" % (100.0 * ov)
                           if ov is not None else "not computable"))
                    lines.append("  - these two transforms describe the same "
                                 "sheet only if the scale ratio and rotation "
                                 "difference are both near zero and the "
                                 "reflection flags agree. This harness does "
                                 "not decide which is correct and does not "
                                 "combine the two DXF files.")
            lines.append("")
    return lines
