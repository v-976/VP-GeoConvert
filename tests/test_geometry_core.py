"""Short synthetic tests for the RH2.1 production geometry foundation."""

import math
import pathlib
import sys
import unittest


SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vp_geoconvert import (  # noqa: E402
    CadPoint, Close, CloseSegment, CoordinateDomain, Correspondence,
    CubicBezier, CubicBezierSegment, DegenerateControlError,
    DomainMismatchError, InsufficientControlError, InvalidTransformError, Line,
    LineSegment, Move, NumericalStabilityError, ObjectPoint, ObservationRole,
    PagePoint, PdfPath, Reflection, SimilarityTransform, SurveyPoint,
    UnsupportedRouteError, evaluate_check, evaluate_control, fit_similarity)


def assert_point_close(case, actual, expected, places=10):
    case.assertIs(type(actual), type(expected))
    for got, want in zip(actual.__dict__.values(), expected.__dict__.values()):
        case.assertAlmostEqual(got, want, places=places)


class TestSimilarity(unittest.TestCase):

    def known_transform(self, reflection=Reflection.DIRECT):
        return SimilarityTransform(
            CoordinateDomain.PDF_PAGE, CoordinateDomain.DXF_CAD,
            2.75, math.radians(23.0), 1200.0, -450.0, reflection)

    def controls_from(self, transform, points):
        return [Correspondence(
            "C%d" % index, point, transform.forward(point),
            ObservationRole.CONTROL)
            for index, point in enumerate(points)]

    def test_known_similarity_from_two_control_points(self):
        expected = self.known_transform()
        observations = self.controls_from(
            expected, [PagePoint(-3.0, 4.0), PagePoint(8.0, 1.5)])
        fitted = fit_similarity(observations)
        self.assertAlmostEqual(fitted.scale, expected.scale, places=12)
        self.assertAlmostEqual(fitted.rotation_rad, expected.rotation_rad,
                               places=12)
        self.assertAlmostEqual(fitted.target_offset_first,
                               expected.target_offset_first, places=12)
        self.assertAlmostEqual(fitted.target_offset_second,
                               expected.target_offset_second, places=12)

    def test_least_squares_uses_all_control(self):
        expected = self.known_transform()
        points = [PagePoint(-4, -3), PagePoint(2, 7), PagePoint(9, -1),
                  PagePoint(5, 11), PagePoint(-8, 6)]
        noise = [(0.03, -0.02), (-0.02, 0.01), (0.01, 0.02),
                 (-0.01, -0.01), (0.0, 0.0)]
        observations = []
        for index, (point, delta) in enumerate(zip(points, noise)):
            target = expected.forward(point)
            observations.append(Correspondence(
                "C%d" % index,
                point,
                CadPoint(target.cad_x + delta[0], target.cad_y + delta[1]),
                ObservationRole.CONTROL))
        fitted = fit_similarity(observations)
        self.assertAlmostEqual(fitted.scale, expected.scale, delta=0.002)
        self.assertAlmostEqual(fitted.rotation_rad, expected.rotation_rad,
                               delta=0.001)
        report = evaluate_control(fitted, observations)
        self.assertEqual(len(report.residuals), 5)
        self.assertLess(report.radial_rms, 0.04)

    def test_check_never_changes_fit(self):
        expected = self.known_transform()
        controls = self.controls_from(
            expected, [PagePoint(0, 0), PagePoint(10, 0), PagePoint(0, 10)])
        check = Correspondence(
            "K0", PagePoint(3, 4), CadPoint(9999, -9999),
            ObservationRole.CHECK)
        without_check = fit_similarity(controls)
        with_check = fit_similarity(controls + [check])
        self.assertEqual(without_check, with_check)
        report = evaluate_check(with_check, controls + [check])
        self.assertEqual(len(report.residuals), 1)
        self.assertGreater(report.radial_rms, 1000.0)

    def test_forward_inverse_and_domain_checks(self):
        transform = self.known_transform()
        source = PagePoint(17.25, -9.5)
        assert_point_close(self, transform.inverse(transform.forward(source)),
                           source)
        with self.assertRaises(DomainMismatchError):
            transform.forward(CadPoint(1, 2))
        with self.assertRaises(DomainMismatchError):
            transform.inverse(PagePoint(1, 2))

        cad_to_survey = SimilarityTransform(
            CoordinateDomain.DXF_CAD, CoordinateDomain.SURVEY,
            1.0, 0.0, 6000000.0, 25000000.0)
        survey = cad_to_survey.forward(CadPoint(10, 20))
        self.assertEqual(survey, SurveyPoint(6000010, 25000020))

        page_to_survey = SimilarityTransform(
            CoordinateDomain.PDF_PAGE, CoordinateDomain.SURVEY,
            0.5, math.radians(-10), 7000000, 30000000)
        survey_from_page = page_to_survey.forward(PagePoint(12, -8))
        assert_point_close(
            self, page_to_survey.inverse(survey_from_page), PagePoint(12, -8),
            places=8)
        with self.assertRaises(UnsupportedRouteError):
            SimilarityTransform(CoordinateDomain.PDF_OBJECT,
                                CoordinateDomain.DXF_CAD,
                                1.0, 0.0, 0.0, 0.0)

    def test_reflection_and_degenerate_cases(self):
        expected = self.known_transform(Reflection.REFLECTED)
        observations = self.controls_from(
            expected, [PagePoint(1, 2), PagePoint(8, -1), PagePoint(-3, 6)])
        fitted = fit_similarity(observations, Reflection.REFLECTED)
        self.assertTrue(fitted.is_reflected)
        assert_point_close(self, fitted.forward(PagePoint(4, 5)),
                           expected.forward(PagePoint(4, 5)))

        with self.assertRaises(InsufficientControlError):
            fit_similarity(observations[:1])
        coincident = [
            Correspondence("A", PagePoint(1, 1), CadPoint(2, 2),
                           ObservationRole.CONTROL),
            Correspondence("B", PagePoint(1, 1), CadPoint(3, 3),
                           ObservationRole.CONTROL),
        ]
        with self.assertRaises(DegenerateControlError):
            fit_similarity(coincident)
        collapsed = [
            Correspondence("A", PagePoint(0, 0), CadPoint(5, 5),
                           ObservationRole.CONTROL),
            Correspondence("B", PagePoint(1, 0), CadPoint(5, 5),
                           ObservationRole.CONTROL),
        ]
        with self.assertRaises(DegenerateControlError):
            fit_similarity(collapsed)
        with self.assertRaises(InvalidTransformError):
            SimilarityTransform(CoordinateDomain.PDF_PAGE,
                                CoordinateDomain.DXF_CAD,
                                0.0, 0.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            PagePoint(float("nan"), 0)
        with self.assertRaises(NumericalStabilityError):
            SimilarityTransform(CoordinateDomain.PDF_PAGE,
                                CoordinateDomain.DXF_CAD,
                                1e-320, 0.0, 0.0, 0.0)


class TestGeometryCommands(unittest.TestCase):

    def test_path_preserves_commands_and_segments(self):
        commands = (
            Move(ObjectPoint(0, 0)),
            Line(ObjectPoint(2, 0)),
            CubicBezier(ObjectPoint(3, 0), ObjectPoint(3, 2),
                        ObjectPoint(2, 2)),
            Line(ObjectPoint(0, 2)),
            Close(),
        )
        path = PdfPath(commands)
        self.assertEqual(path.commands, commands)
        self.assertEqual(path.domain, CoordinateDomain.PDF_OBJECT)
        segments = path.segments()
        self.assertEqual([segment.command_index for segment in segments],
                         [1, 2, 3, 4])
        self.assertIsInstance(segments[0], LineSegment)
        self.assertIsInstance(segments[1], CubicBezierSegment)
        self.assertEqual(segments[1].start, ObjectPoint(2, 0))
        self.assertEqual(segments[2].start, ObjectPoint(2, 2))
        self.assertIsInstance(segments[3], CloseSegment)
        self.assertEqual(segments[3].end, ObjectPoint(0, 0))

        with self.assertRaises(ValueError):
            PdfPath((Move(PagePoint(0, 0)), Line(ObjectPoint(1, 1))))


if __name__ == "__main__":
    unittest.main(verbosity=2)
