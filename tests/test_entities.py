"""Short synthetic tests for RH2.2 entities and provenance."""

import math
import pathlib
import sys
import unittest


SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from vp_geoconvert import (  # noqa: E402
    CadPoint, CoordinateDomain, CubicBezierEntity, DomainMismatchError,
    EntityGeometryError, LayerMetadata, LineEntity, PagePoint, PolylineEntity,
    Reflection, SimilarityTransform, SourceProvenance, transform_entity)


class TestProductionEntities(unittest.TestCase):

    def setUp(self):
        self.provenance = SourceProvenance(
            "drawing.pdf", "path-object-17", CoordinateDomain.PDF_PAGE,
            source_path_id="path-4", source_segment_index=2)
        self.layer = LayerMetadata(layer_name="SOURCE-LAYER",
                                   ocg_id="ocg-12", ocg_name="Road geometry")
        self.transform = SimilarityTransform(
            CoordinateDomain.PDF_PAGE, CoordinateDomain.DXF_CAD,
            2.0, math.radians(90.0), 100.0, 200.0,
            Reflection.DIRECT)

    def test_line_and_polyline_preserve_vertex_order(self):
        line = LineEntity("line-1", PagePoint(1, 2), PagePoint(3, 4),
                          self.provenance, self.layer)
        transformed_line = transform_entity(line, self.transform)
        self.assertEqual(transformed_line.start, CadPoint(96, 202))
        self.assertEqual(transformed_line.end, CadPoint(92, 206))

        polyline = PolylineEntity(
            "polyline-1",
            (PagePoint(0, 0), PagePoint(2, 0), PagePoint(2, 3)),
            self.provenance, closed=True, layer=self.layer)
        transformed_polyline = transform_entity(polyline, self.transform)
        self.assertEqual(transformed_polyline.vertices,
                         (CadPoint(100, 200), CadPoint(100, 204),
                          CadPoint(94, 204)))
        self.assertTrue(transformed_polyline.closed)

    def test_cubic_bezier_transforms_every_control_point(self):
        curve = CubicBezierEntity(
            "curve-1", PagePoint(0, 0), PagePoint(1, 0),
            PagePoint(1, 2), PagePoint(3, 2), self.provenance, self.layer)
        transformed = transform_entity(curve, self.transform)
        self.assertEqual(transformed.start, CadPoint(100, 200))
        self.assertEqual(transformed.control1, CadPoint(100, 202))
        self.assertEqual(transformed.control2, CadPoint(96, 202))
        self.assertEqual(transformed.end, CadPoint(96, 206))
        self.assertIsInstance(transformed, CubicBezierEntity)

    def test_source_geometry_is_not_modified(self):
        original_vertices = (PagePoint(-1, 0), PagePoint(0, 1),
                             PagePoint(1, 0))
        original = PolylineEntity("polyline-2", original_vertices,
                                  self.provenance)
        transformed = transform_entity(original, self.transform)
        self.assertIsNot(original, transformed)
        self.assertEqual(original.vertices, original_vertices)
        self.assertEqual(original.domain, CoordinateDomain.PDF_PAGE)
        self.assertEqual(transformed.domain, CoordinateDomain.DXF_CAD)

    def test_provenance_and_layer_metadata_are_retained(self):
        original = LineEntity("line-2", PagePoint(0, 0), PagePoint(1, 1),
                              self.provenance, self.layer)
        transformed = transform_entity(original, self.transform)
        self.assertIs(transformed.provenance, original.provenance)
        self.assertIs(transformed.layer, original.layer)
        self.assertEqual(transformed.provenance.source_path_id, "path-4")
        self.assertEqual(transformed.provenance.source_segment_index, 2)
        self.assertEqual(transformed.provenance.source_domain,
                         CoordinateDomain.PDF_PAGE)
        with self.assertRaises(ValueError):
            SourceProvenance("drawing.pdf", "path-object-17",
                             CoordinateDomain.PDF_PAGE,
                             source_segment_index=2)

    def test_domain_mismatch_and_mixed_geometry_are_rejected(self):
        cad_line = LineEntity("cad-line", CadPoint(0, 0), CadPoint(1, 1),
                              SourceProvenance(
                                  "reference.dxf", "line-9",
                                  CoordinateDomain.DXF_CAD))
        with self.assertRaises(DomainMismatchError):
            transform_entity(cad_line, self.transform)
        with self.assertRaises(EntityGeometryError):
            LineEntity("mixed", PagePoint(0, 0), CadPoint(1, 1),
                       self.provenance)


if __name__ == "__main__":
    unittest.main(verbosity=2)
