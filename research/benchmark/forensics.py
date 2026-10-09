"""Structural PDF forensics.

Deliberately NOT a full PDF parser. It reads what is reliably readable without
decompressing content streams: the header, the trailer and catalog-level
dictionaries, page boxes, and structural markers that survive in the byte
stream. Everything it reports is either measured or explicitly marked unknown.

The most important entry in this module is `ocg_status`. Optional Content Group
information is reported as PRESENT, ABSENT or NOT_VERIFIED, never as a binary
assumption. A byte-level scan cannot see inside an object stream or an
uncompressed cross-reference stream, so "no /OCProperties in the byte stream"
means "not found by this scan", which is NOT_VERIFIED rather than ABSENT. An
earlier stage reported "no OCG" from exactly this reasoning and was wrong: the
file did carry 92 OCG objects.
"""

import os
import re
import zlib
from typing import Dict, List, Optional, Tuple

OCG_PRESENT = "PRESENT"
OCG_ABSENT = "ABSENT"
OCG_NOT_VERIFIED = "NOT_VERIFIED"

_MARKERS = {
    "/OCProperties": "optional content (OCG) dictionary",
    "/OCGs": "optional content group array",
    "/Image": "image XObject",
    "/DCTDecode": "JPEG (DCT) image data",
    "/JPXDecode": "JPEG 2000 image data",
    "/CCITTFaxDecode": "CCITT G4 image data",
    "/JBIG2Decode": "JBIG2 image data",
    "/Subtype /Form": "form XObject",
    "/Subtype/Form": "form XObject",
    "/Type /Pages": "page tree node",
    "/CropBox": "crop box",
    "/TrimBox": "trim box",
    "/BleedBox": "bleed box",
    "/Viewport": "PDF/X viewport",
    "/OutputIntent": "PDF/X output intent",
    "/SMask": "soft mask",
    "/Group": "transparency group",
    "/Shading": "shading",
    "/Encrypt": "encryption dictionary",
    "/Type/XRef": "cross-reference stream",
    "/Type/ObjStm": "object stream",
}


def structural_scan(path: str) -> Dict:
    """Byte-level structural inventory. Never writes to the file."""
    with open(path, "rb") as fh:
        data = fh.read()
    out: Dict = {"size_bytes": len(data), "path": os.path.abspath(path)}

    m = re.match(rb"%PDF-(\d+\.\d+)", data)
    out["pdf_version"] = m.group(1).decode() if m else None

    tail = data[-4096:]
    mt = re.search(rb"trailer\s*<<(.{0,400}?)>>", tail, re.S)
    out["trailer"] = mt.group(1).decode("latin-1").strip() if mt else None
    out["xref_stream"] = bool(re.search(rb"/Type\s*/XRef", data))
    out["object_streams"] = bool(re.search(rb"/Type\s*/ObjStm", data))
    out["encrypted"] = bool(re.search(rb"/Encrypt\b", data))

    markers: Dict[str, int] = {}
    for key in _MARKERS:
        count = data.count(key.encode("latin-1"))
        if count:
            markers[key] = count
    markers["/OC"] = len(re.findall(rb"/OC\b", data))
    if markers["/OC"] == 0:
        markers.pop("/OC")
    out["markers"] = markers

    def _dict_for(key: bytes) -> Optional[str]:
        mk = re.search(key + rb"\s+(\d+)\s+0\s+R", data)
        if not mk:
            return None
        num = mk.group(1).decode()
        mo = re.search((r"(?<![0-9])" + num + r"\s+0\s+obj\b").encode(), data)
        if not mo:
            return None
        return data[mo.start():mo.start() + 2000].decode("latin-1", "replace")

    info_raw = _dict_for(rb"/Info")
    out["info_object_present"] = info_raw is not None
    out["creator"] = None
    out["producer"] = None
    out["title"] = None
    out["creation_date"] = None
    out["mod_date"] = None
    if info_raw:
        for field, key in (("creator", rb"/Creator\s*\((.*?)\)\s*(?:/|>>)"),
                           ("producer", rb"/Producer\s*\((.*?)\)\s*(?:/|>>)"),
                           ("title", rb"/Title\s*\((.*?)\)\s*(?:/|>>)"),
                           ("creation_date",
                            rb"/CreationDate\s*\((.*?)\)\s*(?:/|>>)"),
                           ("mod_date", rb"/ModDate\s*\((.*?)\)\s*(?:/|>>)")):
            mm = re.search(key, info_raw.encode("latin-1", "replace"), re.S)
            if mm:
                out[field] = mm.group(1).decode("latin-1", "replace")

    out["catalog_raw"] = _dict_for(rb"/Root")

    boxes: List[Tuple[float, float, float, float]] = []
    for b in re.findall(rb"/MediaBox\s*\[\s*([-\d\.\s]+?)\]", data)[:32]:
        try:
            vals = [float(x) for x in b.split()]
        except ValueError:
            continue
        if len(vals) == 4:
            boxes.append(tuple(vals))
    out["media_boxes"] = sorted(set(boxes))
    out["crop_box_count"] = len(re.findall(rb"/CropBox", data))
    out["trim_box_count"] = len(re.findall(rb"/TrimBox", data))

    # ---- OCG status: three-valued, never a binary assumption ------------
    ocg_refs = re.search(rb"/OCGs\s*\[(.{0,200000}?)\]", data, re.S)
    ocg_count: Optional[int] = None
    if ocg_refs:
        ocg_count = len(re.findall(rb"\d+\s+0\s+R", ocg_refs.group(1)))
    out["ocg_object_count"] = ocg_count

    if "/OCProperties" in markers or ocg_count:
        out["ocg_status"] = OCG_PRESENT
        out["ocg_evidence"] = (
            "/OCProperties in byte stream%s"
            % ("" if not ocg_count else
               "; /OCGs array references %d objects" % ocg_count))
    elif out["xref_stream"] or out["object_streams"]:
        # Structure we cannot see through without a real parser.
        out["ocg_status"] = OCG_NOT_VERIFIED
        out["ocg_evidence"] = (
            "no /OCProperties in the byte stream, but this PDF uses "
            "cross-reference or object streams, so the catalog may be inside "
            "a compressed object stream; absence is NOT established")
    else:
        out["ocg_status"] = OCG_NOT_VERIFIED
        out["ocg_evidence"] = (
            "no /OCProperties found by the byte-level scan; this scan is not a "
            "full parser, so absence is not established")
    return out


def marker_lines(markers: Dict[str, int]) -> List[str]:
    return ["  %-20s x%-8d %s" % (k, v, _MARKERS.get(k, "optional content "
                                                   "membership key"))
            for k, v in sorted(markers.items(), key=lambda kv: -kv[1])]
