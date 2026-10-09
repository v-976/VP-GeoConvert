"""Scale-free structural signatures of DXF (SURVEY) and PDF (PAGE) geometry.

Research tool. Purpose: find rotation / scale / reflection without assuming any
prior correspondence, then hand a starting estimate to RANSAC.

PAGE space   : u = horizontal, v = vertical
SURVEY space : X = Northing,    Y = Easting
"""

import math
from collections import Counter, defaultdict

import dxf_reader as D
import pdf_reader as P

DXF = r'benchmark\TKA_Tunnelitie\TKA_Tunnelitie_Suunnitelmakartta_MAS!BG.dxf'
ART = r'research\results\archive\TKA_Tunnelitie'


def dxf_segments(ents, kinds=("LINE", "LWPOLYLINE")):
    """SURVEY-space straight segments."""
    out = []
    for e in ents:
        if e.etype not in kinds:
            continue
        pts = e.pts
        if len(pts) < 2:
            continue
        for i in range(len(pts) - 1):
            out.append((pts[i], pts[i + 1], e.etype, e.layer))
        if e.closed and pts[0] != pts[-1]:
            out.append((pts[-1], pts[0], e.etype, e.layer))
    return out


def dxf_straight_segments(ents):
    return [(a, b) for a, b, _t, _l in dxf_segments(ents)]


def pdf_segments(paths):
    """PAGE-space straight segments (LINE segments only, matrix already applied)."""
    out = []
    for p in paths:
        out.extend(P.segments_of(p))
    return out


def orient_mod180(a, b):
    """Orientation of a SURVEY segment (X,Y) in degrees, mod 180."""
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0


def orient_mod90(a, b):
    return orient_mod180(a, b) % 90.0


def hist(segs, bucket=1.0, key=orient_mod180, weight=None):
    h = defaultdict(float)
    for i, (a, b) in enumerate(segs):
        ang = key(a, b)
        w = 1.0 if weight is None else weight[i]
        h[int(ang / bucket) * bucket] += w
    return h


def top_orientation(segs, bucket=1.0, key=orient_mod180, min_len=0.0):
    h = defaultdict(float)
    for i, (a, b) in enumerate(segs):
        if math.hypot(b[0] - a[0], b[1] - a[1]) < min_len:
            continue
        ang = key(a, b)
        h[int(ang / bucket) * bucket] += 1.0
    return h


def length_profile(segs, reverse=True):
    L = sorted((math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in segs),
               reverse=reverse)
    return L


def main():
    ents, hdr, blocks, _ = D.parse(DXF)
    dsegs = dxf_straight_segments(ents)
    print("=== DXF (SURVEY space) ===")
    print("  straight segments:", len(dsegs))
    dl = length_profile(dsegs)
    print("  length profile top15:", ["%.4f" % x for x in dl[:15]])
    print("  total length: %.4f m" % sum(dl))

    print()
    print("  orientation histogram mod 180 (bucket 5 deg), length-weighted:")
    h = defaultdict(float)
    for a, b in dsegs:
        ang = orient_mod180(a, b)
        h[int(ang / 5.0) * 5] += math.hypot(b[0] - a[0], b[1] - a[1])
    tot = sum(h.values())
    for k in sorted(h, key=lambda x: -h[x])[:10]:
        print("     %6.1f..%6.1f deg : %9.2f m  (%5.1f%%)"
              % (k, k + 5, h[k], 100.0 * h[k] / tot))

    print()
    print("  orientation histogram mod 90 (bucket 5 deg), length-weighted:")
    h9 = defaultdict(float)
    for a, b in dsegs:
        ang = orient_mod90(a, b)
        h9[int(ang / 5.0) * 5] += math.hypot(b[0] - a[0], b[1] - a[1])
    for k in sorted(h9, key=lambda x: -h9[x])[:6]:
        print("     %6.1f..%6.1f deg : %9.2f m" % (k, k + 5, h9[k]))

    for fn, lab in (("3dwin_probe.txt", "3D-Win"),
                    ("proge_export_probe.txt", "ProgeCAD Export"),
                    ("proge_print_probe.txt", "ProgeCAD Print")):
        paths, pages = P.parse(ART + "\\" + fn)
        psegs = pdf_segments(paths)
        print()
        print("=== %s (PAGE space) ===" % lab)
        print("  straight LINE segments:", len(psegs))
        pl = length_profile(psegs)
        print("  length profile top15:", ["%.4f" % x for x in pl[:15]])
        print("  total length: %.4f pt" % sum(pl))
        hp = defaultdict(float)
        for a, b in psegs:
            ang = orient_mod180(a, b)
            hp[int(ang / 5.0) * 5] += math.hypot(b[0] - a[0], b[1] - a[1])
        totp = sum(hp.values())
        for k in sorted(hp, key=lambda x: -hp[x])[:10]:
            print("     %6.1f..%6.1f deg : %9.2f pt  (%5.1f%%)"
                  % (k, k + 5, hp[k], 100.0 * hp[k] / totp))


if __name__ == "__main__":
    main()