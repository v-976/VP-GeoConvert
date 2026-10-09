# Archived research scripts

These are the superseded Set A, Set B1 and Set B2 research scripts. They are
kept as a record of the path taken. They are **not** used by the canonical
harness and **must not be imported into new work**: several functions here are
known to be unsafe and will silently produce wrong numbers.

The canonical implementation is `research/benchmark/`.

## Disposition map

### Set A — `archive/geometry_benchmark/`

| file | disposition | note |
|---|---|---|
| `matcher.py` | SUPERSEDED | `fit_similarity` and the grid were sound and were reimplemented cleanly in `benchmark/similarity.py` and `benchmark/geometry.py`. `ransac` was **not** carried over: its probe set was the longest segments only, which drove the probe score to zero even for a correct transform on real-world data. |
| `pdf_reader.py` | SUPERSEDED | probe stdout parsing reimplemented in `benchmark/pdf_extract.py`, now matrix-applied at parse time and with MOVE/LINE pairing made explicit |
| `dxf_reader.py` | SUPERSEDED | reimplemented in `benchmark/dxf_extract.py` |
| `_test_fit.py` | ARCHIVED | self-test of `fit_similarity`; superseded by `tests/test_harness.py` |
| `benchmark_lib.py` | ARCHIVED | **contains a hardcoded dataset path** |
| `checks.py` | ARCHIVED | **contains a hardcoded dataset path** |
| `circles.py` | ARCHIVED | **contains a hardcoded dataset path** |
| `diagnose.py` | ARCHIVED | **contains hardcoded dataset path AND hardcoded transforms** for one specific drawing |
| `explore_dxf.py` | ARCHIVED | **contains a hardcoded dataset path** |
| `final_report.py` | ARCHIVED | **contains a hardcoded dataset path** |
| `profile_est.py` | ARCHIVED | superseded |
| `run_benchmark.py` | ARCHIVED | **contains a hardcoded dataset path** |
| `signatures.py` | ARCHIVED | **contains a hardcoded dataset path** |
| `stage1_coarse.py` | ARCHIVED | **contains a hardcoded dataset path** |
| `stage2_hypotheses.py` | ARCHIVED | **contains a hardcoded dataset path** |
| `stage3_verify.py` | ARCHIVED | **contains a hardcoded dataset path** |
| `stage4_final.py` | ARCHIVED | **contains a hardcoded dataset path** |
| `transform_est.py` | ARCHIVED | **contains a hardcoded dataset path** |
| `dirmatch.py` | ARCHIVED | superseded |

### Set B1 — `archive/realworld_benchmark/b1_*.py`

| file | disposition | note |
|---|---|---|
| `b1_analysis.py` | **DEFECTIVE** | see defect 1 below |
| `b1_final.py` | **DEFECTIVE** | see defect 2 below; `seg_matches` was sound and was reimplemented |
| `b1_residuals.py` | **DEFECTIVE** | see defect 3 below. Not fixable in place: the metric itself mixed domains. |
| `b1_match.py` | ARCHIVED | superseded by `b1_analysis.py` |
| `b1_calibrate.py` | ARCHIVED | historically useful: this is the run that exposed defect 1 |
| `b1_strict.py` | ARCHIVED | historically useful: showed that requiring one PDF segment per CAD line is too strict, because PDF vectorisation splits long lines into collinear fragments |
| `b1_defence.py` | ARCHIVED | `spatial_split_balanced` was sound and was reimplemented in `benchmark/split.py` |
| `b1_report.py` | ARCHIVED | historical record of the B1 conclusion |

### Set B2 — `archive/realworld_benchmark/b2_*.py` and helpers

| file | disposition | note |
|---|---|---|
| `b2_core.py` | SUPERSEDED | 46 KB containing ~600 lines of inline self-tests and dead code (`search`, `ransac` wrapper, `signature_peaks`, `translation_votes`, `length_peaks`, `_signature`, `_cheap_count`, `_reproduces`, `seg_matches_set`). The live algorithm was split across `similarity.py`, `geometry.py`, `matching.py`. Self-tests moved to `tests/`. |
| `b2_matrix.py` | SUPERSEDED | see defects 4 and 5 below |
| `b2_selectivity.py` | SUPERSEDED | its `spread_sample` correction was kept; the ordered-prefix bug it worked around is now impossible because `split.py` exposes `spread_sample` directly |
| `b2_deep.py` | SUPERSEDED | residual reporting reimplemented in `benchmark/validation.py` |
| `b2_cross.py` | SUPERSEDED | reimplemented in `benchmark/crossref.py` |
| `dxf_forensics.py` | SUPERSEDED | folded into `benchmark/dxf_extract.py` and `benchmark/reporting.py` |
| `dxf_geom.py` | SUPERSEDED | reimplemented in `benchmark/dxf_extract.py` |
| `pdf_forensics.py` | SUPERSEDED | reimplemented in `benchmark/forensics.py` with three-valued OCG status |
| `probe_summary.py` | SUPERSEDED | folded into `benchmark/pdf_extract.py` and `benchmark/reporting.py` |

## Defects found during the RH1 audit

Each of these produced wrong numbers at the time. The canonical harness holds
each one closed with a named test in `tests/test_harness.py`.

### 1. Proximity helper that never enforced its tolerance

`b1_analysis.nearest_point` returned the nearest candidate found in adjacent
grid cells **without testing the distance**. Callers that forgot the check got
matches for almost any input. Measured consequence: a 100 m translation error
still matched 328 of 826 entities, and 30 of 100 random transforms scored 50 or
more, above the accepted hypothesis itself.

Fix: `SegmentIndex.nearest_directed_deviation` returns `None` when nothing is
within tolerance, and the tolerance is applied inside the index so no caller
can skip it. Test:
`TestMatching.test_tolerance_is_enforced`.

The function itself was later patched to be correct at its call sites, but it
remained a footgun because the guarantee lived in the caller.

### 2. Defective refit that degraded a correct hypothesis

`b1_final.refit` extracted correspondences incorrectly and reduced a hypothesis
that matched 324 entities to one that matched 1.

Fix: refitting was removed from the canonical harness. The clustered exact-fit
representative is frozen after CONTROL-only selection. Constructing a PAGE
midpoint by applying the inverse of the same transform is circular and returns
that transform by construction; it is not an independent refit. A future
refit requires explicit PAGE/CAD correspondences.

### 3. Residual metric that mixed PAGE and CAD coordinates

`b1_residuals` built a spatial index over CAD-space DXF segments and queried it
with PAGE-space PDF coordinates, comparing the result against a tolerance
expressed in PAGE points. Two domains in one comparison produced meaningless
numbers, and the run reported 0 of 4000.

Fix: `benchmark/validation.py` computes residuals only in CAD. A CAD point is
transformed into PAGE space to find the nearest feature, the matched PAGE point
is transformed back into CAD, and the difference is taken in CAD. The
PAGE-point tolerance is used only as a match gate, and its CAD equivalent is
reported separately so the two are never confused. Tests:
`TestDomainSeparation.test_residual_values_are_in_cad_not_page_points`,
`test_residuals_must_be_cad_domain`.

### 4. Ordered-prefix "spatial sample"

`b2_matrix` calibrated selectivity on `control[:250]`. The control list is
ordered by grid cell, so that prefix is the top quarter of the sheet, not a
representative sample. Measured consequence: one candidate read 7 on the
prefix while the full pool read 90, and four candidate conclusions were
wrong.

Fix: `split.spread_sample` is the only subsampling tool, and the selectivity
stage uses it. Test: `TestSplit.test_spread_sample_is_not_an_ordered_prefix`.

### 5. Magic thresholds standing in for product policy

`b2_matrix` classified candidates `STRONG_CANDIDATE`, `PARTIAL_CANDIDATE`,
`AMBIGUOUS`, `NO_EVIDENCE` from absolute counts and fractions
(`sub_n >= 30 and margin_random >= 5.0 and ctrl_frac >= 0.10 ...`). Those
numbers were invented during that run and have no measured basis, and they are
product decisions made by a research script.

Fix: the harness emits measurements and descriptive labels only. Each label
carries the number behind it. There is no verdict field, and `research_labels`
is a list so a single strongest signal is not forced.

### 6. Two further defects found and fixed during the RH1 rewrite

* **Direction conversion sign error.** The bucket lookup added the rotation to
  a CAD direction instead of subtracting it. Because the transform maps PAGE to
  CAD, this silently produced an empty hypothesis set rather than a wrong one,
  so it was invisible until a transform stopped being recovered on synthetic
  data. Fixed in `matching._pair_fits`, with the reason stated in the
  docstring.
* **Translation clustered in CAD units.** Binning translation in CAD units
  collapses when the CAD unit is small: for a millimetre drawing every
  candidate landed in one bin and the cluster mean was meaningless. Fixed by
  mapping a fixed CAD reference point into PAGE space before binning, so the bin
  width is in PAGE points.

### 7. Three-valued OCG status

`pdf_forensics` and the B1 stage treated "no `/OCProperties` in the byte
stream" as "no OCG". That reasoning is wrong for any PDF with object streams or
an uncompressed cross-reference stream, and it produced a wrong conclusion on
Set B1, whose PDF did carry 92 OCG objects. Fix: `forensics.structural_scan`
returns `PRESENT`, `ABSENT` or `NOT_VERIFIED` together with the evidence.

### 8. RH1 implementation defects found during regression recovery

The interrupted first RH1 implementation had four additional problems. They
were found by independently inspecting the regression rather than accepting a
completed 12/12 matrix as proof of correctness.

* **LINE after CUBIC_BEZIER was discarded.** The parser reset the current path
  point on every cubic segment. In PDF path semantics the cubic endpoint
  becomes the current point, so a following LINE begins there. The defect
  reduced `IR208122...pdf` from 90,594 usable straight PAGE segments to 51,959
  and caused the full matrix to select a repeated-layer artefact instead of the
  previously confirmed relationship. Fixed in `pdf_extract.py`.
* **Axis swap was not an axis swap.** `axis_swapped()` applied a 90-degree
  target rotation and an unrelated translation. It now exactly satisfies
  `(cad_x', cad_y') = (cad_y, cad_x)` and is tested point-by-point.
* **Spatial cell coverage used two coordinate grids.** The matched subset was
  binned over its own bounding box and compared with cells binned over the full
  drawing's box. Both are now binned against the full drawing bounds.
* **`$INSUNITS=0` was treated as metres.** DXF code 0 means unitless, so no
  millimetre conversion is valid. It now remains raw CAD units.

The first RH1 run (`harness_version=1.0.0`) is therefore superseded. The
corrected regression is produced by version 1.0.1.

## Why archive rather than delete

The defective scripts document how the conclusions of Set B1 and Set B2 were
reached, including the runs that disproved earlier claims. That reasoning is
part of the record. They are kept read-only-by-convention in `archive/` so the
active tree contains exactly one implementation of every function.
