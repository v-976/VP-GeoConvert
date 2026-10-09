"""Self-tests for the canonical research benchmark harness.

Standard library unittest only. No third-party test framework.

Every test here exists because something in the accumulated research code was
either wrong or unverified. The defect is named so a future reader knows which
past failure the test is holding closed.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark import geometry, matching, similarity, split, units, validation
from benchmark.models import (Domain, GeometryEntity, SimilarityTransform,
                              UnitInfo)


# ------------------------------------------------------------------ helpers

def scene(seed: int, count: int = 40, spread: bool = True):
    """A synthetic PAGE scene plus its CAD image under a known transform."""
    import math
    import random
    rng = random.Random(seed)
    page = []
    for j in range(count):
        y0 = j * 60.0
        length = 120.0 + rng.uniform(0, 700)
        x0 = rng.uniform(0, 300)
        page.append(((x0, y0), (x0 + length, y0)))
        if spread and j % 3 == 0:
            page.append(((x0 + length / 2.0, y0),
                         (x0 + length / 2.0, y0 + 60.0)))
    return page


def entities_from_segments(segments, layer="L", etype="LINE"):
    return [GeometryEntity(entity_id="%d:%s" % (i, etype), entity_type=etype,
                           layer=layer, segments=[s], domain=Domain.CAD)
            for i, s in enumerate(segments)]


def to_cad(segments, transform: SimilarityTransform):
    return [(transform.apply_point(a), transform.apply_point(b))
            for a, b in segments]


TRUE_T = SimilarityTransform(0.176403540756,
                             __import__("math").radians(-169.749115222),
                             25485383.8694, 6677815.4683, 1)


# ------------------------------------------------------- similarity recovery

class TestSimilarity(unittest.TestCase):

    def test_exact_similarity_recovery(self):
        """A similarity must be recovered exactly from clean correspondences."""
        pairs = [(u, v, TRUE_T.apply(u, v)[0], TRUE_T.apply(u, v)[1])
                 for u, v in ((10.0, 20.0), (300.0, 40.0), (700.0, 90.0),
                               (120.0, 640.0))]
        fit = similarity.fit_similarity(pairs, 1)
        self.assertIsNotNone(fit)
        self.assertAlmostEqual(fit.scale, TRUE_T.scale, places=10)
        self.assertAlmostEqual(fit.rotation_rad, TRUE_T.rotation_rad,
                               places=10)
        self.assertAlmostEqual(fit.tx, TRUE_T.tx, places=4)
        self.assertAlmostEqual(fit.ty, TRUE_T.ty, places=4)

    def test_apply_invert_roundtrip(self):
        """PAGE -> CAD -> PAGE must be the identity, in both reflections."""
        for eps in (1, -1):
            t = SimilarityTransform(0.37, 0.9, 1000.0, -2000.0, eps)
            for p in ((0.0, 0.0), (123.4, -56.7), (3000.0, 842.0)):
                x, y = t.apply_point(p)
                u, v = t.inverse_apply(x, y)
                self.assertAlmostEqual(u, p[0], places=8)
                self.assertAlmostEqual(v, p[1], places=8)

    def test_reflection_recovered_only_with_matching_eps(self):
        """A mirrored drawing must be readable, and only as eps = -1."""
        mirrored = SimilarityTransform(0.2, 1.1, 500.0, -900.0, -1)
        pairs = [(u, v, *mirrored.apply(u, v))
                 for u, v in ((1.0, 2.0), (55.0, 66.0), (900.0, 12.0))]
        correct = similarity.fit_similarity(pairs, -1)
        self.assertIsNotNone(correct)
        self.assertAlmostEqual(correct.scale, mirrored.scale, places=10)
        self.assertAlmostEqual(correct.eps, -1)
        wrong = similarity.fit_similarity(pairs, 1)
        self.assertTrue(wrong is None
                        or abs(wrong.scale - mirrored.scale) > 1e-6)

    def test_noisy_similarity_recovery(self):
        """With bounded noise the fit must stay close and stay a similarity."""
        import random
        rng = random.Random(5)
        scale = 0.5
        rotation = 0.42
        tx, ty = 25400000.0, 6670000.0
        t = SimilarityTransform(scale, rotation, tx, ty, 1)
        pairs = []
        for _ in range(40):
            u = rng.uniform(0, 2000)
            v = rng.uniform(0, 800)
            x, y = t.apply(u, v)
            # noise is a fraction of the drawing extent, not of the coordinates
            pairs.append((u, v, x + rng.uniform(-0.5, 0.5),
                          y + rng.uniform(-0.5, 0.5)))
        fit = similarity.fit_similarity(pairs, 1)
        self.assertIsNotNone(fit)
        self.assertAlmostEqual(fit.scale, scale, delta=2e-3)
        self.assertAlmostEqual(fit.rotation_rad, rotation, delta=1e-3)

    def test_transform_rejects_invalid_eps_and_scale(self):
        with self.assertRaises(ValueError):
            SimilarityTransform(1.0, 0.0, 0.0, 0.0, 0)
        with self.assertRaises(ValueError):
            SimilarityTransform(0.0, 0.0, 0.0, 0.0, 1)

    def test_angle_difference_is_modulo_pi(self):
        import math
        self.assertAlmostEqual(
            similarity.angle_difference_rad(0.1, math.pi + 0.1), 0.0,
            places=9)
        self.assertLess(
            abs(similarity.angle_difference_rad(0.1, math.pi - 0.1)), 0.3)

    def test_axis_swap_is_explicit_hypothesis(self):
        swapped = TRUE_T.axis_swapped()
        self.assertNotEqual(swapped.eps, TRUE_T.eps)
        for page_point in ((0.0, 0.0), (123.4, 56.7), (-8.0, 900.0)):
            cad_x, cad_y = TRUE_T.apply_point(page_point)
            swapped_x, swapped_y = swapped.apply_point(page_point)
            self.assertAlmostEqual(swapped_x, cad_y, places=7)
            self.assertAlmostEqual(swapped_y, cad_x, places=7)


# ------------------------------------------------------------------ matching

class TestMatching(unittest.TestCase):

    def setUp(self):
        self.config = matching.MatchConfig(candidate_page_segments=4000,
                                           candidate_cad_segments=120,
                                           verify_clusters=120)

    def test_exact_transform_matches_all_entities(self):
        page = scene(11)
        cad = to_cad(page, TRUE_T)
        ents = entities_from_segments(cad)
        index = geometry.SegmentIndex(page, self.config.cell_size)
        self.assertEqual(
            geometry.consensus_count(ents, TRUE_T, index,
                                     self.config.tolerance_points,
                                     self.config.samples,
                                     self.config.direction_tolerance_deg),
            len(ents))

    def test_tolerance_is_enforced(self):
        """HOLDING CLOSED: a Set B1 defect where tolerance was never tested.

        A 300 PAGE-point translation error must match nothing. Before the
        tolerance was enforced the same probe matched hundreds of entities.
        """
        page = scene(11)
        cad = to_cad(page, TRUE_T)
        ents = entities_from_segments(cad)
        index = geometry.SegmentIndex(page, self.config.cell_size)
        wrong = TRUE_T.perturbed(shift_x=300.0)
        self.assertEqual(
            geometry.consensus_count(ents, wrong, index,
                                     self.config.tolerance_points,
                                     self.config.samples,
                                     self.config.direction_tolerance_deg),
            0)

    def test_proximity_alone_is_not_sufficient(self):
        """A small rotation leaves points near geometry but changes direction."""
        page = scene(11)
        cad = to_cad(page, TRUE_T)
        ents = entities_from_segments(cad)
        index = geometry.SegmentIndex(page, self.config.cell_size)
        rotated = TRUE_T.perturbed(rotation_deg=1.5)
        matched = geometry.consensus_count(ents, rotated, index,
                                           self.config.tolerance_points,
                                           self.config.samples,
                                           self.config.direction_tolerance_deg)
        self.assertLess(matched, len(ents) * 0.1)

    def test_fragmented_page_geometry_still_matches(self):
        """PDF vectorisation may split one CAD line into collinear fragments."""
        page = scene(11, count=20)
        cad = to_cad(page, TRUE_T)
        # split each segment into three collinear fragments
        fragments = []
        for (pa, pb) in page:
            for i in range(3):
                a = similarity.interpolate_point(pa, pb, i / 3.0)
                b = similarity.interpolate_point(pa, pb, (i + 1) / 3.0)
                fragments.append((a, b))
        ents = entities_from_segments(cad)
        index = geometry.SegmentIndex(fragments, self.config.cell_size)
        self.assertEqual(
            geometry.consensus_count(ents, TRUE_T, index,
                                     self.config.tolerance_points,
                                     self.config.samples,
                                     self.config.direction_tolerance_deg),
            len(ents))

    def test_repeated_parallel_geometry_defence(self):
        """HOLDING CLOSED: repeated parallel geometry must not create a match.

        Many identical parallel lines give a large proximity count, so a
        proximity-only criterion would report a strong match at a wrong
        transform. The direction-plus-tolerance test must reject them.
        """
        page = [((0.0, float(j) * 20.0), (900.0, float(j) * 20.0))
                for j in range(45)]
        index = geometry.SegmentIndex(page, self.config.cell_size)
        # identity transform here, so CAD equals PAGE numerically and the
        # probes sit 5 pt below the nearest PAGE line: 3.3 tolerances away
        identity = SimilarityTransform(1.0, 0.0, 0.0, 0.0, 1)
        probes = [((float(i) * 7.0, 5.0), (float(i) * 7.0 + 300.0, 5.0))
                  for i in range(30)]
        ents = entities_from_segments(probes)
        matched = geometry.consensus_count(ents, identity, index,
                                           self.config.tolerance_points,
                                           self.config.samples,
                                           self.config.direction_tolerance_deg)
        self.assertEqual(matched, 0)

    def test_search_recovers_known_transform(self):
        page = scene(11)
        cad = to_cad(page, TRUE_T)
        index = geometry.SegmentIndex(page, self.config.cell_size)
        found = matching.search_similarity(cad, page, 1, self.config,
                                           index=index)
        self.assertTrue(found)
        best = found[0].transform
        self.assertAlmostEqual(best.scale, TRUE_T.scale, delta=2e-3)
        self.assertLess(abs(similarity.angle_difference_rad(
            best.rotation_rad, TRUE_T.rotation_rad)), 0.02)
        self.assertAlmostEqual(best.tx, TRUE_T.tx, delta=20.0)
        self.assertAlmostEqual(best.ty, TRUE_T.ty, delta=20.0)

    def test_search_recovers_millimetre_scale(self):
        """A DXF in millimetres must be found, not only a metre-scale one."""
        small = SimilarityTransform(0.000176403540756,
                                    TRUE_T.rotation_rad, TRUE_T.tx,
                                    TRUE_T.ty, 1)
        page = scene(11)
        cad = to_cad(page, small)
        index = geometry.SegmentIndex(page, self.config.cell_size)
        found = matching.search_similarity(cad, page, 1, self.config,
                                           index=index)
        self.assertTrue(found)
        best = found[0].transform
        self.assertAlmostEqual(best.scale, small.scale, delta=small.scale
                               * 0.02)

    def test_search_partial_overlap_with_decoys(self):
        """Only part of the CAD geometry exists in the PAGE scene, plus noise."""
        import random
        page_all = scene(11)
        keep = [s for i, s in enumerate(page_all) if i % 5 < 2]
        rng = random.Random(3)
        decoys = []
        for _ in range(150):
            x0 = rng.uniform(0, 3000)
            y0 = rng.uniform(0, 800)
            length = rng.uniform(10, 600)
            angle = rng.uniform(0, 3.14159)
            decoys.append(((x0, y0), (x0 + length * __import__("math").cos(angle),
                                      y0 + length * __import__("math").sin(angle))))
        cad = to_cad(keep, TRUE_T)
        index = geometry.SegmentIndex(keep + decoys, self.config.cell_size)
        found = matching.search_similarity(cad, keep + decoys, 1, self.config,
                                           index=index)
        self.assertTrue(found)
        best = found[0].transform
        self.assertAlmostEqual(best.scale, TRUE_T.scale, delta=2e-3)

    def test_config_rejects_inconsistent_acceptance_windows(self):
        """Acceptance windows narrower than half the scan step leave gaps."""
        with self.assertRaises(ValueError):
            matching.MatchConfig(scale_step_bins=1, scale_step_log=0.08,
                                 scale_accept_log=0.01)
        with self.assertRaises(ValueError):
            matching.MatchConfig(rotation_step_deg=15.0,
                                 rotation_accept_deg=2.0)

    def test_random_transforms_are_deterministic(self):
        a = matching.random_transforms(7, 10, TRUE_T)
        b = matching.random_transforms(7, 10, TRUE_T)
        self.assertEqual([t.to_dict() for t in a], [t.to_dict() for t in b])
        c = matching.random_transforms(8, 10, TRUE_T)
        self.assertNotEqual([t.to_dict() for t in a], [t.to_dict() for t in c])


# --------------------------------------------------------------------- split

class TestSplit(unittest.TestCase):

    def _entities(self, n=200):
        ents = []
        for i in range(n):
            x = 1000.0 + (i % 20) * 37.0
            y = 2000.0 + (i // 20) * 41.0
            ents.append(GeometryEntity(
                entity_id="e%03d:LINE" % i, entity_type="LINE",
                layer="L%d" % (i % 5), segments=[((x, y), (x + 18.0, y))],
                domain=Domain.CAD))
        return ents

    def test_no_entity_leakage(self):
        """HOLDING CLOSED: vertex-level CONTROL/CHECK leakage.

        Pools must be disjoint at entity level and must together cover the
        input exactly.
        """
        ents = self._entities()
        s = split.split_control_check(ents)
        ids_c = {e.entity_id for e in s.control}
        ids_k = {e.entity_id for e in s.check}
        self.assertEqual(ids_c & ids_k, set())
        self.assertEqual(len(ids_c) + len(ids_k), len(ents))
        s.verify(ents)

    def test_split_is_deterministic(self):
        ents = self._entities()
        a = split.split_control_check(ents)
        b = split.split_control_check(ents)
        self.assertEqual([e.entity_id for e in a.control],
                         [e.entity_id for e in b.control])
        self.assertEqual([e.entity_id for e in a.check],
                         [e.entity_id for e in b.check])

    def test_split_is_balanced(self):
        s = split.split_control_check(self._entities())
        self.assertLessEqual(abs(len(s.control) - len(s.check)), 2)

    def test_split_is_spatially_distributed(self):
        """Both pools must reach the far corners, not sit in one cluster."""
        ents = self._entities()
        s = split.split_control_check(ents)
        for pool in (s.control, s.check):
            filled, total = split.cell_coverage(ents, pool)
            self.assertGreaterEqual(filled, total * 0.6,
                                    "%d of %d cells" % (filled, total))

    def test_spread_sample_is_not_an_ordered_prefix(self):
        """HOLDING CLOSED: the `control[:250]` spatial-selection error.

        spread_sample must span the input, so a cell-ordered list yields
        coverage of the whole sheet rather than its first rows.
        """
        items = list(range(1000))
        sample = split.spread_sample(items, 100)
        self.assertEqual(len(sample), 100)
        self.assertEqual(sample[0], 0)
        self.assertGreater(sample[-1], 900)
        self.assertNotEqual(sample, items[:100])

    def test_spread_sample_edge_cases(self):
        self.assertEqual(split.spread_sample([], 10), [])
        self.assertEqual(split.spread_sample([1, 2, 3], 10), [1, 2, 3])
        self.assertEqual(split.spread_sample(list(range(50)), 0), [])

    def test_cell_coverage_uses_the_full_drawing_grid(self):
        entities = [
            GeometryEntity("a", "LINE", "L",
                           [((0.0, 0.0), (1.0, 0.0))], Domain.CAD),
            GeometryEntity("b", "LINE", "L",
                           [((100.0, 100.0), (101.0, 100.0))], Domain.CAD),
        ]
        # Entity b must remain in the upper-right cell of the grid defined by
        # all entities. Re-binning the matched subset over its own degenerate
        # bounds used to move it to the lower-left cell and report 0 coverage.
        self.assertEqual(split.cell_coverage(entities, [entities[1]], 2),
                         (1, 2))

    def test_layer_concentration_detects_single_layer(self):
        ents = [GeometryEntity("a", "LINE", "HOT", [((0.0, 0.0), (1.0, 0.0))],
                               Domain.CAD),
                GeometryEntity("b", "LINE", "HOT", [((1.0, 0.0), (2.0, 0.0))],
                               Domain.CAD),
                GeometryEntity("c", "LINE", "COLD", [((2.0, 0.0), (3.0, 0.0))],
                               Domain.CAD)]
        # share is the fraction of MATCHED entities in the top layer, so with
        # both matched entities on HOT the share is 1.0, not 2/3
        _table, name, share = split.layer_stats(ents, {"a", "b"})
        self.assertEqual(name, "HOT")
        self.assertAlmostEqual(share, 1.0)

    def test_entities_without_segments_do_not_break_the_split(self):
        ents = [GeometryEntity("p1", "POINT", "L", [], Domain.CAD,
                               center=(1.0, 2.0)),
                GeometryEntity("p2", "POINT", "L", [], Domain.CAD,
                               center=(3.0, 4.0))]
        s = split.split_control_check(ents)
        s.verify(ents)
        self.assertEqual(len(s.control) + len(s.check), 2)


# --------------------------------------------------------------------- units

class TestUnits(unittest.TestCase):

    def test_metres_declaration(self):
        u = units.unit_from_insunits("6")
        self.assertTrue(u.declared)
        self.assertEqual(u.name, "metres")
        self.assertAlmostEqual(u.to_millimetres(1.0), 1000.0)
        self.assertAlmostEqual(u.from_millimetres(250.0), 0.25)

    def test_millimetres_declaration(self):
        u = units.unit_from_insunits("4")
        self.assertTrue(u.declared)
        self.assertAlmostEqual(u.to_millimetres(1.0), 1.0)
        self.assertAlmostEqual(u.to_millimetres(0.5), 0.5)

    def test_absent_declaration_yields_no_conversion(self):
        u = units.unit_from_insunits(None)
        self.assertFalse(u.declared)
        self.assertIsNone(u.to_millimetres(1.0))
        self.assertIsNone(u.from_millimetres(1.0))
        self.assertEqual(units.residual_unit_label(u), "raw_cad_units")

    def test_unparseable_declaration_yields_unknown(self):
        for bad in ("", "abc", "  "):
            u = units.unit_from_insunits(bad)
            self.assertFalse(u.declared)

    def test_roundtrip_for_every_declared_unit(self):
        for code, info in units.INSUNITS.items():
            if code == 0:
                self.assertFalse(info.declared)
                continue
            self.assertTrue(info.declared, code)
            mm = info.to_millimetres(2.0)
            self.assertIsNotNone(mm)
            self.assertAlmostEqual(info.from_millimetres(mm), 2.0, places=9)

    def test_unit_label_distinguishes_metre_from_millimetre_files(self):
        self.assertIn("declared metres",
                      units.residual_unit_label(units.unit_from_insunits("6")))
        self.assertIn("declared millimetres",
                      units.residual_unit_label(units.unit_from_insunits("4")))

    def test_unitless_is_not_silently_treated_as_metres(self):
        u = units.unit_from_insunits("0")
        self.assertEqual(u.name, "unitless")
        self.assertFalse(u.declared)
        self.assertIsNone(u.to_millimetres(1.0))


class TestPdfProbeParser(unittest.TestCase):

    def test_line_after_cubic_starts_at_cubic_endpoint(self):
        """A cubic endpoint is the current path position for the next LINE."""
        from benchmark.pdf_extract import parse_probe_output
        text = """document file version     = 17
document page count       = 1
PAGE index                 = 0
  page size (points)       = u=100 v=100
  page object count        = 1
  [obj] type = PATH
    object matrix (PAGE) = [a=2 b=0 c=0 d=2 e=10 f=20]
    segments = 4
      seg[0] MOVE u=0 v=0 close=false
      seg[1] LINE u=1 v=0 close=false
      seg[2] CUBIC_BEZIER u=2 v=1 close=false
      seg[3] LINE u=3 v=1 close=false
"""
        fd, path = tempfile.mkstemp(suffix=".probe.txt")
        os.close(fd)
        try:
            with open(path, "w", encoding="latin-1") as fh:
                fh.write(text)
            _version, pages = parse_probe_output(path)
            self.assertEqual(pages[0].segments,
                             [((10.0, 20.0), (12.0, 20.0)),
                              ((14.0, 22.0), (16.0, 22.0))])
        finally:
            os.unlink(path)


# ------------------------------------------------ coordinate-domain separation

class TestDomainSeparation(unittest.TestCase):

    def test_page_segments_must_be_page_domain(self):
        from benchmark.models import PdfPage
        with self.assertRaises(ValueError):
            PdfPage(page_index=0, width_points=1.0, height_points=1.0,
                    segment_domain=Domain.CAD)

    def test_dxf_entities_must_be_cad_domain(self):
        with self.assertRaises(ValueError):
            GeometryEntity("x", "LINE", "L", [], domain=Domain.PAGE)

    def test_residuals_must_be_cad_domain(self):
        from benchmark.models import ResidualStats
        with self.assertRaises(ValueError):
            ResidualStats(1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                          domain=Domain.PAGE)

    def test_residual_values_are_in_cad_not_page_points(self):
        """HOLDING CLOSED: the PAGE/CAD mixing defect.

        A CAD space residual must be expressed in CAD units. With a scale of
        0.1764 CAD/point, a 1.5 point tolerance is 0.2646 CAD units, so a
        residual near that value must be small in CAD terms and large in
        point terms. The function must report the CAD number.
        """
        config = matching.MatchConfig()
        page = scene(11)
        cad = to_cad(page, TRUE_T)
        ents = entities_from_segments(cad)
        index = geometry.SegmentIndex(page, config.cell_size)
        stats = validation.pool_residuals(ents, TRUE_T, index, config,
                                           units.unit_from_insunits("6"))
        self.assertEqual(stats.n_entities, len(ents))
        self.assertLess(stats.radial_max, 1e-6)
        gate = validation.tolerance_in_cad_units(TRUE_T,
                                                 config.tolerance_points)
        self.assertAlmostEqual(gate, 1.5 * TRUE_T.scale, places=12)

    def test_threshold_table_withholds_mm_when_units_undeclared(self):
        config = matching.MatchConfig()
        page = scene(11, count=10)
        cad = to_cad(page, TRUE_T)
        ents = entities_from_segments(cad)
        index = geometry.SegmentIndex(page, config.cell_size)
        stats = validation.pool_residuals(ents, TRUE_T, index, config,
                                           units.unit_from_insunits(None))
        table = validation.threshold_pass_rates(stats, TRUE_T,
                                                units.unit_from_insunits(None))
        self.assertTrue(all(passed is None for _t, passed, _n in table))


# ---------------------------------------------------------------- selectivity

class TestSelectivity(unittest.TestCase):

    def test_perturbation_localisation(self):
        """HOLDING CLOSED: the consensus must depend on the transform.

        A genuine correspondence collapses under a small perturbation. A
        spurious one does not, which is why this ratio is the discriminating
        measurement rather than the raw count.
        """
        from benchmark.selectivity import measure_selectivity
        config = matching.MatchConfig(candidate_page_segments=4000,
                                      candidate_cad_segments=60,
                                      verify_clusters=60)
        page = scene(11)
        cad = to_cad(page, TRUE_T)
        ents = entities_from_segments(cad)
        index = geometry.SegmentIndex(page, config.cell_size)
        sel = measure_selectivity(TRUE_T, ents, index, config,
                                  sample_size=100, random_count=20)
        self.assertGreater(sel.baseline, 0)
        self.assertEqual(sel.random_max, 0)
        # localisation_ratio is None when the reference perturbation (wrong by
        # 5 tolerances) matched nothing at all: suppression is then total and
        # no finite ratio is needed.
        self.assertEqual(sel.localisation_reference, 0)
        self.assertIsNone(sel.localisation_ratio)
        self.assertEqual(sel.axis_swap, 0)
        # A mirror of an axis-aligned line is still an axis-aligned line, so on
        # a purely rectilinear synthetic scene the mirrored hypothesis can
        # coincidentally match one or two features. That is precisely why the
        # harness compares counts and ratios and does not test a hypothesis by
        # demanding it be exactly zero.
        self.assertLessEqual(sel.opposite_reflection, 1)
        self.assertLess(sel.opposite_reflection, sel.baseline)
        self.assertEqual(sel.rotation_90, 0)

    def test_selectivity_is_deterministic(self):
        from benchmark.selectivity import measure_selectivity
        config = matching.MatchConfig(candidate_page_segments=4000,
                                      candidate_cad_segments=60,
                                      verify_clusters=60)
        page = scene(11)
        cad = to_cad(page, TRUE_T)
        ents = entities_from_segments(cad)
        index = geometry.SegmentIndex(page, config.cell_size)
        a = measure_selectivity(TRUE_T, ents, index, config,
                                sample_size=60, random_count=10)
        b = measure_selectivity(TRUE_T, ents, index, config,
                                sample_size=60, random_count=10)
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_axis_swap_and_reflation_are_explicit_hypotheses(self):
        from benchmark.selectivity import measure_selectivity
        config = matching.MatchConfig()
        page = scene(11, count=10)
        cad = to_cad(page, TRUE_T)
        ents = entities_from_segments(cad)
        index = geometry.SegmentIndex(page, config.cell_size)
        sel = measure_selectivity(TRUE_T, ents, index, config,
                                  sample_size=20, random_count=5)
        self.assertIsNotNone(sel.axis_swap)
        self.assertIsNotNone(sel.opposite_reflection)
        # both must be strictly worse than the selected hypothesis
        self.assertLess(sel.axis_swap, sel.baseline)
        self.assertLess(sel.opposite_reflection, sel.baseline)


# ------------------------------------------------------------------ rerun

class TestReproducibility(unittest.TestCase):

    def test_same_input_same_output(self):
        """HOLDING CLOSED: a benchmark rerun must be bit-identical."""
        config = matching.MatchConfig(candidate_page_segments=4000,
                                      candidate_cad_segments=120,
                                      verify_clusters=120)
        page = scene(11)
        cad = to_cad(page, TRUE_T)
        index = geometry.SegmentIndex(page, config.cell_size)
        first = matching.search_similarity(cad, page, 1, config, index=index)
        second = matching.search_similarity(cad, page, 1, config, index=index)
        self.assertEqual([h.transform.to_dict() for h in first],
                         [h.transform.to_dict() for h in second])
        self.assertEqual([h.search_consensus for h in first],
                         [h.search_consensus for h in second])

    def test_split_and_matrix_are_reproducible(self):
        ents = entities_from_segments(to_cad(scene(11, count=30), TRUE_T))
        a = split.split_control_check(ents)
        b = split.split_control_check(ents)
        self.assertEqual([e.entity_id for e in a.control],
                         [e.entity_id for e in b.control])

    def test_matrix_resumes_completed_pair_without_recalculation(self):
        from benchmark.matrix import run_matrix
        from benchmark.models import (CandidateEvidence, Dataset, DxfDocument,
                                      PdfDocument, PdfPage, UNKNOWN_UNITS)
        pdf = PdfDocument("p.pdf", "p.pdf", "a" * 64, 1,
                          pages=[PdfPage(0, 10.0, 10.0)])
        dxf = DxfDocument("d.dxf", "d.dxf", "b" * 64, 1,
                          units=UNKNOWN_UNITS)
        dataset = Dataset("fixture", "fixture", [pdf], [dxf])
        evidence = CandidateEvidence(
            pdf_name="p.pdf", dxf_name="d.dxf", pdf_page_index=0,
            dx_matched_entities=0, dxf_offered_control=0,
            dxf_offered_check=0, control_matched=0, check_matched=0,
            control_fraction=0.0, check_fraction=0.0, transform=None,
            transform_source="checkpoint fixture")
        key = "p.pdf|p0|d.dxf"
        checkpoint_calls = []
        got = run_matrix(dataset, matching.MatchConfig(),
                         existing={key: evidence},
                         checkpoint=lambda *_: checkpoint_calls.append(1))
        self.assertEqual(got, [evidence])
        self.assertEqual(checkpoint_calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
