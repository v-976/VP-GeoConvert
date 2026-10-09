"""Canonical research benchmark harness for VP GeoConvert.

RESEARCH TOOLING ONLY. This package is NOT the VP GeoConvert production
matching engine and must never be presented as one. It measures evidence and
records it. It deliberately makes no product decisions:

  * no production thresholds
  * no confidence score
  * no AUTO / MANUAL / UNSAFE policy
  * no "X % is enough" or "Y mm is normal" assumption
  * no generator-specific behaviour

Coordinate domains are kept strictly separate throughout:

    PDF raw/object   p, q     read only to interpret an object matrix
    PDF PAGE         u, v     after each object's own transform
    DXF CAD          cad_x, cad_y   raw DXF group codes
    VP SURVEY        X = Northing, Y = Easting   NOT USED here

The CAD -> SURVEY mapping is not established by this harness and is never
assumed. Strictly 2D: Z / H are never read.
"""

HARNESS_VERSION = "1.0.1"

__all__ = ["HARNESS_VERSION"]
