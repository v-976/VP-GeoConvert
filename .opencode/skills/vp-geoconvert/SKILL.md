---
name: VP GeoConvert
description: Project-wide architecture rules and scope boundaries for VP GeoConvert, the local-first vector-PDF to georeferenced 2D DXF converter. Use for any task touching project architecture, the internal 2D geometry model, pipeline stages, project/output file formats, output naming, traceability, privacy constraints, dependency licensing, MVP scope, or the current engineering milestone. Enforces preservation of already-approved architectural decisions and forbids silent scope expansion, silent requirement reinterpretation, and silent refactoring. Do not use for PDF/PDFium extraction internals (use pdf-engineering) or for georeferencing mathematics and QA statistics (use qa-geodesy).
---

# VP GeoConvert — Project Architecture

Project-specific guardrails. This skill does not restate the project documents; it
points at them and names the decisions that must survive implementation.

## Authoritative requirement sources

Read these before architectural work. They are the requirements; this skill is
not.

- `AGENTS.md` — project role, mandatory reading, repository safety, decision rule
- `README.md` — product scope, core principles, planned workflow, coordinate orientation
- `docs/DESIGN_REQUIREMENTS.md` — scope, coordinate systems, source protection, output workspace, project/session file, traceability, orientation, QA independence, core file-format scope
- `docs/GEOMETRY_MODEL.md` — internal 2D geometry model, coordinate domains, numeric representation, entity metadata, reference observations, correspondence, transformation, QA data, persistence, extension boundary
- `PRIVACY.md` — local-only operation
- `DISCLAIMER.md` — engineering/surveying disclaimer
- `THIRD_PARTY_NOTICES.md` — dependency and license release gate

Domain-specific detail lives in the sibling skills `pdf-engineering` and `qa-geodesy`.

## Pipeline shape

```
Vector PDF
-> PDF vector extraction
-> VP internal 2D geometry
-> georeferencing
-> independent QA
-> georeferenced 2D DXF
```

The PDF engine must never become the internal data model. Neither may DXF,
LandXML, PLF/GT, or a national coordinate system.

## Non-negotiable constraints

- Strictly 2D. Do not calculate, transform, infer, validate, or assign elevation. No `Z = 0`. Text such as `+24.35` stays text.
- Four explicit 2D coordinate domains: PDF OBJECT uses `p, q`; PDF PAGE uses
  `u, v`; DXF CAD uses `cad_x, cad_y`; SURVEY uses `X = Northing, Y = Easting`.
  Never mix them implicitly. Approved routes are PAGE to CAD, PAGE to SURVEY
  from explicit user correspondences, and a separate CAD to SURVEY route from
  explicit user data. Never infer CAD to SURVEY, a CRS, or an axis swap.
- No CRS tied to any country or local system. Never infer a CRS from coordinate magnitudes.
- 64-bit double precision. No early rounding; formatting is a display concern.
- Source files are read-only. Never overwrite or modify an input PDF, DXF, LandXML, PLF, or GT. Generated output is always a new file.
- Mandatory output naming: `_GEOREFERENCED` for the final DXF, `_COMPARE.pdf` for a saved visual comparison, and `_COMPARE.pdf` deliberately carries no georeferencing data.
- Runtime is local and offline. No network functionality in application code without explicit architectural approval.
- Prefer permissive dependencies: MIT, BSD, Apache-2.0. Report GPL, LGPL, AGPL, MPL, dual-licensed, or unclear licenses before adoption. Do not copy code from a reference implementation just because it is publicly visible.
- QA stays independent of transformation fitting wherever possible. CONTROL
  participates in fitting; CHECK participates in neither fitting nor selection.
  Residuals compare values only within one declared target domain.

## Scope discipline

Not a general CAD application. Do not introduce cloud processing, telemetry,
analytics, AI dependencies, raster-plan conversion, DWG support, full CAD
editing, a plugin framework, or heavy dependencies without explicit approval.

A runtime plugin framework is not an MVP requirement. Preserve a clean
importer/exporter extension boundary; do not build the framework.

## Current engineering milestone

The PDFium proof of concept is complete as a preliminary research stage and
remains isolated at `poc/pdfium_probe/`.

The current milestone is RH2 — Production Geometry Engine. RH2.1 is limited to
the independent 2D geometry and coordinate-transformation core defined in
`AGENTS.md` and `docs/GEOMETRY_MODEL.md`. It does not add GUI, automatic
matching, full import/export, affine replacement, 3D/elevation, CRS inference,
network functionality, telemetry, or unrelated product features.

## Stop rule

If implementation and project documentation disagree, stop and report the
conflict. Do not silently reinterpret, weaken, replace, or remove an
established requirement. When a decision would alter an approved architectural
requirement, stop and report rather than making the change.

## Working method

Inspect the repository, read the relevant documentation, find the smallest
implementation that satisfies the task, avoid unrelated refactoring, implement,
build, run appropriate diagnostics, inspect the result, then report honestly.
Keep changes small, coherent, and reversible. Commit locally only when the
implementation is coherent; the sandbox push target is intentionally disabled.

## Reporting discipline

State results using these distinct labels, and never claim a test that was not run:

`IMPLEMENTED` / `BUILD VERIFIED` / `RUNTIME VERIFIED` /
`TESTED WITH REAL DATA` / `NOT VERIFIED`

Successful computation is not proof of correct results.
