"""Final matcher run: 2-segment RANSAC per PDF / per page, then inlier
refinement and a full CONTROL / CHECK split. Research tool.
"""

import math
from collections import defaultdict

import dxf_reader as D
import pdf_reader as P
import matcher as M
import transform_est as T

DXF = r'benchmark\TKA_Tunnelitie\TKA_Tunnelitie_Suunnitelmakartta_MAS!BG.dxf'
ART = r'research\results\archive\TKA_Tunnelitie'

FILES = [("3D-Win", "3dwin_probe.txt"),
         ("ProgeCAD Export", "proge_export_probe.txt"),
         ("ProgeCAD Print", "proge_print_probe.txt")]


def dxf_segments(ents):
    out = []
    for e in ents:
        if e.etype not in ("LINE", "LWPOLYLINE"):
            continue
        p = e.pts
        if len(p) < 2:
            continue
        for i in range(len(p) - 1):
            out.append((p[i], p[i + 1]))
        if e.closed and p[0] != p[-1]:
            out.append((p[-1], p[0]))
    return out


def pdf_segments(paths):
    out = []
    for p in paths:
        out.extend(P.segments_of(p))
    return out


def ransac_best(dx, ps, eps, tol=1.5, n_dxf=40, n_pdf=70, probe=40, seed=7):
    """Brute-force 2-segment RANSAC with early exit on a full probe hit."""
    grid = M.Grid(ps)
    dxs = sorted(dx, key=lambda s: -M.seg_len(*s))[:n_dxf]
    pfs = sorted(ps, key=lambda s: -M.seg_len(*s))[:n_pdf]
    best = (0, None)
    tried = 0
    for i in range(len(dxs)):
        for k in range(i + 1, len(dxs)):
            a, b = dxs[i]
            c, d = dxs[k]
            for j in range(len(pfs)):
                for m in range(j + 1, len(pfs)):
                    p, q = pfs[j]
                    r, t = pfs[m]
                    tried += 1
                    pairs = [(p[0], p[1], a[0], a[1]),
                             (q[0], q[1], b[0], b[1]),
                             (r[0], r[1], c[0], c[1]),
                             (t[0], t[1], d[0], d[1])]
                    f = M.fit_similarity(pairs, eps)
                    if f is None:
                        continue
                    s, th, tx, ty = f
                    if not (1e-5 < s < 1e4):
                        continue
                    Tt = (s, th, tx, ty, eps)
                    hits = 0
                    for ss in dxs[:probe]:
                        if M.match_one(Tt, ss[0], ss[1], grid, tol) is not None:
                            hits += 1
                    if hits > best[0]:
                        best = (hits, Tt)
                    if hits >= probe:
                        return {"T": Tt, "hits": hits, "tried": tried,
                                "early": True}
    return {"T": best[1], "hits": best[0], "tried": tried, "early": False}


def refine(Tt, dx, ps, tol=1.5):
    """Refit on all inliers; report CONTROL/CHECK residual statistics."""
    grid = M.Grid(ps)
    inliers = []
    for a, b in dx:
        m = M.match_one(Tt, a, b, grid, tol)
        if m is not None:
            inliers.append(((a, b), m[0], m[1]))
    if len(inliers) < 4:
        return None, inliers
    pairs = []
    for (a, b), (qa, qb), _r in inliers:
        pairs.append((a[0], a[1], qa[0], qa[1]))
        pairs.append((b[0], b[1], qb[0], qb[1]))
    # refit the similarity using matched DXF endpoints against matched PAGE endpoints
    f = M.fit_similarity(pairs, Tt[4])
    return f, inliers


def main():
    ents, hdr, blocks, _ = D.parse(DXF)
    dx = dxf_segments(ents)
    print("DXF straight segments: %d" % len(dx))

    for lab, fn in FILES:
        paths, pages = P.parse(ART + "\\" + fn)
        by_page = defaultdict(list)
        for p in paths:
            by_page[p.page].append(p)
        print()
        print("=" * 78)
        print("%s" % lab)
        print("=" * 78)
        for pg in sorted(by_page):
            ps = pdf_segments(by_page[pg])
            print()
            print("  --- page %d : %d PAGE segments ---" % (pg, len(ps)))
            results = []
            for eps in (+1, -1):
                r = ransac_best(dx, ps, eps)
                if r["T"] is None:
                    print("     eps=%+d : no hypothesis found" % eps)
                    continue
                f, inl = refine(r["T"], dx, ps)
                results.append((len(inl), eps, r, inl, f))
                s, th, tx, ty = r["T"][0], r["T"][1], r["T"][2], r["T"][3]
                lbl = "no-reflect " if eps > 0 else "REFLECT-v"
                print("     eps=%+d (%-11s) tried=%-8d inliers=%4d/%d  "
                      "s=%.9f th=%+9.5f deg"
                      % (eps, lbl, r["tried"], len(inl), len(dx), s,
                         math.degrees(th)))
            if results:
                results.sort(key=lambda z: -z[0])
                n, eps, r, inl, f = results[0]
                s, th, tx, ty = r["T"][0], r["T"][1], r["T"][2], r["T"][3]
                print("     => BEST eps=%+d  s=%.9f  th=%+.6f deg  "
                      "t=(%.4f, %.4f)  inliers=%d/%d (%.1f%%)"
                      % (eps, s, math.degrees(th), tx, ty, n, len(dx),
                         100.0 * n / len(dx)))
                rs = sorted(x[2] for x in inl)
                if rs:
                    print("     => endpoint residual (PAGE pt): median=%.5f "
                          "p95=%.5f max=%.5f"
                          % (rs[len(rs) // 2],
                             rs[int(0.95 * (len(rs) - 1))], rs[-1]))


if __name__ == "__main__":
    main()