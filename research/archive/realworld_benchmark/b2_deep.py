"""Set B2 — deep validation for candidates that survived coarse matching.

Research tool. Reads b2_matrix.json and, for every non-NO_EVIDENCE candidate,
recomputes with the stored (already selected, CONTROL-only) transform:

  * entity-level CONTROL and CHECK consensus on the FULL pools
  * signed axis residuals d_cad_x, d_cad_y (bias, RMS, p95)
  * radial residual RMS / median / p95 / max
  * threshold pass rates in the unit the DXF declares ($INSUNITS)
  * spatial distribution and layer concentration
  * overlap comparison between two DXF candidates for the same PDF

Coordinate domains
------------------
    PAGE  u, v        CAD  cad_x, cad_y        SURVEY X, Y  NOT USED
Strictly 2D.
"""

import json
import math
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import b2_core as C                                              # noqa: E402
import b2_matrix as B                                             # noqa: E402

RES = B.RES
UNIT = {1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m", 14: "dm"}


def load(tag):
    return json.load(open(os.path.join(RES, tag + "_probe.txt".replace(
        "_probe.txt", "_probe.txt")), "r")) if False else None


def axis_residuals(T, pool, psegs, grid, tol=C.TOL_PT):
    dx = []
    dy = []
    ents = 0
    feats = 0
    for e in pool:
        tmp_x = []
        tmp_y = []
        ok = True
        for a, b in e.segs:
            r = C.signed_segment_residuals(T, a, b, psegs, grid, tol)
            if r is None:
                ok = False
                break
            for ddx, ddy in r:
                tmp_x.append(ddx)
                tmp_y.append(ddy)
        if ok:
            ents += 1
            feats += len(e.segs)
            dx.extend(tmp_x)
            dy.extend(tmp_y)
    return ents, feats, dx, dy


def deep_for(res, out):
    pdf_tag = res["pdf"]
    dxf_tag = res["dxf"]
    T = (res["transform"]["scale"], math.radians(res["transform"]["rotation_deg"]),
         res["transform"]["tx"], res["transform"]["ty"], res["transform"]["eps"])
    out("=" * 78)
    out("DEEP VALIDATION  PDF=%s  DXF=%s" % (pdf_tag, dxf_tag))
    out("=" * 78)
    out("  scale %s = %.12f CAD/pt" % ("(m/pt)" if res["unit"] == "metres"
                                       else "(CAD/pt)", T[0]))
    out("  rotation = %+.9f deg   reflection eps = %+d   axis swap: rejected"
        % (T[1] * 180.0 / math.pi, T[4]))
    out("  translation cad_x=%.4f cad_y=%.4f" % (T[2], T[3]))

    dxf_path = [os.path.join(B.BENCH, f) for f in os.listdir(B.BENCH)
                if f.lower().startswith(dxf_tag.lower())][0]
    ents, uc = B.load_dxf(dxf_path)
    dxf_ents = [e for e in ents if e.segs]
    ctrl, chk = C.spatial_split_balanced(dxf_ents)
    psegs = B.load_pdf(pdf_tag)
    grid = C.SegGrid(psegs)
    unit = UNIT.get(uc, "CAD units")

    # residual unit: convert CAD -> report unit
    # CAD units per metre = 1 when metres, 1000 when millimetres
    per_m = 1000.0 if uc == 4 else (1.0 if uc == 6 else None)
    mult = 1.0
    if per_m:
        mult = 1000.0 / per_m       # CAD -> millimetres
    ulab = "mm" if per_m else "raw CAD units"

    result = {"pdf": pdf_tag, "dxf": dxf_tag,
              "verdict": res["verdict"], "unit_declared": res["unit"],
              "residual_unit": ulab, "transform": res["transform"]}

    for label, pool in (("CONTROL (fitted)", ctrl), ("CHECK (independent)", chk)):
        rows = C.entity_rows(T, pool, psegs, grid, 1.0)
        rad = [v for r in rows for v in r["rad"]]
        ents_m, feats, dx, dy = axis_residuals(T, pool, psegs, grid)
        st = C.stats(rad)
        out("  -- %s --" % label)
        out("     entities matched %4d / %4d  (%.1f %%)   unmatched %4d"
            % (len(rows), len(pool), 100.0 * len(rows) / len(pool),
               len(pool) - len(rows)))
        if st:
            out("     radial  n=%d RMS=%.4f median=%.4f p95=%.4f max=%.4f  [%s]"
                % (st["n"], st["rms"] * mult, st["median"] * mult,
                   st["p95"] * mult, st["max"] * mult, ulab))
        if dx:
            out("     d_cad_x  bias=%+.4f RMS=%.4f p95|e|=%.4f  [%s]"
                % ((sum(dx) / len(dx)) * mult,
                   math.sqrt(sum(v * v for v in dx) / len(dx)) * mult,
                   sorted(abs(v) for v in dx)[int(0.95 * (len(dx) - 1))] * mult,
                   ulab))
            out("     d_cad_y  bias=%+.4f RMS=%.4f p95|e|=%.4f  [%s]"
                % ((sum(dy) / len(dy)) * mult,
                   math.sqrt(sum(v * v for v in dy) / len(dy)) * mult,
                   sorted(abs(v) for v in dy)[int(0.95 * (len(dy) - 1))] * mult,
                   ulab))
            if label.startswith("CHECK"):
                out("     threshold pass rates:")
                if per_m:
                    for t_mm in (1, 5, 10, 20, 50, 100, 250, 500, 1000):
                        thr = t_mm / mult
                        ok = sum(1 for v in rad if v <= thr)
                        out("        <= %6d mm : %6d / %6d (%5.1f %%)"
                            % (t_mm, ok, len(rad),
                               100.0 * ok / max(1, len(rad))))
                else:
                    out("        units NOT proven -> no mm thresholds shown")
                result["check"] = {
                    "entities": len(rows), "offered": len(pool),
                    "features": st["n"] if st else 0,
                    "radial": st,
                    "d_cad_x_bias": (sum(dx) / len(dx)) * mult,
                    "d_cad_x_rms": math.sqrt(
                        sum(v * v for v in dx) / len(dx)) * mult,
                    "d_cad_x_p95": sorted(
                        abs(v) for v in dx)[int(0.95 * (len(dx) - 1))] * mult,
                    "d_cad_y_bias": (sum(dy) / len(dy)) * mult,
                    "d_cad_y_rms": math.sqrt(
                        sum(v * v for v in dy) / len(dy)) * mult,
                    "d_cad_y_p95": sorted(
                        abs(v) for v in dy)[int(0.95 * (len(dy) - 1))] * mult,
                }

    # spatial + layer breakdown over the whole DXF entity set
    rows_all = C.entity_rows(T, dxf_ents, psegs, grid, 1.0)
    matched = {id(r["entity"]) for r in rows_all}
    tot = Counter(e.etype for e in dxf_ents)
    mat = Counter(e.etype for e in dxf_ents if id(e) in matched)
    out("  -- match rate by entity type --")
    per_type = {}
    for t in sorted(tot, key=lambda k: -tot[k]):
        out("     %-12s %4d / %4d  (%5.1f %%)" % (t, mat[t], tot[t],
                                                  100.0 * mat[t] / tot[t]))
        per_type[t] = [mat[t], tot[t]]
    lt = Counter(e.layer for e in dxf_ents)
    lm = Counter(e.layer for e in dxf_ents if id(e) in matched)
    rowsl = sorted(((100.0 * lm[k] / lt[k], k, lm[k], lt[k]) for k in lt))
    out("  -- layers, worst 8 --")
    for r_, k, m_, t_ in rowsl[:8]:
        out("     %-32s %4d / %4d  %6.1f %%" % (k, m_, t_, r_))
    out("  -- layers, best 5 --")
    for r_, k, m_, t_ in rowsl[-5:]:
        out("     %-32s %4d / %4d  %6.1f %%" % (k, m_, t_, r_))
    occ_all = C.cell_map(dxf_ents)
    occ_un = C.cell_map([e for e in dxf_ents if id(e) not in matched])
    out("  -- unmatched per 8x8 cell (unmatched/total) --")
    out("     " + "  ".join("%d/%d" % (occ_un.get(k, 0), v)
                            for k, v in sorted(occ_all.items())))
    result["by_type"] = per_type
    result["cells"] = {"filled": sum(1 for k in occ_all
                                     if occ_all[k] - occ_un.get(k, 0) > 0),
                       "total": len(occ_all)}
    result["layers_matched"] = len(lm)
    result["layers_total"] = len(lt)
    result["top_layer_share"] = (lm.most_common(1)[0][1] / len(rows_all)
                                  if rows_all else 0.0)
    result["entities_total"] = len(dxf_ents)
    result["entities_matched"] = len(rows_all)
    return result


def main():
    res = json.load(open(os.path.join(RES, "b2_matrix.json"), encoding="utf-8"))
    outp = os.path.join(RES, "b2_deep.txt")
    deep = []
    with open(outp, "w", encoding="utf-8") as fh:
        def out(m):
            print(m)
            fh.write(m + "\n")
        out("Set B2 deep validation, ALL 12 combinations.")
        for r in res:
            if True:
                deep.append(deep_for(r, out))
                fh.flush()
                json.dump(deep, open(os.path.join(RES, "b2_deep.json"), "w",
                                     encoding="utf-8"), indent=1, default=str)
        # per-PDF ranking table
        out("")
        out("=" * 78)
        out("PER-PDF REFERENCE RANKING (evidence dimensions kept separate)")
        out("=" * 78)
        by_pdf = defaultdict(list)
        for r in res:
            by_pdf[r["pdf"]].append(r)
        for pdf_tag, rows in sorted(by_pdf.items()):
            out("")
            out("PDF %s" % pdf_tag)
            hdr = ("  %-22s %-18s %6s %6s %6s %6s %6s %6s %8s"
                   % ("DXF", "verdict", "consC", "consK", "fracC",
                      "cells", "types", "layers", "vs2nd"))
            out(hdr)
            ranked = sorted(rows, key=lambda r: -(r["evidence"]["A_consensus_total"]
                                                 if r["verdict"] != "NO_EVIDENCE"
                                                 else 0))
            for i, r in enumerate(ranked):
                e = r["evidence"]
                d = r["defence"]
                vs = ("%.2fx" % d["margin_second_transform"]
                      if d["second_best"] else "n/a")
                out("  %-22s %-18s %6d %6d %6.3f %4d/%-3d %3d/%-3d %4d/%-3d %8s"
                    % (r["dxf"][:22], r["verdict"],
                       e["A_control"], e["A_check"], e["B_control_fraction"],
                       e["C_cells_filled"], e["C_cells_total"],
                       e["D_types_matched"], e["D_types_total"],
                       d["layers_matched"], d["layers_total"], vs))
    print("\nwrote %s" % outp)


if __name__ == "__main__":
    main()
