# VP GeoConvert — Design Requirements

## Scope: plan geometry only

VP GeoConvert is a **2D plan georeferencing and conversion tool**.

- Surveying convention in the user-facing geodetic model: **X = Northing, Y = Easting**.
- Internal PDF/page coordinates must use neutral names such as `u, v`, never surveying `X, Y`.
- Elevation/Z is outside the georeferencing scope.
- VP GeoConvert must not calculate, transform, infer, validate, or assign elevations.
- If elevation values exist only as PDF text/annotations, they remain ordinary text/geometry and are not interpreted as heights.
- No artificial `Z=0` or other elevation is to be introduced as surveying data.

The production model uses four explicit 2D coordinate domains:

- PDF OBJECT: `p, q`;
- PDF PAGE: `u, v`;
- DXF CAD: `cad_x, cad_y`;
- SURVEY: `X = Northing`, `Y = Easting`.

These domains must not be mixed implicitly. In particular, CAD axes are not
SURVEY axes unless a separate user-supported CAD-to-SURVEY transformation has
been established.

## Coordinate systems: worldwide use

VP GeoConvert must not be tied to Finland, GK25, N2000, EPSG:3879, or any other national/local coordinate system.

- The program works from user-supplied planar coordinates and reference material.
- Any planar coordinate system may be used if the supplied control/reference data is internally consistent.
- CRS metadata may be recorded when supplied by the user/source, but georeferencing must not depend on a hard-coded national CRS.
- The program must not infer a CRS merely from coordinate magnitudes.

## Source protection

- Input PDF, DXF, LandXML, PLF, GT and other reference files are read-only.
- Source files must never be overwritten or modified.
- Generated/modified files are always new files.

## Project output workspace

By default, generated artifacts are placed in a dedicated output folder so source material remains untouched and results stay together.

The output root is user-configurable. A default application-managed workspace may be offered, but the operator must be able to choose another location.

Suggested project structure:

```
<ProjectName>/
  source references (not copied unless explicitly requested)
  output/
    <name>_GEOREFERENCED.dxf
    <name>_QA.pdf
    <name>_COMPARE.pdf       # only if operator explicitly saves it
    <name>.vpgc
```

`_GEOREFERENCED` is the mandatory suffix for the final georeferenced DXF. It is intentionally explicit rather than abbreviated. Source files are never overwritten.

Visual comparison is temporary by default. If the operator chooses to save it, `_COMPARE` is a mandatory suffix and the PDF contains no georeferencing/GeoPDF data.

## Project/session file

A `.vpgc` project/session file may store processing state such as source paths and hashes, control/check correspondences, transformation parameters, tolerances, selected layers, orientation settings, and QA state. It should not duplicate source geometry unless technically necessary.

## Traceability

QA should record source filenames, SHA-256 hashes, VP GeoConvert version, processing date/time, georeferencing method, transform parameters, CONTROL/CHECK counts, residual statistics, orientation/reflection state, and relevant source/revision metadata.

## Orientation

PDF/page orientation must not redefine CAD or surveying axes. Support AUTO
orientation plus explicit `SWAP X <-> Y`, `FLIP X`, `FLIP Y`, and `RESET` when
operating in SURVEY. Reflection/mirroring must be explicitly reported and
accepted rather than silently hidden. No implicit X/Y swap is allowed. Any
orientation change triggers full georeferencing and QA recalculation.

For PDF-to-DXF registration, the primary transformation route is PAGE to CAD.
PAGE to SURVEY is permitted only from explicit user-provided PAGE/SURVEY
correspondences. CAD to SURVEY is a separate optional route requiring explicit
user-provided CAD/SURVEY data; it is never inferred automatically.

## QA independence

CONTROL observations used to calculate a transformation are not independent proof of accuracy. Independent CHECK observations/geometry must be reported separately whenever available.

Residuals are computed only within one declared target domain. CONTROL and
CHECK observations from different target domains are not combined.


## Core file-format scope

Keep the core deliberately small. VP GeoConvert is an open-source, non-commercial conversion/georeferencing tool, not a general-purpose CAD package.

### Primary input and output

- Vector PDF is the primary conversion input.
- DXF is the primary georeferenced geometry output: `<name>_GEOREFERENCED.dxf`.

### Reference inputs for the core

The initial core should support, subject to implementation and test coverage:

- DXF — 2D reference geometry and layers.
- LandXML / XML — planar alignment/reference geometry and points; elevation values are ignored by the georeferencing model.
- PLF / GT — planar engineering/survey reference geometry where the format can be implemented reliably.
- CSV / TXT and similar point lists — explicit point IDs and X/Y coordinates.
- Manual X/Y entry — control/check coordinates without a reference file.

Every imported reference observation retains its source/provenance and role (for example CONTROL or CHECK). Different reference sources are not silently merged into one undifferentiated dataset.

### Formats outside the initial core

DWG, raster-plan workflows, GeoTIFF and other specialist/national/vendor formats are not required for the initial core. Do not add heavy dependencies merely to increase the format count.

The internal 2D geometry model and importer/exporter boundaries should be designed so additional converters can later be implemented as optional modules/plugins. A plugin framework itself is **not** an MVP requirement; only preserve a clean extension boundary now.
