"""Full PDF x DXF candidate matrix.

Every pair is evaluated independently. The matrix never stops after finding a
good candidate, and it never assumes a one-to-one correspondence: one DXF may
match several PDFs, one PDF may partially overlap several DXFs, and a PDF may
have no usable reference at all.

Ordering of operations, which is the part that matters for independence:

  1. Discover PDFs and DXFs.
  2. Run the PDFium probe and the structural scan (forensics).
  3. Parse DXF geometry.
  4. For each DXF, split entities into CONTROL and CHECK ONCE. This happens
     before any hypothesis exists.
  5. For each (page, DXF) pair, run the blind search with CONTROL segments
     only, for both reflection hypotheses.
  6. Refit using CONTROL entities only.
  7. Freeze the transform. Only now is CHECK touched, exactly once, to measure.
  8. Measure selectivity on a spread CONTROL subsample.
  9. Emit descriptive research labels. No verdict, no confidence score.

CHECK never participates in step 5, 6, or in choosing between the reflection
hypotheses.
"""

import math
import os
import time
from typing import Dict, List, Optional, Sequence, Tuple

from .dxf_extract import load_dxf
from .geometry import SegmentIndex, consensus_count
from .matching import MatchConfig, search_similarity
from .models import (CandidateEvidence, CandidatePair, Dataset, DxfDocument,
                     GeometryEntity, PdfDocument, PdfPage, SimilarityTransform,
                     UnitInfo)
from .selectivity import (measure_selectivity, summarise_for_research)
from .similarity import segment_length
from .split import (cell_coverage, layer_stats, split_control_check,
                    spread_sample, type_stats, unmatched_cell_report)
from .validation import pool_residuals, threshold_pass_rates


class BenchmarkStopped(Exception):
    """Operator requested a stop at a checkpoint-safe pair boundary."""


def discover(folder: str) -> Tuple[List[str], List[str], Dict[str, str]]:
    """List PDFs and DXFs in a dataset folder. No filename interpretation.

    File names are treated as opaque identifiers only. Nothing downstream uses
    them as evidence of correspondence.
    """
    pdfs: List[str] = []
    dxfs: List[str] = []
    skipped: Dict[str, str] = {}
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        if not os.path.isfile(full):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext == ".pdf":
            pdfs.append(full)
        elif ext == ".dxf":
            dxfs.append(full)
        elif ext in (".dwg", ".txt", ".md", ".json", ".out", ".log", ".py"):
            skipped[name] = "not a PDF or DXF input"
        else:
            skipped[name] = "unrecognised extension"
    return pdfs, dxfs, skipped


def _entity_segments(entities: Sequence[GeometryEntity]) -> List:
    out = []
    for e in entities:
        out.extend(e.segments)
    return out


def _bbox_of(entities: Sequence[GeometryEntity]) -> Optional[Tuple]:
    xs: List[float] = []
    ys: List[float] = []
    for e in entities:
        bb = e.bbox()
        if bb:
            xs.extend((bb[0], bb[1]))
            ys.extend((bb[2], bb[3]))
    if not xs:
        return None
    return (min(xs), max(xs), min(ys), max(ys))


def evaluate_pair(pair: CandidatePair, page: PdfPage, dxf: DxfDocument,
                  config: MatchConfig, control: Sequence[GeometryEntity],
                  check: Sequence[GeometryEntity],
                  selectivity_sample: int = 250,
                  random_transform_count: int = 40) -> CandidateEvidence:
    """Evaluate one pdf-page x dxf pair. CHECK is read exactly once, here."""
    index = SegmentIndex(page.segments, config.cell_size)
    control_segments = _entity_segments(control)

    def consensus(entities: Sequence[GeometryEntity],
                  t: SimilarityTransform) -> int:
        return consensus_count(entities, t, index, config.tolerance_points,
                               config.samples, config.direction_tolerance_deg)

    # ---- step 5: blind search, CONTROL only, both reflections ------------
    by_eps: Dict[int, Dict] = {}
    for eps in (1, -1):
        hypotheses = search_similarity(control_segments, page.segments, eps,
                                       config, index=index,
                                       verification_entities=control)
        by_eps[eps] = {"hypotheses": hypotheses}

    scored = []
    for eps in (1, -1):
        for h in by_eps[eps]["hypotheses"]:
            scored.append((h.search_consensus, eps, h))
    scored.sort(key=lambda t: -t[0])

    evidence = CandidateEvidence(
        pdf_name=pair.pdf_name, dxf_name=pair.dxf_name,
        pdf_page_index=pair.pdf_page_index,
        dx_matched_entities=len(page.segments),
        dxf_offered_control=len(control), dxf_offered_check=len(check),
        control_matched=0, check_matched=0, control_fraction=0.0,
        check_fraction=0.0, transform=None, transform_source="none",
        reflection_hypothesis_tested=True, axis_swap_hypothesis_tested=True)

    if not scored:
        evidence.notes.append(
            "the blind search produced no similarity consistent with any "
            "length/direction compatible segment pair on either reflection "
            "hypothesis")
        evidence.research_labels = summarise_for_research(evidence)
        return evidence

    # ---- step 6: freeze the CONTROL-selected cluster representative -------
    # Do not manufacture midpoint correspondences by applying the inverse of
    # the transform being "refitted": fitting those pairs returns the same
    # transform by construction and is circular. A future refit needs explicit
    # PAGE/CAD correspondences. Until then, report the selected cluster mean.
    _score, eps, hypothesis = scored[0]
    transform = hypothesis.transform
    control_matched = consensus(control, transform)

    evidence.transform = transform
    evidence.transform_source = (
        "blind search over data-derived scale range and overlapping rotation "
        "scan, exact 2-point fits clustered in (log scale, rotation, "
        "translation); cluster representative selected from CONTROL only; "
        "no circular midpoint refit")
    evidence.control_matched = control_matched
    evidence.control_fraction = (control_matched / float(len(control))
                                 if control else 0.0)

    # Reflection hypothesis comparison, still CONTROL-only.
    direct = max([s for s, e, _h in scored if e == 1], default=0)
    reflected = max([s for s, e, _h in scored if e == -1], default=0)
    evidence.eps_direct_consensus = direct
    evidence.eps_reflection_consensus = reflected
    if reflected > direct:
        evidence.notes.append(
            "the mirrored hypothesis scored %d against %d for the direct "
            "hypothesis on CONTROL segments; a mirrored drawing would be "
            "reported as such, never normalised away"
            % (reflected, direct))

    # ---- step 7: CHECK, read once ---------------------------------------
    evidence.check_matched = consensus(check, transform)
    evidence.check_fraction = (evidence.check_matched / float(len(check))
                               if check else 0.0)

    # ---- coverage, diversity, concentration ------------------------------
    all_entities = list(control) + list(check)
    matched_ids = set()
    matched_entities: List[GeometryEntity] = []
    for e in all_entities:
        from .geometry import matched_entity_ids
        if matched_entity_ids(e, transform, index, config.tolerance_points,
                              config.samples,
                              config.direction_tolerance_deg):
            matched_ids.add(e.entity_id)
            matched_entities.append(e)

    evidence.cell_coverage = cell_coverage(all_entities, matched_entities)
    table, types_matched, types_offered = type_stats(all_entities, matched_ids)
    evidence.match_rate_by_type = table
    evidence.entity_types_matched = types_matched
    evidence.entity_types_offered = types_offered
    layer_table, top_name, top_share = layer_stats(all_entities, matched_ids)
    evidence.layers_matched = sum(1 for m, t in layer_table.values() if m > 0)
    evidence.layers_offered = len(layer_table)
    evidence.top_layer_name = top_name
    evidence.top_layer_share = top_share
    evidence.matched_bbox_cad = _bbox_of(matched_entities)
    evidence.unmatched_cells = unmatched_cell_report(all_entities, matched_ids)

    # ---- residuals, CAD domain ------------------------------------------
    units = dxf.units
    evidence.residuals_control = pool_residuals(control, transform, index,
                                                config, units)
    evidence.residuals_check = pool_residuals(check, transform, index, config,
                                              units)

    # ---- selectivity -----------------------------------------------------
    evidence.selectivity = measure_selectivity(
        transform, control, index, config,
        sample_size=selectivity_sample,
        random_count=random_transform_count)

    evidence.research_labels = summarise_for_research(evidence)
    if matched_entities:
        from .geometry import matched_entity_ids  # noqa: F401
    if _bbox_of(matched_entities) and control_matched and not matched_entities:
        evidence.notes.append("control matched entities but the bbox is empty")
    return evidence


def run_matrix(dataset: Dataset, config: MatchConfig,
               selectivity_sample: int = 250,
               random_transform_count: int = 40,
                progress=None,
                existing: Optional[Dict[str, CandidateEvidence]] = None,
                checkpoint=None, should_stop=None) -> List[CandidateEvidence]:
    """Evaluate every page x dxf combination. No early exit."""
    results: List[CandidateEvidence] = []
    existing = existing or {}
    for dxf in dataset.dxfs:
        matching = dxf.matching_entities()
        partition = split_control_check(matching, n_cells=8,
                                        seed=config.random_seed)
        control, check = partition.control, partition.check
        if progress:
            progress("dxf %s: %d entities with straight geometry -> "
                     "CONTROL %d / CHECK %d"
                     % (dxf.name, len(matching), len(control), len(check)))
        for pdf in dataset.pdfs:
            for page in pdf.pages:
                pair = CandidatePair.make(pdf.name, page.page_index, dxf.name)
                if pair.pair_id in existing:
                    ev = existing[pair.pair_id]
                    results.append(ev)
                    if progress:
                        progress("  %-46s | %-40s resumed from checkpoint"
                                 % (pdf.name, dxf.name))
                    continue
                if should_stop and should_stop():
                    raise BenchmarkStopped(
                        "operator stop requested before %s" % pair.pair_id)
                t0 = time.time()
                if progress:
                    progress("START %s" % pair.pair_id)
                ev = evaluate_pair(pair, page, dxf, config, control, check,
                                   selectivity_sample, random_transform_count)
                ev.notes.append("evaluation wall time %.1f s" % (time.time() - t0))
                results.append(ev)
                if checkpoint:
                    checkpoint(pair.pair_id, ev)
                if should_stop and should_stop():
                    raise BenchmarkStopped(
                        "operator stop requested after checkpointing %s"
                        % pair.pair_id)
                if progress:
                    progress("  %-46s | %-40s control=%d check=%d labels=%s"
                             % (pdf.name, dxf.name, ev.control_matched,
                                ev.check_matched,
                                ";".join(l.split(":")[0]
                                         for l in ev.research_labels) or "-"))
    return results
