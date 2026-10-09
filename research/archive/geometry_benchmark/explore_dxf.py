"""Explore DXF geometry to find matchable features. Research tool."""
import math
import dxf_reader as D

DXF = r'benchmark\TKA_Tunnelitie\TKA_Tunnelitie_Suunnitelmakartta_MAS!BG.dxf'


def seg_len(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def polyline_segments(e):
    """SURVEY-space segments from a polyline entity, honouring closure."""
    out = []
    pts = e.pts
    if len(pts) < 2:
        return out
    for i in range(len(pts) - 1):
        out.append((pts[i], pts[i + 1]))
    if e.closed and pts[0] != pts[-1]:
        out.append((pts[-1], pts[0]))
    return out


def main():
    ents, hdr, blocks, _ = D.parse(DXF)
    print("=== header ===")
    for k in ('$ACADVER', '$DWGCODEPAGE', '$LASTSAVEDBY'):
        print("  %-16s %s" % (k, hdr.get(k)))
    print("  EXTMIN X=%s Y=%s" % (hdr.get('$EXTMIN'), hdr.get('$EXTMIN')))
    print("  EXTMIN X=%s Y=%s" % (hdr.get('$EXTMINX'), hdr.get('$EXTMINY')))

    print()
    print("=== entity counts ===")
    for k, v in sorted(D.entity_counts(ents).items(), key=lambda kv: -kv[1]):
        print("  %-14s %d" % (k, v))

    # ---- straight segments from LINE + LWPOLYLINE -----------------------
    segs = []
    for e in ents:
        if e.etype == "LINE":
            segs.extend(polyline_segments(e))
        elif e.etype == "LWPOLYLINE":
            segs.extend(polyline_segments(e))
    print()
    print("=== straight segments (LINE + LWPOLYLINE) ===")
    print("  count:", len(segs))
    if segs:
        lens = sorted((seg_len(a, b) for a, b in segs), reverse=True)
        print("  total length (m): %.4f" % sum(lens))
        print("  max length (m)   : %.4f" % lens[0])
        print("  min length (m)   : %.6f" % lens[-1])
        print("  10 longest (m):", ["%.4f" % x for x in lens[:10]])
        print("  median  (m): %.4f" % lens[len(lens) // 2])

    # ---- circles / arcs -------------------------------------------------
    circs = [e for e in ents if e.etype == "CIRCLE"]
    arcs = [e for e in ents if e.etype == "ARC"]
    print()
    print("=== circles ===")
    print("  count:", len(circs))
    radii = sorted(e.radius for e in circs)
    print("  radii (m):", ["%.5f" % r for r in radii])
    print("  distinct radii:", len(set(round(r, 6) for r in radii)))
    print("=== arcs ===")
    print("  count:", len(arcs))
    for e in arcs:
        print("   r=%.5f  a0=%.3f a1=%.3f  centre=(%.3f, %.3f)"
              % (e.radius, e.a0, e.a1, e.center[0], e.center[1]))

    # ---- candidate rectangular frames ----------------------------------
    print()
    print("=== candidate frames (closed LWPOLYLINE, >=4 pts, near-rectangular) ===")
    cands = []
    for e in ents:
        if e.etype != "LWPOLYLINE" or not e.closed or len(e.pts) < 4:
            continue
        pts = e.pts
        n = len(pts)
        edges = [(pts[i], pts[(i + 1) % n]) for i in range(n)]
        L = [seg_len(a, b) for a, b in edges]
        longest = max(L)
        if longest < 10.0:
            continue
        # rectangle test: exactly 4 vertices and 2 distinct edge lengths,
        # each appearing twice, and adjacent edges near-perpendicular
        perp = []
        for i in range(n):
            (ax, ay), (bx, by) = edges[i]
            (cx, cy), (dx, dy) = edges[(i + 1) % n]
            v1 = (bx - ax, by - ay)
            v2 = (dx - cx, dy - cy)
            n1 = math.hypot(*v1); n2 = math.hypot(*v2)
            if n1 == 0 or n2 == 0:
                continue
            cosang = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
            perp.append(abs(math.degrees(math.acos(max(-1, min(1, cosang)))) - 90.0))
        if perp and max(perp) < 0.01:
            cands.append((e, longest, max(perp)))
    print("  rectangular candidates:", len(cands))
    for e, L, dev in sorted(cands, key=lambda t: -t[1])[:8]:
        pts = e.pts
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        print("   layer=%-22s n=%d  extentX=%.4f extentY=%.4f  perimeter~%.3f  maxdev=%.2e"
              % (e.layer, len(pts), max(xs) - min(xs), max(ys) - min(ys), 2 * L, dev))
        print("      corners:", ["(%.3f, %.3f)" % p for p in pts[:6]])

    # ---- INSERT --------------------------------------------------------
    print()
    print("=== INSERT ===")
    for e in ents:
        if e.etype == "INSERT":
            print("   name=%s at SURVEY (%.3f, %.3f) sx=%s sy=%s rot=%s layer=%s"
                  % (e.name, e.center[0], e.center[1], e.sx, e.sy, e.rot, e.layer))
    print("   blocks found:", {k: len(v) for k, v in blocks.items()})

    # ---- HATCH ---------------------------------------------------------
    hatches = [e for e in ents if e.etype == "HATCH"]
    print()
    print("=== HATCH ===")
    print("  count:", len(hatches))
    print("  sample raw keys:", sorted(hatches[0].raw.keys()) if hatches else None)
    npts = sum(len(h.raw.get("x", [])) for h in hatches)
    print("  total group-10 points across HATCH:", npts)


if __name__ == "__main__":
    main()