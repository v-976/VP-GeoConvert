"""Selectivity and false-match defence.

Everything here attempts to BREAK a proposed correspondence rather than
confirm it. A transform that is not sharply peaked, or whose consensus is
matched by a transform built from random numbers, or whose matches collapse
onto a single repetitive layer, is not evidence of a correspondence.

The spatial sample used for these probes is a SPREAD sample over the dataset.
An earlier implementation took an ordered prefix of a cell-ordered control
list, which measured the top quarter of the sheet instead of the sheet; that
error produced wrong conclusions for four candidate pairs and is explicitly
avoided here.

All output is measurement. The module states what happened; it does not decide
whether a candidate is acceptable.
"""

import math
from typing import Dict, List, Optional, Sequence

from .geometry import SegmentIndex, consensus_count
from .matching import MatchConfig, random_transforms
from .models import GeometryEntity, SelectivityStats, SimilarityTransform
from .split import spread_sample

# Perturbation amounts are expressed in MULTIPLES OF THE MATCH TOLERANCE, not
# as a fraction of the drawing extent. A fraction of the extent is a different
# number on every dataset and on every page, which made the localisation ratio
# incomparable across runs. A multiple of the tolerance is directly meaningful:
# a transform wrong by 5 tolerances is wrong, on any dataset.
TOLERANCE_MULTIPLES = (0.5, 1.0, 2.0, 5.0, 20.0)
LOCALISATION_REFERENCE_MULTIPLE = 5.0


def _perturbations(transform: SimilarityTransform,
                   config) -> Dict[str, SimilarityTransform]:
    """Standard perturbation family, scaled to the matching tolerance.

    config supplies the tolerance, so the probe is dataset independent and
    its meaning is explicit: every entry is a transform that is wrong by a
    stated number of match tolerances.
    """
    tol_cad = config.tolerance_points * transform.scale
    out: Dict[str, SimilarityTransform] = {}
    for k in TOLERANCE_MULTIPLES:
        amount = k * tol_cad
        out["shift_cad_x+%.1ftol" % k] = transform.perturbed(shift_x=amount)
        out["shift_cad_y+%.1ftol" % k] = transform.perturbed(shift_y=amount)
        out["shift_both-%.1ftol" % k] = transform.perturbed(shift_x=-amount,
                                                             shift_y=-amount)
    for k in TOLERANCE_MULTIPLES:
        angle = k * config.direction_tolerance_deg
        out["rot+%.1ftol" % k] = transform.perturbed(rotation_deg=angle)
        out["rot-%.1ftol" % k] = transform.perturbed(rotation_deg=-angle)
    for f in (0.99, 1.01):
        out["scale_x%g" % f] = transform.perturbed(scale_factor=f)
    return out


def measure_selectivity(transform: SimilarityTransform,
                        control_entities: Sequence[GeometryEntity],
                        index: SegmentIndex,
                        config: MatchConfig,
                        sample_size: int = 250,
                        random_count: int = 40,
                        random_seed: Optional[int] = None) -> SelectivityStats:
    """Attempt to destroy a transform and record how the consensus responds."""
    seed = config.random_seed if random_seed is None else random_seed
    sample = spread_sample(list(control_entities), sample_size)

    def count(t: SimilarityTransform) -> int:
        return consensus_count(sample, t, index, config.tolerance_points,
                               config.samples, config.direction_tolerance_deg)

    baseline = count(transform)
    stats = SelectivityStats(baseline=baseline, sample_size=len(sample),
                             seed=seed, random_seed=seed, random_n=random_count)

    for label, t in _perturbations(transform, config).items():
        value = count(t)
        if label.startswith("shift"):
            stats.translations[label] = value
        elif label.startswith("rot"):
            stats.rotations_deg[label] = value
        else:
            stats.scales[label] = value

    stats.axis_swap = count(transform.axis_swapped())
    stats.opposite_reflection = count(transform.perturbed(eps=-transform.eps))
    stats.rotation_90 = count(transform.perturbed(rotation_deg=90.0))

    randoms = sorted(count(t) for t in random_transforms(
        seed, random_count, transform))
    if randoms:
        stats.random_median = randoms[len(randoms) // 2]
        stats.random_max = randoms[-1]
    return stats


def layer_concentration_warnings(per_layer: Dict[str, List[int]],
                                 top_layer_share: Optional[float],
                                 matched_total: int) -> List[str]:
    """Descriptive warnings about concentration. No policy, no thresholds.

    Each message names the measured quantity and its value so a reader can
    disagree with the description rather than with an opaque flag.
    """
    notes: List[str] = []
    if matched_total <= 0:
        return notes
    if top_layer_share is not None and top_layer_share >= 0.5:
        name = None
        best = (-1.0, None)
        for layer, (m, _t) in per_layer.items():
            if m > 0 and m / float(matched_total) > best[0]:
                best = (m / float(matched_total), layer)
        name = best[1]
        notes.append("concentration: %.0f%% of matched entities fall in one "
                     "layer (%r); repeated geometry in a single layer can "
                     "produce coincidences that survive a transform test"
                     % (100.0 * top_layer_share, name))
    return notes


def localisation_note(selectivity: Optional[SelectivityStats]) -> Optional[str]:
    """Describe whether the consensus depends on the transform at all."""
    if selectivity is None or selectivity.baseline <= 0:
        return None
    ref = selectivity.localisation_reference
    ratio = selectivity.localisation_ratio
    if ref is not None and ref == 0:
        return ("localisation: the transform wrong by 5 tolerances matched 0 "
                "of %d, so the consensus depends entirely on the transform"
                % selectivity.baseline)
    if ratio is None:
        return None
    if ratio < 1.5:
        return ("localisation: baseline %d is only %.2fx the consensus at 5 "
                "tolerances of transform error, so the consensus is largely "
                "transform-independent and is not evidence of a "
                "correspondence" % (selectivity.baseline, ratio))
    return ("localisation: baseline %d is %.2fx the consensus at 5 tolerances "
            "of transform error" % (selectivity.baseline, ratio))


def random_floor_note(selectivity: Optional[SelectivityStats]) -> Optional[str]:
    if selectivity is None or selectivity.random_max is None:
        return None
    if selectivity.random_max == 0:
        return ("random floor: %d random wrong transforms all scored 0"
                % selectivity.random_n)
    return ("random floor: %d of %d random wrong transforms scored >= 10 "
            "(max %d) against a baseline of %d"
            % (selectivity.random_n, selectivity.random_n,
               selectivity.random_max, selectivity.baseline))


def summarise_for_research(evidence) -> List[str]:
    """Descriptive research labels for one candidate.

    Deliberately descriptive, not a verdict. The strongest observed signal is
    named first, and every label carries the number behind it. There is no
    PASS/FAIL, no confidence score, and no acceptance threshold: a reader may
    reasonably weigh these measurements differently.
    """
    labels: List[str] = []
    if evidence.transform is None:
        labels.append("no-hypothesis: the blind search produced no transform")
        return labels
    if evidence.check_matched == 0:
        labels.append("no-independent-evidence: CHECK matched 0 entities, so "
                      "the CONTROL result is unconfirmed")
        return labels

    ratio = (evidence.check_matched / float(evidence.control_matched)
             if evidence.control_matched else 0.0)
    labels.append("check-retention: CHECK matched %d of %d CONTROL entities "
                  "(%.0f%%)" % (evidence.check_matched,
                                evidence.control_matched, 100.0 * ratio))

    sel = evidence.selectivity
    if sel is not None:
        if sel.random_max == 0:
            labels.append("isolated-from-random-floor: all %d random wrong "
                          "transforms scored 0" % sel.random_n)
        else:
            labels.append("random-floor: strongest random wrong transform "
                          "scored %d against baseline %d (%.1fx)"
                          % (sel.random_max, sel.baseline, sel.baseline
                             / float(sel.random_max)))
        loc = sel.localisation_ratio
        ref = sel.localisation_reference
        if ref == 0:
            labels.append("transform-localised: a transform wrong by 5 "
                          "tolerances matched 0 of %d" % sel.baseline)
        elif loc is not None:
            labels.append("localisation: %.1fx suppression at 5 tolerances of "
                          "transform error" % loc)

    if evidence.top_layer_share is not None and evidence.top_layer_share >= 0.5:
        labels.append("layer-concentration: %.0f%% of matches in one layer "
                      "(%r)" % (100.0 * evidence.top_layer_share,
                                evidence.top_layer_name))
    if evidence.cell_coverage and evidence.cell_coverage[1]:
        filled, total = evidence.cell_coverage
        labels.append("spatial-coverage: %d of %d occupied cells contain a "
                      "match (%.0f%%)" % (filled, total, 100.0 * filled
                                           / total))
    if evidence.entity_types_offered:
        labels.append("entity-type-coverage: %d of %d types contribute a "
                      "match" % (evidence.entity_types_matched,
                                 evidence.entity_types_offered))
    if evidence.residuals_check is not None:
        r = evidence.residuals_check
        labels.append("check-residuals: %d features, radial median %.6f CAD "
                      "units" % (r.n_features, r.radial_median))
    return labels
