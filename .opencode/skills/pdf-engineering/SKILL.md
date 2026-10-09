---
name: PDF Engineering
description: PDF and PDFium extraction of engineering vector geometry for VP GeoConvert. Use when opening or parsing a vector PDF, enumerating pages and page objects, identifying PATH, TEXT, IMAGE, and FORM objects, reading object bounds and object or page transformation matrices, reading PATH segments, recursively traversing FORM content with accumulated transforms, or reading OCG and layer information. Supports the RH2 Production Geometry Engine while preserving the completed PDFium proof of concept in poc/pdfium_probe. Enforces p/q OBJECT and u/v PAGE coordinate naming, PDFium as the approved engine, preservation of raw MOVE, LINE, CUBIC_BEZIER, and CLOSE primitives in double precision, and forbids premature ARC, CIRCLE, or other engineering-primitive recognition.
---

# PDF Engineering — PDFium Vector Extraction

Extraction rules for turning vector PDF content into the VP internal 2D geometry
model. The extractor translates; it does not interpret.

## Requirement sources

- `AGENTS.md` — sections "PDF extraction", "Geometry", "Coordinate model", "Current development stage"
- `docs/GEOMETRY_MODEL.md` — "Coordinate domains", "Numeric representation", "Basic geometry", "Paths and PDF extraction", "Entity metadata", "Source documents"
- `docs/DESIGN_REQUIREMENTS.md` — "Scope: plan geometry only", "Core file-format scope"
- `THIRD_PARTY_NOTICES.md` — "PDF engine" release gate
- `README.md` — planned workflow

Project-wide constraints and scope discipline: use the `vp-geoconvert` skill.

## Engine

PDFium is the approved PDF engine. Do not replace it with MuPDF/PyMuPDF without
explicit architectural approval.

No PDF engine or dependency is approved for distribution until its concrete
version, build, and full redistributed dependency set have passed the license
audit.

## Coordinate naming

```
PDF OBJECT  p, q
PDF PAGE    u, v
DXF CAD     cad_x, cad_y
SURVEY      X = Northing, Y = Easting
```

PDF object and page coordinates MUST NOT be called `X` or `Y` in public
interfaces, diagnostic terminology, logs, or JSON output. `X`/`Y` are reserved
for SURVEY. Keep OBJECT, PAGE, CAD, and SURVEY conceptually separate. The PDF
extractor performs explicit OBJECT-to-PAGE placement from PDF matrices; it does
not infer PAGE-to-CAD, PAGE-to-SURVEY, or CAD-to-SURVEY relationships. Approved
georeferencing routes are PAGE to CAD, PAGE to SURVEY from explicit user
correspondences, and a separately supplied CAD to SURVEY route. Do not
introduce `Z` or elevation processing.

## Object inventory

Enumerate pages, then enumerate the objects on each page, identifying PATH, TEXT,
IMAGE, and FORM where available. For each object inspect:

- object bounds
- the object transformation matrix and the page transformation matrix
- PATH segments
- source layer / PDF OCG where PDFium exposes sufficient information
- visibility state and source style (color, line width, dash/linetype) where available

## Preserve the raw path structure

Path extraction must preserve, unmodified and unrounded:

- `MOVE`
- `LINE`
- `CUBIC_BEZIER`
- `CLOSE`

Do not prematurely convert paths into guessed engineering primitives. Recognition
of line, polyline, arc, circle, and other engineering geometry is a **separate,
later stage**. When recognition later proposes `LINE`, `POLYLINE`, `ARC`, or
`CIRCLE` within tolerance, it must not silently destroy the original extracted
representation that QA and debugging depend on. Bézier geometry may later be
recognized or fitted, but the original extracted curve must remain available
until conversion is accepted.

## Recursive FORM traversal

FORM content must support recursive traversal with accumulated transforms. Nested
content inherits the accumulated matrix, not only its own local matrix. Apply
transforms in the correct composition order; a wrong order silently places
geometry in the wrong location.

## Precision and honesty

- 64-bit double precision end to end. Never round coordinates prematurely.
- Unit and page transformation metadata is stored separately from the coordinates.
- Metadata a source does not expose stays unknown rather than invented. Carry,
  where available: stable entity ID, source file ID, source object ID, source
  layer / OCG, source object type, coordinate domain, source style, visibility
  state, and extraction warnings.

## Current goal — RH2 Production Geometry Engine

The PDFium proof of concept is complete as a preliminary research stage and
remains isolated at `poc/pdfium_probe/`. RH2 production geometry must not depend
on research tooling or turn PDFium objects into the internal data model.

For RH2.1, this skill governs only the preserved PDF path representation and
the OBJECT/PAGE boundary. Full PDF import, automatic PDF-to-DXF matching, OCG
membership implementation, GUI, DXF writing, and unrelated production
expansion remain outside the approved stage.

## Reporting

A successful run is not proof of fidelity. State explicitly whether the result
was actually executed against a real PDF, and label unverified claims as
`NOT VERIFIED` rather than implying they were tested.
