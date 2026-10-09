"""Set B2 — cross-reference and overlap analysis (PHASES 8 and 9).

Research tool.

For every PDF it reports the measured separation between DXF candidates, and
for every PDF with more than one surviving candidate it measures whether the
matched areas coincide, whether the transforms are compatible, and whether the
two references agree with each other.

Nothing is merged automatically: DXF files are never combined.
"""

import json
import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import b2_core as C                                              # noqa: E402
import b2_matrix as B                                             # noqa: E402

RES = B.RES


def matched_bbox(T, ents, psegs, grid):
    xs = []
    ys = []
    n = 0
    for e in ents:
        if C.entity_matches(T, e, psegs, grid) is None:
            continue
        n += 1
        for a, b in e.segs:
            for p in (a, b):
                xs.append(p[0])
                ys.append(p[1])
    if not xs:
        return None, 0
    return (min(xs), max(xs), min(ys), max(ys)), n


def inter_overlap(b1, b2):
    if not b1 or not b2:
        return 0.0
    ix = max(0.0, min(b1[1], b2[1]) - max(b1[0], b2[0]))
    iy = max(0.0, min(b1[3], b2[3]) - max(b1[2], b2[2]))
    inter = ix * iy
    a1 = (b1[1] - b1[0]) * (b1[3] - b1[2])
    a2 = (b2[1] - b2[0]) * (b2[3] - b2[2])
    if a1 <= 0 or a2 <= 0:
        return 0.0
    return inter / min(a1, a2)


def main():
    res = json.load(open(os.path.join(RES, "b2_matrix.json"), encoding="utf-8"))
    outp = os.path.join(RES, "b2_cross.txt")
    lines = []

    def out(m):
        print(m)
        lines.append(m)

    by_pdf = defaultdict(list)
    for r in res:
        by_pdf[r["pdf"]].append(r)

    out("=" * 78)
    out("PHASE 8 — CROSS-REFERENCE: measured separation per PDF")
    out("=" * 78)
    summary = {}
    for pdf_tag, rows in sorted(by_pdf.items()):
        out("")
        out("PDF %s" % pdf_tag)
        live = [r for r in rows if r["verdict"] != "NO_EVIDENCE"]
        ranked = sorted(rows, key=lambda r: -(r["evidence"]["A_consensus_total"]
                                             if r["verdict"] != "NO_EVIDENCE"
                                             else -1))
        for i, r in enumerate(ranked):
            e = r["evidence"]
            d = r["defence"]
            out("  #%d %-24s %-18s consensus=%4d  ctrl=%4d check=%4d  "
                "rand-margin=%s"
                % (i + 1, r["dxf"], r["verdict"], e["A_consensus_total"],
                   e["A_control"], e["A_check"],
                   ("%.1fx" % d["margin_random"])
                   if d["margin_random"] != float("inf") else "n/a"))
        if not live:
            out("  -> NO surviving reference candidate")
            summary[pdf_tag] = {"best": "NONE", "second": "NONE"}
            continue
        best = ranked[0]
        second = ranked[1] if len(ranked) > 1 else None
        out("  BEST     : %s" % best["dxf"])
        out("  SECOND   : %s" % (second["dxf"] if second else "NONE"))
        bc = best["evidence"]["A_consensus_total"]
        sc = second["evidence"]["A_consensus_total"] if second else 0
        out("  separation by consensus: %d vs %d -> ratio %.2fx"
            % (bc, sc, (bc / sc) if sc else float("inf")))
        summary[pdf_tag] = {"best": best["dxf"], "second": second["dxf"] if second else "NONE",
                            "best_consensus": bc, "second_consensus": sc}

    out("")
    out("=" * 78)
    out("PHASE 9 — OVERLAPPING REFERENCES: do two DXFs share one PDF?")
    out("=" * 78)
    overlap = {}
    for pdf_tag, rows in sorted(by_pdf.items()):
        live = [r for r in rows if r["verdict"] != "NO_EVIDENCE"]
        if len(live) < 2:
            overlap[pdf_tag] = "only one surviving candidate"
            out("")
            out("PDF %s : only one surviving candidate (%s)"
                % (pdf_tag, live[0]["dxf"] if live else "none"))
            continue
        psegs = B.load_pdf(pdf_tag)
        grid = C.SegGrid(psegs)
        info = []
        for r in live:
            dxf_path = [os.path.join(B.BENCH, f) for f in os.listdir(B.BENCH)
                        if f.lower().startswith(r["dxf"].lower())][0]
            ents, uc = B.load_dxf(dxf_path)
            dxf_ents = [e for e in ents if e.segs]
            T = (r["transform"]["scale"],
                 math.radians(r["transform"]["rotation_deg"]),
                 r["transform"]["tx"], r["transform"]["ty"],
                 r["transform"]["eps"])
            bb, n = matched_bbox(T, dxf_ents, psegs, grid)
            info.append((r["dxf"], T, bb, n))
        out("")
        out("PDF %s : %d surviving candidates" % (pdf_tag, len(live)))
        for name, T, bb, n in info:
            out("   %-24s matched %4d entities" % (name, n))
            if bb:
                out("      matched CAD bbox  cad_x %.3f .. %.3f  (span %.3f)"
                    % (bb[0], bb[1], bb[1] - bb[0]))
                out("                        cad_y %.3f .. %.3f  (span %.3f)"
                    % (bb[2], bb[3], bb[3] - bb[2]))
        for i in range(len(info)):
            for j in range(i + 1, len(info)):
                (n1, T1, b1, c1), (n2, T2, b2, c2) = info[i], info[j]
                ov = inter_overlap(b1, b2)
                sratio = T2[0] / T1[0]
                dth = math.degrees(T2[1] - T1[1]) % 180.0
                if dth > 90:
                    dth -= 180
                out("   PAIR %s  vs  %s" % (n1, n2))
                out("      matched-area overlap  : %.1f %% of the smaller area"
                    % (100.0 * ov))
                out("      scale ratio T2/T1     : %.6f" % sratio)
                out("      rotation difference   : %+.4f deg" % dth)
                out("      reflection flags      : %+d vs %+d" % (T1[4], T2[4]))
                out("      -> transforms are %s"
                    % ("COMPATIBLE in scale/rotation"
                       if abs(sratio - 1.0) < 0.01 and abs(dth) < 0.5
                       else "NOT compatible in scale/rotation"))
                overlap[pdf_tag] = {"pair": [n1, n2], "overlap_pct": 100 * ov,
                                    "scale_ratio": sratio, "dth": dth,
                                    "eps": [T1[4], T2[4]]}

    with open(outp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    json.dump({"per_pdf": summary, "overlap": overlap},
              open(os.path.join(RES, "b2_cross.json"), "w", encoding="utf-8"),
              indent=1, default=str)
    print("\nwrote %s" % outp)


if __name__ == "__main__":
    main()
