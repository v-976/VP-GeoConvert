"""Set B2 — unbiased selectivity recalibration (PHASE 7).

Research tool.

The first matrix run calibrated selectivity on `ctrl[:250]`. The balanced
spatial split returns entities ordered BY CELL, so that prefix is the top
quarter of the sheet, not a representative sample: for one candidate it read 7
while the full pool read 90. Selectivity must be measured on a spatially
SPREAD sample, otherwise the calibration answers a different question from the
one reported consensus answers.

This script recomputes, for every one of the 12 combinations and using the
already selected (CONTROL-only) transform:

  * consensus on a spread CONTROL subsample
  * consensus of 40 random wrong transforms on the SAME subsample
  * consensus under systematic translation / rotation / scale / reflection /
    axis-swap perturbations on the SAME subsample

No new hypothesis is generated and no transform is refitted here.
"""

import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import b2_core as C                                              # noqa: E402
import b2_matrix as B                                             # noqa: E402

RES = B.RES
SUBSET = 250
N_RANDOM = 40


def cons(T, pool, psegs, grid):
    return sum(1 for e in pool
               if C.entity_matches(T, e, psegs, grid) is not None)


def main():
    res = json.load(open(os.path.join(RES, "b2_matrix.json"), encoding="utf-8"))
    outp = os.path.join(RES, "b2_selectivity.txt")
    lines = []

    def out(m):
        print(m)
        lines.append(m)

    out("Set B2 selectivity recalibration on SPREAD CONTROL subsamples "
        "(n=%d each)" % SUBSET)
    out("Transforms are the ones already selected from CONTROL only. "
        "No hypothesis generation, no refitting here.")
    table = {}
    for r in res:
        pdf_tag = r["pdf"]
        dxf_tag = r["dxf"]
        dxf_path = [os.path.join(B.BENCH, f) for f in os.listdir(B.BENCH)
                    if f.lower().startswith(dxf_tag.lower())][0]
        ents, uc = B.load_dxf(dxf_path)
        dxf_ents = [e for e in ents if e.segs]
        ctrl, chk = C.spatial_split_balanced(dxf_ents)
        sub = C.spread_sample(ctrl, SUBSET)
        psegs = B.load_pdf(pdf_tag)
        grid = C.SegGrid(psegs)
        t = r["transform"]
        T = (t["scale"], math.radians(t["rotation_deg"]), t["tx"], t["ty"],
             t["eps"])
        s, th, tx, ty, eps = T
        n = cons(T, sub, psegs, grid)
        span = max(1.0, abs(tx) * 5e-5)
        rnd = random.Random(20261007)
        rv = []
        for _ in range(N_RANDOM):
            rv.append(cons((s * math.exp(rnd.uniform(-0.6, 0.6)),
                            th + rnd.uniform(-math.pi, math.pi),
                            tx + rnd.uniform(-3 * span, 3 * span),
                            ty + rnd.uniform(-3 * span, 3 * span),
                            rnd.choice((+1, -1))), sub, psegs, grid))
        rv.sort()
        rows = {"transform": n, "random_max": rv[-1],
                "random_median": rv[len(rv) // 2],
                "random_ge10": sum(1 for v in rv if v >= 10)}
        for lbl, TT in (
                ("shift +0.5 CAD", (s, th, tx + 0.5, ty, eps)),
                ("shift +5 CAD", (s, th, tx + 5.0, ty, eps)),
                ("rot +0.25 deg", (s, th + math.radians(0.25), tx, ty, eps)),
                ("rot +1 deg", (s, th + math.radians(1.0), tx, ty, eps)),
                ("rot +90 deg", (s, th + math.radians(90.0), tx, ty, eps)),
                ("scale x0.99", (s * 0.99, th, tx, ty, eps)),
                ("scale x1.01", (s * 1.01, th, tx, ty, eps)),
                ("axis swap", (s, th + math.pi / 2.0, -ty, tx, -eps)),
                ("opposite reflection", (s, th, tx, ty, -eps))):
            rows[lbl] = cons(TT, sub, psegs, grid)
        out("")
        out("PDF %-42s DXF %s" % (pdf_tag, dxf_tag))
        out("   selected transform      %5d / %d  (%.1f %%)"
            % (n, len(sub), 100.0 * n / len(sub)))
        out("   %d random wrong T       median=%d max=%d  (>=10 in %d/%d)"
            % (N_RANDOM, rows["random_median"], rows["random_max"],
               rows["random_ge10"], N_RANDOM))
        for lbl in ("shift +0.5 CAD", "shift +5 CAD", "rot +0.25 deg",
                    "rot +1 deg", "rot +90 deg", "scale x0.99",
                    "scale x1.01", "axis swap", "opposite reflection"):
            out("   %-24s %5d / %d" % (lbl, rows[lbl], len(sub)))
        m = (n / rows["random_max"]) if rows["random_max"] else None
        out("   selectivity margin      %s"
            % ("%.1fx" % m if m else "infinite (random floor = 0)"))
        table[pdf_tag + "|" + dxf_tag] = rows

    with open(outp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    json.dump(table, open(os.path.join(RES, "b2_selectivity.json"), "w",
                          encoding="utf-8"), indent=1, default=str)
    print("\nwrote %s" % outp)


if __name__ == "__main__":
    main()
