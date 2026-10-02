# VP GeoConvert — Internal 2D Geometry Model

## Purpose

All readers and writers operate through one neutral internal 2D geometry model.

```
PDFium extractor ─┐
DXF importer      ├─> VP 2D Geometry Model ─> georeferencing / QA ─> DXF writer
LandXML importer  │
PLF/GT importer   │
CSV/TXT importer ─┘
```

The core must not make PDF, DXF, LandXML, or a national coordinate system its internal data model.

## Coordinate domains

Coordinate domains are explicit and must never be mixed implicitly.

### PAGE

Raw drawing/page coordinates use **u, v**.

- They are local coordinates from PDF or another non-georeferenced source.
- They must never be presented as surveying X/Y.
- Unit and page transformation metadata are stored separately.

### SURVEY

Georeferenced planar coordinates use:

- **X = Northing**
- **Y = Easting**

This convention applies throughout the UI, QA reports, control/check data, project files, logs, and public APIs of VP GeoConvert.

### Z / elevation

There is no surveying Z/H coordinate in the core geometry model.

Elevation-looking text remains text. Elevation fields encountered in reference formats are outside the georeferencing calculation and must not be silently converted to geometry elevation.

## Numeric representation

Geometry calculations use 64-bit floating point values.

No early rounding is allowed. Display formatting is separate from stored/calculated precision.

## Basic geometry

The initial model should remain deliberately small.

### Point2

A two-dimensional point in a declared coordinate domain.

### Line

Two endpoints.

### Polyline

Ordered vertices. May be open or closed.

### Arc

Center, radius, start angle, end angle and direction.

### Circle

Center and radius.

### Bezier

Cubic Bézier segment used where PDF paths cannot yet be represented reliably as a recognized CAD primitive.

Bézier geometry may later be recognized/fitted as arcs or other primitives, but the original extracted curve must remain available until conversion is accepted.

### Text

Text content plus insertion point, rotation, height/size information and source styling where available.

Text is geometry/annotation only. Numeric-looking text is not automatically interpreted as a coordinate or elevation.

### Image

Reference to an embedded raster object plus its page transform/bounds. Raster objects do not participate in coordinate fitting unless a future explicit raster workflow is implemented.

## Paths and PDF extraction

PDF paths may contain MOVE, LINE, CUBIC_BEZIER and CLOSE operations. The extractor should preserve the original path structure before optional CAD recognition.

Recognition may produce LINE, POLYLINE, ARC or CIRCLE when tolerances support it. Recognition must not silently destroy the original extracted representation needed for QA/debugging.

PDF FORM objects are recursively traversed with their accumulated transforms.

## Entity metadata

Each entity should carry lightweight metadata where available:

- stable internal entity ID;
- source file ID;
- source entity/object ID when available;
- source layer / PDF OCG;
- source object type;
- coordinate domain;
- source style: color, line width, dash/linetype;
- visibility state where available;
- extraction/import warnings;
- optional provenance tags.

Metadata unsupported by a source remains unknown rather than invented.

## Source documents

Every loaded source is represented separately and assigned a stable source ID.

Recommended source metadata:

- original filename;
- absolute/relative path as appropriate for the local project;
- SHA-256;
- format;
- detected/imported units;
- revision/date metadata when reliably available;
- role: PRIMARY, REFERENCE, or other explicit role;
- importer name/version.

Source files are read-only.

## Reference observations

Georeferencing observations are separate from drawing entities.

### Roles

- CONTROL — participates in fitting the transformation.
- CHECK — independent validation; never participates in fitting.
- DISABLED — retained but excluded.

### Source groups

Each observation belongs to a source group such as:

- GRID
- CONTROL_POINTS
- ALIGNMENT
- GEOMETRY
- MANUAL
- other explicitly named importer/source.

Residuals and statistics are reportable per source group.

## Correspondence

A correspondence links PAGE-domain information to SURVEY-domain information.

Examples:

- page point ↔ survey control point;
- page grid intersection ↔ entered X/Y;
- page line/curve feature ↔ reference alignment feature.

Automatic matching creates **proposals**, not silently accepted correspondences. Operator acceptance state is stored.

## Transformation

The primary transformation is a 2D similarity/Helmert transformation.

Stored transformation state includes:

- translation;
- uniform scale;
- rotation;
- explicit orientation mapping;
- reflection state;
- fitted CONTROL IDs;
- residual statistics.

Affine transformation is diagnostic unless explicitly promoted by a future design decision.

Changing axis orientation, control membership, or correspondence invalidates the current transformation and triggers recalculation of QA.

## Orientation

Orientation is explicit rather than hidden inside ambiguous axis naming.

Supported operator states:

- AUTO
- SWAP X <-> Y
- FLIP X
- FLIP Y
- RESET

Ordinary drawing rotation is handled mathematically by the similarity transformation. Reflection/mirroring is separately detected and visibly reported. A reflected solution must not be silently accepted.

## Units

Units belong to a coordinate domain/source, not to individual arbitrary entities.

The system must distinguish raw PDF/page units from real planar coordinate units.

No CRS is inferred from coordinate magnitude.

The output unit must be explicit. Initial surveying/engineering workflows are expected to commonly use metres, but the internal architecture must not hard-code Finland or a particular CRS.

## QA data

QA is derived from immutable source geometry plus explicit correspondence/transformation state.

Store/report at least:

- CONTROL residuals;
- CHECK residuals separately;
- RMS;
- median;
- p95 where sample size permits meaningful reporting;
- maximum residual;
- per-observation dX/dY and radial residual;
- scale;
- rotation;
- translation;
- orientation/reflection state;
- source/revision warnings.

A low CONTROL RMS is not presented as independent validation.

## Project persistence

The `.vpgc` project stores processing state and references to sources rather than modifying source files.

It may store:

- source paths and SHA-256 hashes;
- source roles;
- importer settings;
- layer selections;
- correspondence definitions;
- CONTROL/CHECK roles;
- orientation;
- transformation parameters;
- tolerances;
- QA state;
- operator-approved exceptions.

Where practical, reopening a project verifies source hashes and warns if a source file has changed.

## Extension boundary

Readers/importers translate external formats into this internal model. Writers/exporters translate the internal model into external formats.

Future optional conversion modules must not require changes to georeferencing mathematics merely because a new file format is added.

The initial project does **not** require a runtime plugin framework; it requires clean importer/exporter interfaces so one can be introduced later without redesigning the geometry core.
