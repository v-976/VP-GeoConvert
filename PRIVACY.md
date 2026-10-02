# Privacy and Local-Only Operation

VP GeoConvert is designed to operate fully locally and independently on the user's computer.

The application must not transmit project files, coordinates, elevations, geometry, telemetry, analytics, crash reports, or other user/project data to the developer or third parties.

The application contains no project-specific coordinates, elevations, control points, or site-specific georeferencing data. Spatial information used for processing is obtained only from files explicitly supplied by the user and parameters explicitly entered or selected by the user.

Definitions of coordinate reference systems (for example EPSG/CRS definitions) may be included as technical reference data. Such definitions are not project/site coordinates or user data.

Any future feature requiring network access must be treated as an explicit architectural and privacy-policy change and must not be introduced silently.
