"""Minimal, safe PDF forensics by scanning uncompressed structure only.

Research tool. Deliberately NOT a full PDF parser: it reads the PDF header,
the trailer/catalog-level object dictionaries that are stored uncompressed, and
counts structural markers that survive in the byte stream. It never writes.

Nothing here is used as matching evidence; it is inventory only.

PDF raw/object coordinate domain : p, q   (as stored, per object)
"""

import re
import zlib


def scan(path):
    data = open(path, "rb").read()
    info = {"bytes": len(data)}

    m = re.match(rb"%PDF-(\d+\.\d+)", data)
    info["pdf_version"] = m.group(1).decode() if m else None

    # ---- trailer -------------------------------------------------------
    tail = data[-2048:]
    mt = re.search(rb"trailer\s*<<(.{0,400}?)>>", tail, re.S)
    info["trailer"] = mt.group(1).decode("latin-1").strip() if mt else None
    info["has_xref_stream"] = bool(re.search(rb"/Type\s*/XRef", data))
    info["has_objstm"] = bool(re.search(rb"/Type\s*/ObjStm", data))
    info["encrypted"] = bool(re.search(rb"/Encrypt\s", data))

    # ---- structural markers present in the byte stream ------------------
    markers = {
        "/OCProperties": "optional content (OCG) dictionary",
        "/OCGs": "optional content group array",
        "/OC": "optional content membership key",
        "/BDC": "begin marked content",
        "/EMC": "end marked content",
        "/Image": "image XObject",
        "/DCTDecode": "JPEG (DCT) image data",
        "/JPXDecode": "JPEG 2000 image data",
        "/CCITTFaxDecode": "CCITT G4 image data",
        "/FlateDecode": "Flate-compressed data",
        "/Subtype /Form": "form XObject",
        "/Subtype/Form": "form XObject",
        "/Type /Page": "page object",
        "/Type/Page": "page object",
        "/MediaBox": "page media box",
        "/CropBox": "crop box",
        "/BleedBox": "bleed box",
        "/TrimBox": "trim box",
        "/Annots": "annotations",
        "/SMask": "soft mask",
        "/Group": "transparency group",
        "/Pattern": "pattern",
        "/Shading": "shading",
        "/Viewport": "viewport (PDF/X)",
        "/OutputIntent": "output intent",
        "/Metadata": "XMP metadata stream",
    }
    found = {}
    for k, desc in markers.items():
        c = data.count(k.encode("latin-1"))
        if c:
            found[k] = c
    info["markers"] = found

    # ---- Info dictionary (usually uncompressed) ------------------------
    mi = re.search(rb"/Info\s+(\d+)\s+0\s+R", data)
    info["info_obj"] = mi.group(1).decode() if mi else None
    if mi:
        num = mi.group(1).decode()
        mo = re.search((r"(?<![0-9])" + num + r"\s+0\s+obj\b").encode(),
                       data)
        if mo:
            seg = data[mo.start():mo.start() + 1200]
            info["info_raw"] = seg[:600].decode("latin-1", "replace")

    # ---- Root / catalog dictionary --------------------------------------
    mr = re.search(rb"/Root\s+(\d+)\s+0\s+R", data)
    info["root_obj"] = mr.group(1).decode() if mr else None
    if mr:
        num = mr.group(1).decode()
        mo = re.search((r"(?<![0-9])" + num + r"\s+0\s+obj\b").encode(),
                       data)
        if mo:
            seg = data[mo.start():mo.start() + 2000]
            info["catalog_raw"] = seg[:900].decode("latin-1", "replace")

    # ---- MediaBox values (usually in page dicts, uncompressed) ---------
    boxes = re.findall(rb"/MediaBox\s*\[\s*([-\d\.\s]+?)\]", data)
    mb = []
    for b in boxes[:20]:
        try:
            vals = [float(x) for x in b.split()]
            if len(vals) == 4:
                mb.append(tuple(vals))
        except ValueError:
            pass
    info["mediaboxes"] = mb
    cb = re.findall(rb"/CropBox\s*\[\s*([-\d\.\s]+?)\]", data)
    info["cropbox_count"] = len(cb)
    tb = re.findall(rb"/TrimBox\s*\[\s*([-\d\.\s]+?)\]", data)
    info["trimbox_count"] = len(tb)

    # ---- page tree count ------------------------------------------------
    mp = re.search(rb"/Type\s*/Pages(.{0,400}?)/Kids", data, re.S)
    if mp:
        mc = re.search(rb"/Count\s+(\d+)", mp.group(1))
        info["pages_count_declared"] = int(mc.group(1)) if mc else None
    info["page_objects_seen"] = len(re.findall(rb"/Type\s*/Page[^s]", data))

    return info


def main():
    import sys
    path = sys.argv[1]
    info = scan(path)
    print("=== PDF structural forensics (no decompression) ===")
    print("bytes                : %d" % info["bytes"])
    print("PDF version          : %s" % info["pdf_version"])
    print("encrypted            : %s" % info["encrypted"])
    print("xref stream          : %s" % info["has_xref_stream"])
    print("object streams       : %s" % info["has_objstm"])
    print("trailer              : %s" % info["trailer"])
    print("Root object          : %s" % info["root_obj"])
    print("Info object          : %s" % info["info_obj"])
    if "info_raw" in info:
        print("--- Info dictionary (raw) ---")
        print(info["info_raw"][:400])
    if "catalog_raw" in info:
        print("--- Catalog (raw, first 700 chars) ---")
        print(info["catalog_raw"][:700])
    print()
    print("--- structural markers present in byte stream ---")
    for k, c in sorted(info["markers"].items(), key=lambda kv: -kv[1]):
        print("   %-22s x%-6d  (%s)" % (k, c, {
            "/OCProperties": "optional content (OCG) dictionary",
            "/OCGs": "OCG array",
            "/OC": "optional content membership key",
            "/BDC": "begin marked content",
            "/EMC": "end marked content",
            "/Image": "image XObject",
            "/DCTDecode": "JPEG image data",
            "/JPXDecode": "JPEG2000 image data",
            "/CCITTFaxDecode": "CCITT image data",
            "/FlateDecode": "Flate-compressed data",
            "/Subtype /Form": "form XObject",
            "/Subtype/Form": "form XObject",
            "/Type /Page": "page object",
            "/MediaBox": "MediaBox",
            "/CropBox": "CropBox",
            "/BleedBox": "BleedBox",
            "/TrimBox": "TrimBox",
            "/Annots": "annotations",
            "/SMask": "soft mask",
            "/Group": "transparency group",
            "/Pattern": "pattern",
            "/Shading": "shading",
            "/Viewport": "viewport",
            "/OutputIntent": "output intent",
            "/Metadata": "XMP metadata",
        }.get(k, "")))
    print()
    print("pages /Count declared: %s" % info.get("pages_count_declared"))
    print("page objects seen    : %s" % info["page_objects_seen"])
    print("CropBox count        : %s" % info["cropbox_count"])
    print("TrimBox count        : %s" % info["trimbox_count"])
    if info["mediaboxes"]:
        print("MediaBox values (distinct):")
        for b in sorted(set(info["mediaboxes"]))[:10]:
            print("   %s" % (b,))


if __name__ == "__main__":
    main()