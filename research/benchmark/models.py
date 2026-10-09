"""Explicit research data models.

Design rule enforced here: coordinates, units, geometry domain and source
identity are SEPARATE fields and are never merged into one another.

A geometry object always knows which domain it lives in (PAGE or CAD).
Residual computation always returns CAD-domain quantities, because that is the
only domain in which both a PDF feature and a DXF feature can be compared
without an unproven coordinate-system assumption.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

Point = Tuple[float, float]


class Domain(Enum):
    """Geometry domain of a coordinate pair.

    SURVEY is declared so that its absence is explicit rather than
    accidental. Nothing in this harness produces or consumes SURVEY points.
    """

    PAGE = "PAGE"          # u, v -- PDF page space, PDF units (points)
    CAD = "CAD"            # cad_x, cad_y -- raw DXF, DXF native units
    SURVEY = "SURVEY"      # X = Northing, Y = Easting -- NOT USED
    RAW_PDF_OBJECT = "RAW_PDF_OBJECT"   # p, q -- object space before matrix


# --------------------------------------------------------------------- units

@dataclass(frozen=True)
class UnitInfo:
    """DXF unit declaration.

    `metres_per_unit` is None when the file does not declare units. A None
    value must never be guessed: residual reporting then stays in raw CAD
    units and no mm/m labels are produced.
    """

    code: Optional[int]
    name: str                       # human readable, from the file
    metres_per_unit: Optional[float]

    @property
    def declared(self) -> bool:
        return self.metres_per_unit is not None

    def to_millimetres(self, v: float) -> Optional[float]:
        if self.metres_per_unit is None:
            return None
        return v * self.metres_per_unit * 1000.0

    def from_millimetres(self, mm: float) -> Optional[float]:
        if self.metres_per_unit is None:
            return None
        return mm / (self.metres_per_unit * 1000.0)


UNKNOWN_UNITS = UnitInfo(None, "undeclared", None)


# ------------------------------------------------------------------ geometry

@dataclass
class GeometryEntity:
    """One DXF entity with its 2D CAD geometry.

    An entity is the atomic unit of evidence. It is never split across the
    CONTROL and CHECK pools, and a match is all-or-nothing per entity.
    """

    entity_id: str
    entity_type: str
    layer: Optional[str]
    segments: List[Tuple[Point, Point]] = field(default_factory=list)
    domain: Domain = Domain.CAD
    # non-segment features kept for inventory / future matching
    center: Optional[Point] = None
    radius: Optional[float] = None
    start_angle: Optional[float] = None
    end_angle: Optional[float] = None
    closed: bool = False
    text: Optional[str] = None

    def __post_init__(self):
        if self.domain is not Domain.CAD:
            raise ValueError("DXF entities are CAD domain by definition; got "
                             "%s" % self.domain)

    @property
    def has_segments(self) -> bool:
        return bool(self.segments)

    def bbox(self) -> Optional[Tuple[float, float, float, float]]:
        if not self.segments:
            return None
        xs = [p[0] for s in self.segments for p in s]
        ys = [p[1] for s in self.segments for p in s]
        return (min(xs), max(xs), min(ys), max(ys))


# ------------------------------------------------------------------ documents

@dataclass
class PdfPage:
    page_index: int
    width_points: float
    height_points: float
    rotation_deg: int = 0
    object_count: int = 0
    path_count: int = 0
    text_count: int = 0
    image_count: int = 0
    form_count: int = 0
    move_count: int = 0
    line_count: int = 0
    cubic_count: int = 0
    close_flag_count: int = 0
    declared_segment_count: int = 0
    printed_segment_count: int = 0
    distinct_matrix_count: int = 0
    truncation_notices: int = 0
    has_transparency: Optional[bool] = None
    segments: List[Tuple[Point, Point]] = field(default_factory=list)
    segment_domain: Domain = Domain.PAGE

    def __post_init__(self):
        if self.segment_domain is not Domain.PAGE:
            raise ValueError("PDF page segments must be PAGE domain")


@dataclass
class PdfDocument:
    path: str
    name: str
    sha256: str
    size_bytes: int
    pdf_version: Optional[str] = None
    creator: Optional[str] = None
    producer: Optional[str] = None
    title: Optional[str] = None
    creation_date: Optional[str] = None
    mod_date: Optional[str] = None
    encrypted: Optional[bool] = None
    uses_xref_stream: Optional[bool] = None
    uses_object_streams: Optional[bool] = None
    media_boxes: List[Tuple[float, float, float, float]] = field(
        default_factory=list)
    crop_box_count: int = 0
    trim_box_count: int = 0
    ocg_status: str = "NOT_VERIFIED"       # PRESENT | ABSENT | NOT_VERIFIED
    ocg_evidence: str = ""
    ocg_object_count: Optional[int] = None
    pdfium_file_version: Optional[int] = None
    probe_exe: Optional[str] = None
    pages: List[PdfPage] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)


@dataclass
class DxfDocument:
    path: str
    name: str
    sha256: str
    size_bytes: int
    acad_version: Optional[str] = None
    last_saved_by: Optional[str] = None
    measurement: Optional[int] = None
    units: UnitInfo = UNKNOWN_UNITS
    sections: List[str] = field(default_factory=list)
    layers: List[str] = field(default_factory=list)
    extmin: Optional[Point] = None
    extmax: Optional[Point] = None
    entity_counts: Dict[str, int] = field(default_factory=dict)
    entities: List[GeometryEntity] = field(default_factory=list)
    cad_x_span: Optional[float] = None
    cad_y_span: Optional[float] = None

    def matching_entities(self) -> List[GeometryEntity]:
        """Entities usable as matching features: straight 2D segments only.

        CURVE_TYPES and INSERT are inventoried but not used for matching here.
        """
        return [e for e in self.entities if e.has_segments]


# ------------------------------------------------------------------ transform

@dataclass(frozen=True)
class SimilarityTransform:
    """2D similarity / Helmert, PAGE -> CAD.

        cad_x = s*( cos(th)*u - eps*sin(th)*v ) + tx
        cad_y = s*( sin(th)*u + eps*cos(th)*v ) + ty

    `eps` is an EXPLICIT reflection hypothesis (+1 direct, -1 mirrored). It is
    never folded into the rotation silently. There is no affine variant: a
    similarity is never silently replaced by a more flexible model.
    """

    scale: float
    rotation_rad: float
    tx: float
    ty: float
    eps: int = 1

    def __post_init__(self):
        if self.eps not in (1, -1):
            raise ValueError("eps must be +1 or -1, got %r" % (self.eps,))
        if self.scale <= 0:
            raise ValueError("scale must be positive, got %r" % self.scale)

    @property
    def rotation_deg(self) -> float:
        import math
        return math.degrees(self.rotation_rad)

    @property
    def determinant_sign(self) -> int:
        return self.eps

    def to_dict(self) -> dict:
        return {"scale": self.scale, "rotation_deg": self.rotation_deg,
                "rotation_rad": self.rotation_rad, "tx": self.tx,
                "ty": self.ty, "eps": self.eps,
                "reflection": self.eps < 0}

    @classmethod
    def from_dict(cls, d: dict) -> "SimilarityTransform":
        import math
        return cls(d["scale"], math.radians(d["rotation_deg"]), d["tx"],
                   d["ty"], int(d["eps"]))

    def apply(self, u: float, v: float) -> Point:
        """PAGE -> CAD."""
        import math
        c = math.cos(self.rotation_rad)
        s = math.sin(self.rotation_rad)
        e = self.eps
        return (self.scale * (c * u - e * s * v) + self.tx,
                self.scale * (s * u + e * c * v) + self.ty)

    def apply_point(self, p: Point) -> Point:
        return self.apply(p[0], p[1])

    def inverse_apply(self, cad_x: float, cad_y: float) -> Point:
        """CAD -> PAGE. Exact inverse of `apply`."""
        import math
        c = math.cos(self.rotation_rad)
        s = math.sin(self.rotation_rad)
        e = self.eps
        dx = cad_x - self.tx
        dy = cad_y - self.ty
        return ((c * dx + s * dy) / self.scale,
                (-e * s * dx + e * c * dy) / self.scale)

    def inverse_point(self, p: Point) -> Point:
        return self.inverse_apply(p[0], p[1])

    def perturbed(self, scale_factor: float = 1.0, rotation_deg: float = 0.0,
                  shift_x: float = 0.0, shift_y: float = 0.0,
                  eps: Optional[int] = None) -> "SimilarityTransform":
        import math
        return SimilarityTransform(self.scale * scale_factor,
                                   self.rotation_rad + math.radians(
                                       rotation_deg),
                                   self.tx + shift_x, self.ty + shift_y,
                                   self.eps if eps is None else eps)

    def axis_swapped(self) -> "SimilarityTransform":
        """The hypothesis 'cad_x and cad_y are swapped'.

        This is the exact target-coordinate permutation

            (cad_x', cad_y') = (cad_y, cad_x)

        expressed in the same similarity parameterisation. It is an EXPLICIT
        alternative hypothesis, never a silent normalisation.
        """
        import math
        return SimilarityTransform(self.scale,
                                   math.pi / 2.0 - self.rotation_rad,
                                   self.ty, self.tx, -self.eps)


# ------------------------------------------------------- split and statistics

@dataclass
class ControlCheckSplit:
    """Entity-level CONTROL / CHECK partition.

    Invariants guaranteed by `split.py` and checked by the test suite:
      * disjoint: no entity id appears in both pools
      * covering: control + check == the full input entity list
      * deterministic: same input, same seed -> same partition
      * spatially distributed: both pools span occupied cells of a grid
    """

    control: List[GeometryEntity]
    check: List[GeometryEntity]
    n_cells: int
    seed: int
    strategy: str = "cell-sorted-alternating"

    def verify(self, source: List[GeometryEntity]) -> None:
        c = {e.entity_id for e in self.control}
        k = {e.entity_id for e in self.check}
        if c & k:
            raise AssertionError("CONTROL/CHECK leakage: %d shared entities"
                                 % len(c & k))
        if len(c) + len(k) != len(source):
            raise AssertionError("split is not covering: %d + %d != %d"
                                 % (len(c), len(k), len(source)))
        if len({e.entity_id for e in source}) != len(source):
            raise AssertionError("source entity ids are not unique")


@dataclass
class ResidualStats:
    """Residuals, ALWAYS in CAD domain.

    `unit` names the reporting unit. It is `raw_cad_units` unless the DXF
    declared units, in which case millimetres are additionally available.
    Values are stored in CAD units; `*_mm` fields exist only when the DXF
    declared units.
    """

    n_features: int
    n_entities: int
    radial_rms: Optional[float]
    radial_median: Optional[float]
    radial_p95: Optional[float]
    radial_max: Optional[float]
    cad_x_rms: Optional[float]
    cad_y_rms: Optional[float]
    cad_x_bias: Optional[float]
    cad_y_bias: Optional[float]
    cad_x_p95: Optional[float]
    cad_y_p95: Optional[float]
    domain: Domain = Domain.CAD
    units_declared: bool = False
    residual_unit: str = "raw_cad_units"
    # Raw CAD-domain radial values, kept for threshold tables. Excluded from
    # to_dict() to keep the JSON readable and stable.
    raw_radial: Optional[List[float]] = None

    def __post_init__(self):
        if self.domain is not Domain.CAD:
            raise ValueError("residuals are CAD domain by definition")

    def to_dict(self) -> dict:
        return {"domain": self.domain.value, "n_features": self.n_features,
                "n_entities": self.n_entities,
                "radial_rms": self.radial_rms,
                "radial_median": self.radial_median,
                "radial_p95": self.radial_p95,
                "radial_max": self.radial_max,
                "cad_x_rms": self.cad_x_rms, "cad_y_rms": self.cad_y_rms,
                "cad_x_bias": self.cad_x_bias, "cad_y_bias": self.cad_y_bias,
                "cad_x_p95": self.cad_x_p95, "cad_y_p95": self.cad_y_p95,
                "residual_unit": self.residual_unit,
                "units_declared": self.units_declared,
                # Needed by a resumed run to render threshold tables exactly
                # as an uninterrupted run would. This remains CAD-domain data.
                "raw_radial": self.raw_radial}

    @classmethod
    def from_dict(cls, d: dict) -> "ResidualStats":
        return cls(
            n_features=d["n_features"], n_entities=d["n_entities"],
            radial_rms=d["radial_rms"], radial_median=d["radial_median"],
            radial_p95=d["radial_p95"], radial_max=d["radial_max"],
            cad_x_rms=d["cad_x_rms"], cad_y_rms=d["cad_y_rms"],
            cad_x_bias=d["cad_x_bias"], cad_y_bias=d["cad_y_bias"],
            cad_x_p95=d["cad_x_p95"], cad_y_p95=d["cad_y_p95"],
            domain=Domain(d.get("domain", "CAD")),
            units_declared=d.get("units_declared", False),
            residual_unit=d.get("residual_unit", "raw_cad_units"),
            raw_radial=d.get("raw_radial"))


@dataclass
class SelectivityStats:
    """Response of the consensus to deliberate transform perturbation.

    `localisation_ratio` compares the selected transform against the strongest
    small perturbation. A ratio near 1 means the consensus does NOT depend on
    the transform and is therefore not evidence of a correspondence.
    """

    baseline: int
    sample_size: int
    translations: Dict[str, int] = field(default_factory=dict)
    rotations_deg: Dict[str, int] = field(default_factory=dict)
    scales: Dict[str, int] = field(default_factory=dict)
    axis_swap: Optional[int] = None
    opposite_reflection: Optional[int] = None
    rotation_90: Optional[int] = None
    random_median: Optional[int] = None
    random_max: Optional[int] = None
    random_n: int = 0
    random_seed: Optional[int] = None
    seed: Optional[int] = None

    @property
    def random_floor_ratio(self) -> Optional[float]:
        if self.random_max in (None, 0):
            return None
        return self.baseline / float(self.random_max)

    @property
    def localisation_reference(self) -> Optional[int]:
        """Consensus at the reference perturbation, i.e. wrong by 5 tolerances.

        This is the number the localisation ratio is built on. A ratio near 1
        means the consensus is essentially transform independent and is
        therefore not evidence of a correspondence.
        """
        key = "shift_both-5.0tol"
        v = self.translations.get(key)
        if v is None:
            values = [v for v in self.translations.values()
                      if v is not None]
            return max(values) if values else None
        return v

    @property
    def localisation_ratio(self) -> Optional[float]:
        """baseline / consensus when the transform is wrong by 5 tolerances."""
        ref = self.localisation_reference
        if ref is None or ref <= 0:
            return None
        return self.baseline / float(ref)

    def to_dict(self) -> dict:
        return {"baseline": self.baseline, "sample_size": self.sample_size,
                "translations": self.translations,
                "rotations_deg": self.rotations_deg,
                "scales": self.scales,
                "axis_swap": self.axis_swap,
                "opposite_reflection": self.opposite_reflection,
                "rotation_90_deg": self.rotation_90,
                "random_median": self.random_median,
                "random_max": self.random_max, "random_n": self.random_n,
                "random_seed": self.random_seed, "seed": self.seed,
                "random_floor_ratio": self.random_floor_ratio,
                "localisation_reference_5tol": self.localisation_reference,
                "localisation_ratio": self.localisation_ratio}

    @classmethod
    def from_dict(cls, d: dict) -> "SelectivityStats":
        return cls(
            baseline=d["baseline"], sample_size=d["sample_size"],
            translations=d.get("translations", {}),
            rotations_deg=d.get("rotations_deg", {}),
            scales=d.get("scales", {}), axis_swap=d.get("axis_swap"),
            opposite_reflection=d.get("opposite_reflection"),
            rotation_90=d.get("rotation_90_deg"),
            random_median=d.get("random_median"),
            random_max=d.get("random_max"), random_n=d.get("random_n", 0),
            random_seed=d.get("random_seed"), seed=d.get("seed"))


@dataclass
class CandidateEvidence:
    """All measured evidence for ONE pdf x dxf pair.

    Every field is a measurement. There is no verdict, no confidence score and
    no policy field. `research_labels` are DESCRIPTIVE strings that name which
    measured quantity stood out; each is accompanied by the number that
    produced it.
    """

    pdf_name: str
    dxf_name: str
    pdf_page_index: int
    dx_matched_entities: int
    dxf_offered_control: int
    dxf_offered_check: int
    control_matched: int
    check_matched: int
    control_fraction: float
    check_fraction: float
    transform: Optional[SimilarityTransform]
    transform_source: str                       # how it was obtained
    reflection_hypothesis_tested: bool = True
    axis_swap_hypothesis_tested: bool = True
    eps_reflection_consensus: Optional[int] = None
    eps_direct_consensus: Optional[int] = None
    cell_coverage: Optional[Tuple[int, int]] = None
    entity_types_matched: int = 0
    entity_types_offered: int = 0
    layers_matched: int = 0
    layers_offered: int = 0
    top_layer_share: Optional[float] = None
    top_layer_name: Optional[str] = None
    match_rate_by_type: Dict[str, List[int]] = field(default_factory=dict)
    residuals_control: Optional[ResidualStats] = None
    residuals_check: Optional[ResidualStats] = None
    selectivity: Optional[SelectivityStats] = None
    matched_bbox_cad: Optional[Tuple[float, float, float, float]] = None
    unmatched_cells: Optional[str] = None
    research_labels: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pdf_name": self.pdf_name, "dxf_name": self.dxf_name,
            "pdf_page_index": self.pdf_page_index,
            "dx_matched_entities": self.dx_matched_entities,
            "dxf_offered_control": self.dxf_offered_control,
            "dxf_offered_check": self.dxf_offered_check,
            "control_matched": self.control_matched,
            "check_matched": self.check_matched,
            "control_fraction": self.control_fraction,
            "check_fraction": self.check_fraction,
            "transform": self.transform.to_dict() if self.transform else None,
            "transform_source": self.transform_source,
            "reflection_hypothesis_tested": self.reflection_hypothesis_tested,
            "axis_swap_hypothesis_tested": self.axis_swap_hypothesis_tested,
            "eps_reflection_consensus": self.eps_reflection_consensus,
            "eps_direct_consensus": self.eps_direct_consensus,
            "cell_coverage": list(self.cell_coverage)
            if self.cell_coverage else None,
            "entity_types_matched": self.entity_types_matched,
            "entity_types_offered": self.entity_types_offered,
            "layers_matched": self.layers_matched,
            "layers_offered": self.layers_offered,
            "top_layer_share": self.top_layer_share,
            "top_layer_name": self.top_layer_name,
            "match_rate_by_type": self.match_rate_by_type,
            "residuals_control": self.residuals_control.to_dict()
            if self.residuals_control else None,
            "residuals_check": self.residuals_check.to_dict()
            if self.residuals_check else None,
            "selectivity": self.selectivity.to_dict()
            if self.selectivity else None,
            "matched_bbox_cad": list(self.matched_bbox_cad)
            if self.matched_bbox_cad else None,
            "unmatched_cells": self.unmatched_cells,
            "research_labels": self.research_labels,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CandidateEvidence":
        transform = (SimilarityTransform.from_dict(d["transform"])
                     if d.get("transform") else None)
        residuals_control = (ResidualStats.from_dict(d["residuals_control"])
                             if d.get("residuals_control") else None)
        residuals_check = (ResidualStats.from_dict(d["residuals_check"])
                           if d.get("residuals_check") else None)
        selectivity = (SelectivityStats.from_dict(d["selectivity"])
                       if d.get("selectivity") else None)
        coverage = d.get("cell_coverage")
        bbox = d.get("matched_bbox_cad")
        return cls(
            pdf_name=d["pdf_name"], dxf_name=d["dxf_name"],
            pdf_page_index=d["pdf_page_index"],
            dx_matched_entities=d["dx_matched_entities"],
            dxf_offered_control=d["dxf_offered_control"],
            dxf_offered_check=d["dxf_offered_check"],
            control_matched=d["control_matched"],
            check_matched=d["check_matched"],
            control_fraction=d["control_fraction"],
            check_fraction=d["check_fraction"], transform=transform,
            transform_source=d["transform_source"],
            reflection_hypothesis_tested=d.get(
                "reflection_hypothesis_tested", True),
            axis_swap_hypothesis_tested=d.get(
                "axis_swap_hypothesis_tested", True),
            eps_reflection_consensus=d.get("eps_reflection_consensus"),
            eps_direct_consensus=d.get("eps_direct_consensus"),
            cell_coverage=tuple(coverage) if coverage else None,
            entity_types_matched=d.get("entity_types_matched", 0),
            entity_types_offered=d.get("entity_types_offered", 0),
            layers_matched=d.get("layers_matched", 0),
            layers_offered=d.get("layers_offered", 0),
            top_layer_share=d.get("top_layer_share"),
            top_layer_name=d.get("top_layer_name"),
            match_rate_by_type=d.get("match_rate_by_type", {}),
            residuals_control=residuals_control,
            residuals_check=residuals_check, selectivity=selectivity,
            matched_bbox_cad=tuple(bbox) if bbox else None,
            unmatched_cells=d.get("unmatched_cells"),
            research_labels=d.get("research_labels", []),
            notes=d.get("notes", []))


@dataclass
class CandidatePair:
    """One pdf x dxf pair to be evaluated. Identity only, no geometry."""

    pdf_name: str
    pdf_page_index: int
    dxf_name: str
    pair_id: str

    @classmethod
    def make(cls, pdf_name: str, page_index: int, dxf_name: str) \
            -> "CandidatePair":
        return cls(pdf_name, page_index, dxf_name,
                   "%s|p%d|%s" % (pdf_name, page_index, dxf_name))


@dataclass
class Dataset:
    """A folder of engineering drawings plus optional reference geometry."""

    folder: str
    name: str
    pdfs: List[PdfDocument] = field(default_factory=list)
    dxfs: List[DxfDocument] = field(default_factory=list)
    skipped_files: Dict[str, str] = field(default_factory=dict)

    @property
    def n_pairs(self) -> int:
        return sum(d.page_count for d in self.pdfs) * len(self.dxfs)

    def provenance(self) -> dict:
        return {
            "dataset_folder": self.folder,
            "dataset_name": self.name,
            "pdfs": [{"name": p.name, "sha256": p.sha256,
                      "size_bytes": p.size_bytes, "pages": p.page_count}
                     for p in self.pdfs],
            "dxfs": [{"name": d.name, "sha256": d.sha256,
                      "size_bytes": d.size_bytes,
                      "units_declared": d.units.declared,
                      "units_name": d.units.name}
                     for d in self.dxfs],
        }
