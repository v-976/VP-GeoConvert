"""Set B2 — full 4 PDF x 3 DXF blind candidate matrix.

Research tool. Runs every combination independently; never stops after the
first good match, never assumes one-to-one correspondence, and never uses a
file name as matching evidence.

Method constraints enforced here
--------------------------------
* Similarity (Helmert) is the only transform used for correspondence.
* Reflection (eps = -1) and axis swap are EXPLICIT separate hypotheses.
* Affine is never used to select a correspondence. (Not computed at all in
  this pass; see NOT VERIFIED.)
* Hypothesis generation, ranking, fitting, refitting, reflection selection
  and orientation selection use CONTROL entities ONLY.
* CHECK entities are evaluated once, at the end, with a frozen transform.
* A whole entity is one unit of evidence: it is never split across pools.

Coordinate domains
------------------
    PAGE  u, v          after each PDF object's own matrix
    CAD   cad_x, cad_y  raw DXF group codes
    SURVEY X, Y         NOT USED (mapping not established)
Strictly 2D.
"""

import json
import math
import os
import random
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "geometry_benchmark"))

import b2_core as C                                              # noqa: E402
import dxf_geom as G                                             # noqa: E402
import pdf_reader as P                                           # noqa: E402

BENCH = r'benchmark\Set_B2_Test'
RES = r'research\results\archive\b2'

# exploration parameters -- NOT acceptance thresholds
FIT_SEGS = 200
SIG_CAP = 20000

N_RANDOM = 40
SUBSET = 250          # entities used for the selectivity sweep (speed)
UNIT_NAME = {1: "inches", 2: "feet", 4: "millimetres", 5: "centimetres",
             6: "metres", 14: "decimetres"}


def log(msg, fh=None):
    print(msg)
    if fh:
        fh.write(msg + "\n")


def load_pdf(tag):
    txt = os.path.join(RES, tag + "_probe.txt")
    paths, pages = P.parse(txt)
    segs = []
    for p in paths:
        segs.extend(P.segments_of(p))
    return segs


def load_dxf(path):
    ents, hdr = G.load(path)
    code = None
    ins = hdr.get("$INSUNITS")
    if ins:
        try:
            code = int(float(ins[0][1]))
        except ValueError:
            code = None
    return ents, code


def consensus(T, pool, psegs, grid, tol=C.TOL_PT):
    return sum(1 for e in pool
               if C.seg_matches_set(T, e, psegs, grid, tol) is not None)


def run_combo(pdf_tag, dxf_tag, out):
    t_start = time.time()
    log("", out)
    log("=" * 78, out)
    log("COMBINATION  PDF=%s   DXF=%s" % (pdf_tag, dxf_tag), out)
    log("=" * 78, out)

    dxf_path = [os.path.join(BENCH, f) for f in os.listdir(BENCH)
                if f.lower().startswith(dxf_tag.lower())][0]
    ents, unit_code = load_dxf(dxf_path)
    unit_name = UNIT_NAME.get(unit_code, "UNKNOWN")
    dxf_ents = [e for e in ents if e.segs]
    ctrl, chk = C.spatial_split_balanced(dxf_ents)

    psegs = load_pdf(pdf_tag)
    grid = C.SegGrid(psegs)

    log("  DXF entities with straight geometry : %d" % len(dxf_ents), out)
    log("  CONTROL pool / CHECK pool           : %d / %d"
        % (len(ctrl), len(chk)), out)
    log("  $INSUNITS                           : %s (%s)"
        % (unit_code, unit_name), out)
    log("  PAGE straight segments              : %d" % len(psegs), out)
    log("  matching tolerance                  : %.2f PAGE pt (direction "
        "%.1f deg)" % (C.TOL_PT, C.DIR_TOL_DEG), out)

    # ---- PHASE 3: blind coarse matching, CONTROL only ------------------
    csegs = [(a, b) for e in ctrl for a, b in e.segs]
    hyps = []
    for eps in (+1, -1):
        rr = C.hough_search(csegs, psegs, eps, pool=ctrl, grid=grid,
                            tol=C.TOL_PT, sig_cap=SIG_CAP, fit_segs=FIT_SEGS)
        if not rr:
            log("  eps=%+d : no hypothesis produced any exact fit" % eps, out)
            hyps.append({"eps": eps, "T": None, "control": 0, "ranked": []})
            continue
        r = rr[0]
        n = consensus(r["T"], ctrl, psegs, grid)
        ranked = [{"consensus": x["consensus"], "votes": x["votes"],
                   "scale": x["T"][0], "rotation_deg": math.degrees(x["T"][1]),
                   "tx": x["T"][2], "ty": x["T"][3]} for x in rr[:4]]
        hyps.append({"eps": eps, "T": r["T"], "control": n,
                     "votes": r["votes"], "ranked": ranked})
        log("  eps=%+d (%-12s) cluster-votes=%-6d search-consensus140=%3d "
            " CONTROL matched = %d / %d"
            % (eps, "no reflection" if eps > 0 else "REFLECTION",
               r["votes"], r["consensus"], n, len(ctrl)), out)
        for rk in ranked:
            log("       candidate: votes %4d cons %3d  s=%.9f  th=%+11.5f deg"
                % (rk["votes"], rk["consensus"], rk["scale"],
                   rk["rotation_deg"]), out)

    live = [h for h in hyps if h["T"] is not None]
    if not live:
        log("  RESULT: NO_EVIDENCE (no hypothesis generated)", out)
        return {"pdf": pdf_tag, "dxf": dxf_tag, "verdict": "NO_EVIDENCE",
                "unit": unit_name, "seconds": time.time() - t_start}

    live.sort(key=lambda h: -h["control"])
    best = live[0]
    runner = live[1] if len(live) > 1 else None
    log("  selected eps=%+d on CONTROL consensus alone" % best["eps"], out)

    # ---- PHASE 5/6: refit on CONTROL only, then evaluate ---------------
    T0 = C.midpoint_refit(best["T"], ctrl, psegs, grid)

    # ---- PHASE 4: evidence dimensions, measured separately -------------
    ev = C.evidence(T0, ctrl, chk, psegs, grid, unit_scale=1.0)

    # ---- PHASE 7: false-match defence ---------------------------------
    s, th, tx, ty, eps = T0
    sub = ctrl[:SUBSET] if len(ctrl) > SUBSET else ctrl
    sub_n = consensus(T0, sub, psegs, grid)
    sweeps = []

    def rec(label, T):
        v = consensus(T, sub, psegs, grid)
        sweeps.append({"label": label, "value": v})
        log("     %-30s %5d / %d" % (label, v, len(sub)), out)
        return v

    log("  -- selectivity sweep on a %d-entity CONTROL subset --"
        % len(sub), out)
    rec("selected transform", T0)
    for dm in (0.5, 2.0, 10.0, 100.0):
        rec("shift cad_x +%g" % dm, (s, th, tx + dm, ty, eps))
    for dm in (0.5, 2.0, 10.0, 100.0):
        rec("shift cad_y +%g" % dm, (s, th, tx, ty + dm, eps))
    for dd in (0.05, 0.25, 1.0, 5.0):
        rec("rotation +%g deg" % dd,
            (s, th + math.radians(dd), tx, ty, eps))
    for f in (0.99, 0.95, 1.01, 1.05):
        rec("scale x%g" % f, (s * f, th, tx, ty, eps))
    rec("axis swap cad_x<->cad_y",
        (s, th + math.pi / 2.0, -ty, tx, -eps))
    rec("opposite reflection", (s, th, tx, ty, -eps))

    rnd = random.Random(20261007)
    rand_vals = []
    span = max(1.0, abs(tx) * 1e-4)
    for i in range(N_RANDOM):
        T = (s * math.exp(rnd.uniform(-0.6, 0.6)),
             th + rnd.uniform(-math.pi, math.pi),
             tx + rnd.uniform(-2 * span, 2 * span),
             ty + rnd.uniform(-2 * span, 2 * span),
             rnd.choice((+1, -1)))
        rand_vals.append(consensus(T, sub, psegs, grid))
    rand_vals.sort()
    log("     %-30s median=%d max=%d  (>=10 in %d/%d)"
        % ("%d random wrong transforms" % N_RANDOM, rand_vals[len(rand_vals) // 2],
           rand_vals[-1], sum(1 for v in rand_vals if v >= 10), N_RANDOM), out)

    rand_max = rand_vals[-1]
    best_second = runner["control"] if runner else 0

    # ---- concentration diagnostics ------------------------------------
    matched_all = [r["entity"] for r in ev["rows_control"] + ev["rows_check"]]
    layers = Counter(e.layer for e in dxf_ents)
    mlayers = Counter(e.layer for e in matched_all)
    lay_conc = max((mlayers[k] / layers[k] for k in layers
                    if layers[k] > 0), default=0.0)
    # how much of the consensus sits in the single most productive layer
    top_layer = mlayers.most_common(1)[0] if mlayers else (None, 0)
    layer_share = (top_layer[1] / len(matched_all)) if matched_all else 0.0

    # ---- verdict ------------------------------------------------------
    chk_frac = ev["B_check_fraction"]
    ctrl_frac = ev["B_control_fraction"]
    margin_random = (sub_n / rand_max) if rand_max else float("inf")
    margin_second = (best["control"] / best_second) if best_second else float("inf")

    if sub_n >= 30 and margin_random >= 5.0 and ctrl_frac >= 0.10 \
            and ev["G_check_entities"] >= 15:
        verdict = "STRONG_CANDIDATE"
    elif sub_n >= 8 and margin_random >= 3.0 and ctrl_frac >= 0.02:
        verdict = "PARTIAL_CANDIDATE"
    elif sub_n >= 8 and margin_random < 3.0:
        verdict = "AMBIGUOUS"
    else:
        verdict = "NO_EVIDENCE"

    log("  -- verdict --", out)
    log("     subset consensus %d | random max %d | margin %.1fx"
        % (sub_n, rand_max, margin_random), out)
    log("     second-best transform consensus %d | margin %.1fx"
        % (best_second, margin_second), out)
    log("     CONTROL fraction %.3f | CHECK fraction %.3f"
        % (ctrl_frac, chk_frac), out)
    log("     cells filled %d/%d | entity types %d/%d | layers %d"
        % (ev["C_cells_filled"], ev["C_cells_total"],
           ev["D_types_matched"], ev["D_types_total"], len(mlayers)), out)
    log("     VERDICT: %s" % verdict, out)

    return {
        "pdf": pdf_tag, "dxf": dxf_tag, "verdict": verdict,
        "unit_code": unit_code, "unit": unit_name,
        "dxf_entities": len(dxf_ents),
        "ctrl_pool": len(ctrl), "chk_pool": len(chk),
        "pdf_segments": len(psegs),
        "transform": {"scale": s, "rotation_deg": math.degrees(th),
                      "tx": tx, "ty": ty, "eps": eps},
        "eps_hypotheses": hyps,
        "evidence": {
            "A_consensus_total": ev["A_consensus"],
            "A_control": ev["A_control"], "A_check": ev["A_check"],
            "B_control_fraction": ctrl_frac, "B_check_fraction": chk_frac,
            "C_cells_filled": ev["C_cells_filled"],
            "C_cells_total": ev["C_cells_total"],
            "D_types_matched": ev["D_types_matched"],
            "D_types_total": ev["D_types_total"],
            "D_by_type": {k: list(v) for k, v in
                          ev["D_entity_type_counts"].items()},
            "E_pdf_segments_used": ev["E_pdf_segments_used"],
            "F_control": ev["F_control"], "F_check": ev["F_check"],
            "G_check_entities": ev["G_check_entities"],
            "G_check_features": ev["G_check_features"],
        },
        "defence": {"sweeps": sweeps, "random_median":
                    rand_vals[len(rand_vals) // 2], "random_max": rand_max,
                    "subset_size": len(sub), "subset_consensus": sub_n,
                    "margin_random": margin_random,
                    "second_best": best_second,
                    "margin_second_transform": margin_second,
                    "layer_share_top": layer_share,
                    "layer_max_match_fraction": lay_conc,
                    "layers_matched": len(mlayers),
                    "layers_total": len(layers)},
        "seconds": time.time() - t_start,
    }


def main():
    if not C.run_self_test():
        print("ABORT: core self-test failed")
        return 1
    pdfs = sorted(f[:-len("_probe.txt")] for f in os.listdir(RES)
                  if f.endswith("_probe.txt"))
    dxfs = ["TKA_Asematie", "TKA_LiiPy", "valaistus_Postitori"]
    print("PDFs : %s" % pdfs)
    print("DXFs : %s" % dxfs)

    outp = os.path.join(RES, "b2_matrix.txt")
    results = []
    with open(outp, "w", encoding="utf-8") as out:
        for ptag in pdfs:
            for dtag in dxfs:
                r = run_combo(ptag, dtag, out)
                results.append(r)
                out.flush()
                json.dump(results, open(os.path.join(RES, "b2_matrix.json"),
                                        "w", encoding="utf-8"),
                          indent=1, default=str)
    print("\nwrote %s and b2_matrix.json" % outp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
