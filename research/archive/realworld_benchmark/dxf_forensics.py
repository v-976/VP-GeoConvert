"""DXF forensic inventory for the Set B1 real-world benchmark.

Research tool. Read-only. Parses group-code/value pairs directly; no DXF
library, no conversion, no modification of the source file.

Coordinate domain produced here:
    DXF raw CAD coordinates:  cad_x, cad_y
No claim is made that cad_x == SURVEY X or cad_y == SURVEY Y.

Strictly 2D: group codes 30/31/32 (Z) are parsed only to prove they are
present, never used for geometry.
"""

import math
import re
import sys
from collections import OrderedDict, Counter

Z_CODES = (30, 31, 32, 33, 34, 35, 36, 37, 38, 39)


def pairs(path):
    with open(path, "r", encoding="latin-1") as fh:
        while True:
            cl = fh.readline()
            if cl == "":
                return
            vl = fh.readline()
            if vl == "":
                return
            c = cl.strip()
            if not re.match(r"^-?\d+$", c):
                continue
            yield int(c), vl.rstrip("\n").rstrip("\r")


def f(v):
    try:
        return float(v)
    except ValueError:
        return None


def main(path):
    header = {}
    sections = []
    ents = []
    cur = None
    section = None
    cur_block = None
    blocks = OrderedDict()
    z_present = 0
    tables_layers = set()
    in_tables = False
    last_var = None
    var_code = None

    for code, value in pairs(path):
        if code == 0:
            kw = value.strip()
            if kw == "SECTION":
                section = None
                cur = None
                continue
            if kw == "ENDSEC":
                if cur is not None:
                    _flush(cur, section, ents, blocks, cur_block)
                    cur = None
                section = None
                cur_block = None
                in_tables = False
                continue
            if kw == "EOF":
                if cur is not None:
                    _flush(cur, section, ents, blocks, cur_block)
                    cur = None
                break
            if kw == "BLOCK":
                if cur is not None:
                    _flush(cur, section, ents, blocks, cur_block)
                cur = None
                cur_block = "?"
                continue
            if kw == "ENDBLK":
                if cur is not None:
                    _flush(cur, section, ents, blocks, cur_block)
                cur = None
                cur_block = None
                continue
            if kw in ("SEQEND",):
                cur = None
                continue
            if cur is not None:
                _flush(cur, section, ents, blocks, cur_block)
            cur = {"t": kw, "layer": None, "codes": []}
            continue

        if code == 2 and cur is None and section is None:
            section = value.strip()
            sections.append(section)
            continue

        if section == "HEADER":
            if code == 9:
                last_var = value.strip()
                var_code = None
            elif last_var is not None:
                header.setdefault(last_var, []).append((code, value.strip()))
            continue

        if cur is None:
            if section == "BLOCKS" and code == 2 and cur_block in (None, "?"):
                cur_block = value.strip()
            continue

        cur["codes"].append((code, value.strip()))
        if code == 8:
            cur["layer"] = value.strip()
        if code in Z_CODES:
            z_present += 1
        if section == "TABLES" and code == 2:
            tables_layers.add(value.strip())

    print("=== DXF forensic inventory (read-only) ===")
    print("file: %s" % path)
    print()
    print("sections present : %s" % ", ".join(sections))
    print()
    print("-- header --")
    for k in ("$ACADVER", "$ACADMAINTVER", "$DWGCODEPAGE", "$LASTSAVEDBY",
              "$INSUNITS", "$MEASUREMENT", "$LUNITS", "$LIMMIN", "$LIMMAX",
              "$EXTMIN", "$EXTMAX", "$PDMODE", "$PDSIZE"):
        if k in header:
            vals = [v for _c, v in header[k]]
            print("   %-14s %s" % (k, " | ".join(vals[:8])))
    ins = header.get("$INSUNITS")
    unit_name = None
    if ins:
        umap = {1: "inches", 2: "feet", 4: "millimetres", 5: "centimetres",
                6: "metres", 14: "decimetres", 21: "meters (alt)"}
        try:
            unit_name = umap.get(int(float(ins[0][1])), "code %s" % ins[0][1])
        except ValueError:
            unit_name = ins[0][1]
    print("   -> INSUNITS : %s" % (unit_name if unit_name else
                                    "ABSENT -> units NOT declared in file"))

    print()
    print("-- ENTITIES section --")
    counts = Counter(e["t"] for e in ents)
    for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print("   %-14s %d" % (k, v))
    print("   %-14s %d" % ("TOTAL", len(ents)))

    print()
    print("-- BLOCKS section --")
    print("   block definitions: %d" % len(blocks))
    for k, v in list(blocks.items())[:12]:
        print("      %-30s %d entities" % (k, len(v)))

    # ---- layers ---------------------------------------------------------
    layers = Counter(e["layer"] for e in ents if e["layer"])
    print()
    print("-- layers referenced by ENTITIES --")
    print("   distinct layers: %d" % len(layers))
    for k, v in layers.most_common(20):
        print("      %-34s %d" % (k, v))

    # ---- geometry extents (CAD units, cad_x / cad_y) --------------------
    xs = []
    ys = []
    have = Counter()
    for e in ents:
        xs_v = []
        ys_v = []
        buf_x = []
        buf_y = []
        for code, val in e["codes"]:
            if code == 10:
                buf_x.append(f(val))
            elif code == 20:
                buf_y.append(f(val))
        n = min(len(buf_x), len(buf_y))
        for i in range(n):
            if buf_x[i] is not None and buf_y[i] is not None:
                xs.append(buf_x[i])
                ys.append(buf_y[i])
        if e["t"] in ("LINE", "LWPOLYLINE", "CIRCLE", "ARC", "POINT",
                      "TEXT", "MTEXT", "INSERT", "SOLID", "3DFACE",
                      "POLYLINE", "SPLINE", "ELLIPSE", "HATCH", "LEADER",
                      "MULTILEADER", "DIMENSION"):
            have[e["t"]] += 1
    print()
    print("-- modelspace extents from parsed geometry (CAD units) --")
    if xs:
        print("   cad_x  min = %.6f   max = %.6f   span = %.6f"
              % (min(xs), max(xs), max(xs) - min(xs)))
        print("   cad_y  min = %.6f   max = %.6f   span = %.6f"
              % (min(ys), max(ys), max(ys) - min(ys)))
    print()
    print("   entity types carrying geometry: %s" % dict(have))
    print("   group codes in Z family seen: %d (ignored, strictly 2D)"
          % z_present)

    print()
    print("-- presence checks required by the benchmark --")
    for t in ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "HATCH",
              "INSERT", "TEXT", "MTEXT", "SPLINE", "ELLIPSE", "POINT",
              "SOLID", "3DFACE", "LEADER", "MULTILEADER", "DIMENSION"):
        print("   %-12s %d" % (t, counts.get(t, 0)))


def _flush(cur, section, ents, blocks, cur_block):
    if section == "ENTITIES":
        ents.append(cur)
    elif section == "BLOCKS" and cur_block and cur_block != "?":
        blocks.setdefault(cur_block, []).append(cur)
    elif section is None:
        ents.append(cur)


if __name__ == "__main__":
    main(sys.argv[1])