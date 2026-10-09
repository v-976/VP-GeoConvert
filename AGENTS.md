# AGENTS.md — VP GeoConvert

## Project role

You are the implementation agent for VP GeoConvert.

VP GeoConvert is an open-source, local-first engineering tool for converting vector engineering PDF drawings into georeferenced 2D DXF with independent QA.

This repository contains engineering and surveying software. Coordinate correctness, geometry preservation, traceability, privacy, and reproducibility are more important than implementation speed.

Do not redesign the product while implementing it.

---

## Mandatory reading

Before modifying code, read:

- README.md
- DISCLAIMER.md
- PRIVACY.md
- THIRD_PARTY_NOTICES.md
- docs/DESIGN_REQUIREMENTS.md
- docs/GEOMETRY_MODEL.md

These documents define the current project requirements.

If implementation and project documentation disagree, STOP and report the conflict.

Do not silently reinterpret, weaken, replace, or remove an established requirement.

---

## Repository safety

This working copy is a SANDBOX.

The authoritative upstream project is:

v-976/VP-GeoConvert

The sandbox may fetch from upstream for comparison, but PUSH TO UPSTREAM IS DISABLED intentionally.

Rules:

- Never attempt to restore or bypass disabled push access.
- Never change Git remotes unless explicitly instructed.
- Never force-push.
- Never rewrite existing upstream history.
- Local commits are allowed and encouraged.
- Keep commits small, coherent, and reversible.
- Do not commit generated build directories, binaries, caches, temporary files, test output, or third-party binary packages unless explicitly required.

Changes made here are experimental until reviewed and accepted into the authoritative repository.

---

## Scope

VP GeoConvert is NOT a general CAD application.

Current core pipeline:

Vector PDF
→ PDF vector extraction
→ VP internal 2D geometry
→ georeferencing
→ independent QA
→ georeferenced 2D DXF

Do not add unrelated product features.

Do not introduce:

- cloud processing
- telemetry
- analytics
- AI dependencies
- raster-plan conversion
- DWG support
- full CAD editing
- plugin frameworks
- unnecessary heavy dependencies

unless explicitly approved.

---

## Coordinate model

The application is strictly 2D.

Coordinate domains are explicit and must never be mixed implicitly:

- PDF OBJECT: `p, q`; source object coordinates used while interpreting PDF
  object geometry and transforms.
- PDF PAGE: `u, v`; page-local drawing coordinates.
- DXF CAD: `cad_x, cad_y`; native planar coordinates of DXF reference
  geometry.
- SURVEY: `X = Northing`, `Y = Easting`; user-provided project/survey
  coordinates.

Survey/project coordinate convention:

X = Northing
Y = Easting

PDF/page coordinates MUST NOT be called X/Y in public project interfaces or diagnostic terminology.

Use:

u = PDF/page horizontal coordinate
v = PDF/page vertical coordinate

Keep OBJECT, PAGE, CAD, and SURVEY coordinate spaces conceptually separate.
Do not treat `cad_x, cad_y` as SURVEY `X, Y` without a separate transformation
based on explicit user-provided data.

Do not introduce Z/elevation processing.

Do not:

- calculate Z
- transform Z
- validate Z
- assign Z=0 to 2D geometry
- interpret drawing text such as +24.35 as elevation automatically

Elevation-looking text remains text unless a future explicitly approved feature states otherwise.

---

## Geometry

The internal geometry model is 2D and uses double precision.

Preserve source geometry as faithfully as possible.

PDF path extraction should initially preserve:

- MOVE
- LINE
- CUBIC_BEZIER
- CLOSE

Do not prematurely convert paths into guessed engineering primitives.

Recognition of:

- line
- polyline
- arc
- circle
- other engineering geometry

belongs to a separate recognition stage.

Never round coordinates prematurely.

---

## PDF extraction

PDFium is the approved PDF engine.

Do not replace PDFium with MuPDF/PyMuPDF without explicit architectural approval.

The intended extraction architecture is:

PDF
→ PDFium
→ VP PDF Vector Extractor
→ VP 2D Geometry

FORM objects must eventually support recursive traversal with accumulated transforms.

OCG/layer information should be preserved when PDFium exposes sufficient information.

---

## Georeferencing

Primary model:

2D similarity / Helmert transformation

For PDF-to-DXF registration, the primary route is PAGE to CAD.

PAGE to SURVEY is permitted only when the user explicitly supplies PAGE and
SURVEY correspondences. CAD to SURVEY is a separate optional transformation,
also based only on explicit user-provided data. Neither a CAD-to-SURVEY mapping
nor a CRS may be inferred automatically.

Minimum:
2 CONTROL points

With 3+ CONTROL points:
use least-squares adjustment and residual analysis.

Affine transformation is diagnostic/optional and MUST NOT silently replace Helmert merely because it produces smaller residuals.

CONTROL observations participate in transformation estimation.

CHECK observations NEVER participate in transformation estimation.

CONTROL and CHECK statistics must remain separate.

Residuals must compare coordinates in one declared target domain only. Never
subtract PAGE, CAD, and SURVEY coordinates from one another.

Reflection/mirroring must never be silently accepted.

---

## QA philosophy

Successful computation is not proof of correct georeferencing.

QA must remain independent from transformation fitting where possible.

Systematic/global disagreement and local geometry/revision disagreement are different error classes.

Never hide discrepancies by automatically choosing a more flexible transformation.

Engineering results must remain traceable and independently verifiable.

---

## Privacy

VP GeoConvert is local-first and offline-capable.

Project files, drawings, coordinates, geometry, elevations, project metadata, telemetry, crash data, and user information must not be transmitted externally.

Do not introduce network functionality into application code without explicit architectural approval.

Development tooling may access normal software-development services when required, but runtime application behavior must remain local.

---

## Dependencies and licensing

Own project code is intended for a permissive open-source license.

Prefer dependencies using permissive licenses such as:

- MIT
- BSD
- Apache-2.0

Any dependency using or involving:

- GPL
- LGPL
- AGPL
- MPL
- unusual/non-standard licenses
- unclear licensing
- dual licensing

must be reported before adoption.

Do not copy code from reference implementations merely because it is publicly visible.

Record relevant third-party dependencies for later THIRD_PARTY_NOTICES review.

---

## Working method

Before implementing a task:

1. Inspect the existing repository.
2. Read relevant project documentation.
3. Identify the smallest implementation that satisfies the task.
4. Avoid unrelated refactoring.
5. Implement.
6. Build.
7. Run appropriate tests or diagnostic execution.
8. Inspect the result.
9. Report exactly what changed and what remains unverified.
10. Commit locally only after the implementation is coherent.

Never claim something was tested if it was not actually tested.

Clearly distinguish:

- IMPLEMENTED
- BUILD VERIFIED
- RUNTIME VERIFIED
- TESTED WITH REAL DATA
- NOT VERIFIED

Do not fabricate test results.

---

## Current development stage

The PDFium proof of concept is complete as a preliminary research stage and
remains isolated in `poc/pdfium_probe/`.

The current engineering milestone is:

RH2 — PRODUCTION GEOMETRY ENGINE

The immediate RH2.1 scope is the minimal independent 2D geometry and coordinate
transformation core: explicit OBJECT, PAGE, CAD, and SURVEY domains; preserved
PDF path commands; 2D similarity/Helmert fitting; and separate CONTROL and
CHECK evaluation.

RH2 does not relax the product boundaries above. In particular, do not add a
GUI, automatic PDF-to-DXF matching, full PDF/DXF import or DXF export, affine
replacement, CRS inference, elevation/3D processing, cloud functionality,
telemetry, or unrelated production features unless separately approved.

---

## Decision rule

When uncertain:

Do not guess silently.

Investigate the repository and relevant API/documentation first.

If a decision would alter an established VP GeoConvert architectural requirement, stop and report it instead of making the change.

A new proposal is acceptable only when it solves a concrete technical problem, does not unnecessarily expand scope, preserves privacy and licensing requirements, and remains compatible with the strict 2D architecture.
