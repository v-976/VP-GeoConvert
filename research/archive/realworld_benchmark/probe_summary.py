"""Summarise pdfium_probe stdout for the Set B1 real-world benchmark.

Research tool. Reads only the probe's stdout artefact.

PAGE space: u = horizontal, v = vertical
PDF raw/object space (p, q) is what the probe reports per object before the
object matrix is applied; this script therefore also reports the dominant
object matrix so the two domains stay distinguishable.
"""

import re
import sys
from collections import Counter, defaultdict

RE_OBJ = re.compile(r"^\s*\[obj\]\s+type\s+=\s+(\w+)")
RE_MATRIX = re.compile(
    r"object matrix \(PAGE\)\s*=\s*\[a=(\S+)\s+b=(\S+)\s+c=(\S+)\s+d=(\S+)"
    r"\s+e=(\S+)\s+f=(\S+)\]")
RE_SEGCOUNT = re.compile(r"^\s*segments\s+=\s+(\d+)")
RE_SEG = re.compile(r"seg\[(\d+)\]\s+(\w+)\s+u=(\S+)\s+v=(\S+)\s+close=(\w+)")
RE_PAGE = re.compile(r"^PAGE index\s+=\s+(\d+)")
RE_PAGESIZE = re.compile(r"page size \(points\)\s+=\s*u=(\S+)\s+v=(\S+)")
RE_ROT = re.compile(r"page rotation\s+=\s+(-?\d+)")
RE_PAGEOBJ = re.compile(r"page object count\s+=\s+(\d+)")
RE_TRANSP = re.compile(r"page has transparency\s+=\s*(\w+)")
RE_PAGECOUNT = re.compile(r"document page count\s+=\s+(\d+)")
RE_VER = re.compile(r"document file version\s+=\s+(\d+)")
RE_IMG = re.compile(r"image pixel size\s+=\s+(\d+) x (\d+) px")
RE_IMGCS = re.compile(r"image colorspace\s+=\s+(\w+)")


def main(path):
    # detect UTF-16LE (PowerShell redirect) or plain
    with open(path, "rb") as fh:
        head = fh.read(2)
    enc = "utf-16" if head in (b"\xff\xfe", b"\xfe\xff") else "latin-1"

    types = Counter()
    segs = Counter()
    mats = Counter()
    pages = []
    cur = None
    segcount_declared = 0
    segcount_present = 0
    trunc_notes = 0
    close_true = 0
    img_sizes = Counter()
    img_cs = Counter()
    page = None
    doc_pages = None
    doc_ver = None
    truncated_paths = 0

    with open(path, "r", encoding=enc) as fh:
        for line in fh:
            m = RE_PAGECOUNT.search(line)
            if m:
                doc_pages = int(m.group(1)); continue
            m = RE_VER.search(line)
            if m:
                doc_ver = int(m.group(1)); continue
            m = RE_PAGE.match(line)
            if m:
                page = {"index": int(m.group(1)), "size": None,
                        "rot": None, "objects": None, "transp": None}
                pages.append(page)
                cur = None
                continue
            m = RE_PAGESIZE.search(line)
            if m and page is not None:
                page["size"] = (float(m.group(1)), float(m.group(2)))
                continue
            m = RE_ROT.search(line)
            if m and page is not None:
                page["rot"] = int(m.group(1)); continue
            m = RE_PAGEOBJ.search(line)
            if m and page is not None:
                page["objects"] = int(m.group(1)); continue
            m = RE_TRANSP.search(line)
            if m and page is not None:
                page["transp"] = m.group(1); continue
            m = RE_MATRIX.search(line)
            if m:
                mats[tuple(float(g) for g in m.groups())] += 1
                continue
            m = RE_OBJ.match(line)
            if m:
                t = m.group(1)
                types[t] += 1
                cur = {"declared": 0, "present": 0}
                continue
            m = RE_SEGCOUNT.match(line)
            if m and cur is not None:
                cur["declared"] = int(m.group(1))
                segcount_declared += cur["declared"]
                continue
            m = RE_SEG.search(line)
            if m and cur is not None:
                segs[m.group(2)] += 1
                cur["present"] += 1
                segcount_present += 1
                if m.group(5) == "true":
                    close_true += 1
                continue
            if "further segment(s) not shown" in line:
                trunc_notes += 1
                truncated_paths += 1
                continue
            m = RE_IMG.search(line)
            if m:
                img_sizes[(int(m.group(1)), int(m.group(2)))] += 1
                continue
            m = RE_IMGCS.search(line)
            if m:
                img_cs[m.group(1)] += 1
                continue

    print("=== PDFium probe summary ===")
    print("PDF file version       : %s" % doc_ver)
    print("document page count    : %s" % doc_pages)
    print()
    print("-- per page --")
    for p in pages:
        print("   page %-3d size=%s  rot=%s  objects=%s  transparency=%s"
              % (p["index"], p["size"], p["rot"], p["objects"], p["transp"]))
    print()
    print("-- page object types (PDFium page objects only, no FORM descent) --")
    for k, v in sorted(types.items(), key=lambda kv: -kv[1]):
        print("   %-10s %d" % (k, v))
    print()
    print("-- path segments (printed, subject to probe cap of 64/path) --")
    print("   TOTAL printed        %d" % segcount_present)
    for k, v in sorted(segs.items(), key=lambda kv: -kv[1]):
        print("   %-14s %d" % (k, v))
    print("   segments declared    %d" % segcount_declared)
    print("   CLOSE flags (close=true) %d" % close_true)
    print("   probe truncation notices  %d" % trunc_notes)
    print()
    print("-- object matrices (raw PDF object space -> PAGE) --")
    print("   distinct matrices   %d" % len(mats))
    for k, v in mats.most_common(8):
        print("   %8d x  a=%-14s b=%-12s c=%-12s d=%-14s e=%-14s f=%-12s"
              % (v, k[0], k[1], k[2], k[3], k[4], k[5]))
    print()
    if img_sizes:
        print("-- IMAGE objects --")
        for k, v in img_sizes.most_common(10):
            print("   %d x %d px   seen %d times" % (k[0], k[1], v))
        print("   colorspaces: %s" % dict(img_cs))
    else:
        print("-- IMAGE objects: none reported by FPDFImageObj_GetImageMetadata --")


if __name__ == "__main__":
    main(sys.argv[1])