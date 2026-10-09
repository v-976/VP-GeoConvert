---
name: QA and Geodesy
description: Georeferencing and independent engineering/surveying QA for VP GeoConvert. Use for the 2D similarity/Helmert transformation, least-squares adjustment, CONTROL and CHECK correspondences and observation roles, same-domain residual components and radial residuals, RMS, median, p95, and maximum statistics, orientation handling with AUTO, SWAP X Y, FLIP, and RESET, reflection or mirroring detection, affine transformation as a diagnostic alternative, QA reporting, and .vpgc persistence of transformation and QA state. Enforces strict 2D, explicit OBJECT/PAGE/CAD/SURVEY domains, X = Northing and Y = Easting only in SURVEY, CONTROL-only fitting and selection, and fully separate CHECK statistics.
---

# QA and Geodesy — Georeferencing and Independent Verification

Georeferencing must be reproducible and independently checkable. A low residual
is a measurement, not proof.

## Requirement sources

- `AGENTS.md` — sections "Georeferencing", "QA philosophy", "Coordinate model", "Scope"
- `docs/GEOMETRY_MODEL.md` — "Coordinate domains", "Reference observations", "Correspondence", "Transformation", "Orientation", "Units", "QA data", "Project persistence"
- `docs/DESIGN_REQUIREMENTS.md` — "Orientation", "QA independence", "Traceability", "Coordinate systems: worldwide use"
- `README.md` — "Coordinate orientation", "Core principles"
- `DISCLAIMER.md` — independent verification obligation

Project-wide constraints: use the `vp-geoconvert` skill.

## Strictly 2D

Elevation and `Z` are outside the georeferencing scope. Do not calculate,
transform, validate, infer, or assign elevation, and do not introduce an
artificial `Z = 0`. Elevation values appearing in reference formats are outside
the georeferencing calculation and must not be silently converted to geometry
elevation. Elevation-looking text remains text.

## Coordinate domains never mixed

```
PDF OBJECT  p, q
PDF PAGE    u, v
DXF CAD     cad_x, cad_y
SURVEY      X = Northing, Y = Easting
```

The surveying convention applies whenever the declared domain is SURVEY. It
does not rename PAGE or CAD axes. Never infer a CRS from coordinate magnitudes,
and never bind the program to a national or local coordinate system.

## Primary transformation

2D similarity / Helmert transformation is the primary model.

Approved routes are PAGE to CAD for PDF-to-DXF registration, PAGE to SURVEY
from explicit user-provided correspondences, and CAD to SURVEY as a separate
optional transformation from explicit user-provided correspondences. CAD to
SURVEY is never inferred. Every transformation records and enforces its source
and target domains.

- Minimum: 2 CONTROL points.
- With 3+ CONTROL points: least-squares adjustment with residual analysis.

Stored transformation state: translation, uniform scale, rotation, explicit
orientation mapping, reflection state, fitted CONTROL IDs, residual statistics.

Changing axis orientation, CONTROL membership, or correspondences invalidates
the current transformation and triggers full recalculation of QA.

## CONTROL and CHECK are separate classes

- CONTROL observations participate in transformation estimation.
- CHECK observations **NEVER** participate in transformation estimation or
  selection.
- CONTROL and CHECK statistics must remain separate in every report.
- Each observation retains its source and role (for example CONTROL or CHECK).
  Different reference sources are never silently merged into one undifferentiated
  dataset; residuals are reportable per source group.
- A low CONTROL RMS is never presented as independent validation. CONTROL
  observations used to calculate a transformation are not independent proof of
  accuracy.

## Residuals and statistics

Residuals are computed only after both values are expressed in the same
declared target domain. PAGE, CAD, and SURVEY values are never subtracted from
one another. In CAD, components use CAD-axis terminology such as
`d_cad_x`, `d_cad_y`; `dX`, `dY` are reserved for SURVEY.

Per observation, report:

- target-domain component residuals
- radial residual

Aggregate statistics:

- RMS
- median
- p95, where the sample size permits meaningful reporting
- maximum residual

Also report scale, rotation, translation, orientation/reflection state, and
source/revision warnings. QA is derived from immutable source geometry plus
explicit correspondence and transformation state.

## Affine transformation

Affine is diagnostic unless explicitly promoted by a future approved design
decision. It MUST NOT silently replace Helmert merely because it produces
smaller residuals. Report it as an explicit, separately labelled alternative.

## Orientation and reflection

Supported operator states: `AUTO`, `SWAP X <-> Y`, `FLIP X`, `FLIP Y`, `RESET`.

- PDF/page orientation must not redefine CAD or surveying axes.
- Ordinary drawing rotation, including 90/180/270 degrees, is handled
  mathematically by the transformation rather than by silently redefining
  surveying axes.
- Reflection or mirroring must never be silently accepted. Detect it, report it
  visibly, and require explicit acceptance.
- Automatic orientation must remain visible to the operator; ambiguous
  orientation produces a warning rather than a silent assumption.
- Any orientation change triggers a complete georeferencing and QA
  recalculation.

## Error classes

Systematic/global disagreement and local geometry/revision disagreement are
different error classes and are reported differently. Never hide a discrepancy
by automatically choosing a more flexible transformation. A large CHECK residual
after a small CONTROL residual is a finding to surface, not to tune away.

## Traceability

QA records source filenames, SHA-256 hashes, VP GeoConvert version, processing
date/time, georeferencing method, transform parameters, CONTROL/CHECK counts,
residual statistics, orientation/reflection state, and relevant source/revision
metadata. Source files are read-only; a `.vpgc` project stores state and
references, and reopening a project verifies source hashes and warns when a
source file has changed.

## Bottom line

The current milestone is RH2 — Production Geometry Engine. RH2.1 is limited to
the approved 2D geometry, transformation, CONTROL fitting, and independent
CHECK evaluation foundation.

Successful computation is not proof of correct georeferencing. Engineering
results must remain traceable and independently verifiable before any
surveying, setting-out, construction, or design use.
