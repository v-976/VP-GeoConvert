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
