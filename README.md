# VP GeoConvert

Open-source, local-first PDF-to-georeferenced-DXF converter for surveying and engineering QA.

> **Early development:** VP GeoConvert is not yet suitable for production surveying or construction work.

## Core principles

- Fully local and offline operation.
- No telemetry and no transmission of project files, coordinates, elevations, geometry, or other user data.
- No built-in project coordinates, elevations, or site-specific georeferencing data. Spatial information comes only from user-supplied files and parameters.
- Vector PDF extraction and DXF generation with georeferencing and independent QA.
- Georeferencing methods planned: GRID, ALIGNMENT / GEOMETRY, CONTROL POINTS, and COMBINED.
- Surveying convention: **X = Northing, Y = Easting**.
- Results must be independently verified before surveying, setting-out, construction, or engineering use.

## Planned workflow

`PDF -> vector extraction -> CAD geometry -> georeferencing -> independent QA -> DXF export`

Visual comparison exports use the mandatory filename suffix `_COMPARE.pdf` and are intentionally **not georeferenced**.

## Licensing and safety

The project is intended to be released under the MIT License. A separate engineering/surveying disclaimer and third-party notices will be maintained. Dependency licensing will be audited before any distributable build is published.

No distributable build should be treated as approved until the dependency and license audit is complete.


## Coordinate orientation

VP GeoConvert must not assume that PDF page axes correspond to surveying axes. Drawings may be rotated, axis-swapped, or mirrored.

- The surveying convention is always **X = Northing, Y = Easting**.
- `AUTO` should determine PDF-to-survey axis orientation from available control information.
- Manual corrections must include `SWAP X <-> Y`, `FLIP X`, `FLIP Y`, and `RESET`.
- Ordinary page rotation (including 90/180/270 degrees) should be handled by the georeferencing transformation rather than silently redefining surveying axes.
- Possible reflection/mirroring must be explicitly detected and reported.
- Any orientation change must trigger a complete georeferencing and QA recalculation.
- Automatic orientation must remain visible to the operator; ambiguous orientation must produce a warning rather than a silent assumption.
