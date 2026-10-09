"""Circle detection in PAGE space + scale-free radius fingerprint matching.

Research tool.

Why circles: the DXF ground truth contains 32 CIRCLE entities with only 6
distinct radii. That is a strong scale- and rotation-invariant fingerprint.
If the PDF contains the same circles, the radius set itself reveals the scale
without any assumed correspondence.

PAGE space   : u = horizontal, v = vertical
SURVEY space : X = Northing,    Y = Easting

Purely 2D.
"""

import math
from collections import defaultdict

import pdf_reader as P

ART = r'research\results\archive\TKA_Tunnelitie'
DXF = r'benchmark\TKA_Tunnelitie\TKA_Tunnelitie_Suunnitelmakartta_MAS!BG.dxf'


# ---------------------------------------------------------------- circle fit
def fit_circle_kasa(pts):
    """Algebraic (Kasa) circle fit. Returns (cu, cv, r, rms) or None."""
    n = len(pts)
    if n < 3:
        return None
    Sx = Sy = Sxx = Syy = Sxy = Sxz = Syz = Sz = 0.0
    for x, y in pts:
        z = x * x + y * y
        Sx += x; Sy += y
        Sxx += x * x; Syy += y * y; Sxy += x * y
        Sxz += x * z; Syz += y * z; Sz += z
    # normal equations for [cu, cv, k]
    A = [[2 * Sxx, 2 * Sxy, Sx],
         [2 * Sxy, 2 * Syy, Sy],
         [Sx,       Sy,       float(n)]]
    B = [Sxz, Syz, Sz]
    # 3x3 solve by Gaussian elimination
    M = [A[i][:] + [B[i]] for i in range(3)]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-15:
            return None
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for r in range(3):
            if r == col:
                continue
            f = M[r][col] / pv
            for c in range(col, 4):
                M[r][c] -= f * M[col][c]
    cu = M[0][3] / M[0][0]
    cv = M[1][3] / M[1][1]
    k = M[2][3] / M[2][2]
    r2 = k + cu * cu + cv * cv
    if r2 <= 0:
        return None
    r = math.sqrt(r2)
    acc = 0.0
    for x, y in pts:
        acc += (math.hypot(x - cu, y - cv) - r) ** 2
    return (cu, cv, r, math.sqrt(acc / n))


def path_points(p):
    return [(s[1], s[2]) for s in p.segs]


def detect_circles(paths, rel_tol=0.010, min_pts=6, max_pts=4000,
                   min_seg_len=0.0):
    """Detect circle-like closed paths. Returns list of dicts."""
    out = []
    for p in paths:
        pts = path_points(p)
        if len(pts) < min_pts or len(pts) > max_pts:
            continue
        closed = math.hypot(pts[-1][0] - pts[0][0],
                            pts[-1][1] - pts[0][1])
        fit = fit_circle_kasa(pts)
        if fit is None:
            continue
        cu, cv, r, rms = fit
        if r <= 0:
            continue
        if closed > 0.25 * r:
            continue                      # not a closed loop
        if rms > rel_tol * r:
            continue                      # not circular enough
        if min_seg_len > 0:
            ok = True
            for i in range(len(pts)):
                a = pts[i]
                b = pts[(i + 1) % len(pts)]
                if math.hypot(b[0] - a[0], b[1] - a[1]) < min_seg_len:
                    ok = False
                    break
            if not ok:
                continue
        out.append({"page": p.page, "cu": cu, "cv": cv, "r": r,
                    "rms": rms, "npts": len(pts),
                    "closed": closed,
                    "nbound": closed / (2 * math.pi * r),
                    "matrix": p.matrix})
    return out


def fingerprint(circles, label):
    rs = sorted(c["r"] for c in circles)
    print("  %s: %d circles" % (label, len(rs)))
    if not rs:
        return
    med = rs[len(rs) // 2]
    print("     radii: " + ", ".join("%.4f" % r for r in rs))
    print("     radii / median: " +
          ", ".join("%.5f" % (r / med) for r in rs))
    return rs


def main():
    import dxf_reader as D
    ents, hdr, blocks, _ = D.parse(DXF)
    dcirc = [e for e in ents if e.etype == "CIRCLE" and e.radius]
    print("=== DXF ground truth (SURVEY X,Y) ===")
    print("  CIRCLE entities: %d" % len(dcirc))
    dr = sorted(e.radius for e in dcirc)
    dmed = dr[len(dr) // 2]
    print("  radii (m): " + ", ".join("%.5f" % r for r in dr))
    print("  radii / median: " + ", ".join("%.5f" % (r / dmed) for r in dr))
    from collections import Counter
    print("  multiplicity per distinct radius: %s"
          % sorted(Counter(round(r, 6) for r in dr).values(), reverse=True))

    for lab, fn in (("3D-Win", "3dwin_probe.txt"),
                    ("ProgeCAD Export", "proge_export_probe.txt"),
                    ("ProgeCAD Print", "proge_print_probe.txt")):
        print()
        print("=== %s (PAGE u,v) ===" % lab)
        paths, pages = P.parse(ART + "\\" + fn)
        for pg in sorted(set(p.page for p in paths)):
            sub = [p for p in paths if p.page == pg]
            circs = detect_circles(sub)
            by_page = [c for c in circs if c["page"] == pg]
            print("  page %d: %d paths -> %d circles" % (pg, len(sub), len(by_page)))
            rs = sorted(c["r"] for c in by_page)
            if rs:
                med = rs[len(rs) // 2]
                print("     radii: " + ", ".join("%.4f" % r for r in rs))
                print("     radii / median: " +
                      ", ".join("%.5f" % (r / med) for r in rs))
                print("     multiplicity: %s"
                      % sorted(Counter(round(r, 4) for r in rs).values(),
                               reverse=True))
                nb = sorted(set(round(c["nbound"], 2) for c in by_page))
                print("     points-per-circle: %s" % nb)
                # scale from radius match
                ratios = [rp / rd for rp, rd in zip(rs, dr)] if len(rs) == len(dr) else []
                if ratios:
                    print("     scale ratios (pdf/dxf, paired by rank): " +
                          ", ".join("%.6f" % x for x in ratios[:12]))


if __name__ == "__main__":
    main()