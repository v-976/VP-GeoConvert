// pdfium_probe — VP GeoConvert PDFium proof of concept
//
// RESEARCH PROBE. NOT PRODUCTION CODE.
//
// Purpose: determine whether PDFium's public API can expose engineering vector
// geometry from a PDF with sufficient fidelity to justify building the real
// VP PDF Vector Extractor on top of it.
//
// Coordinate model (AGENTS.md, docs/GEOMETRY_MODEL.md):
//   This stage has PAGE coordinates only.
//     u = PDF/page horizontal coordinate
//     v = PDF/page vertical coordinate
//   PDF/page coordinates are never named X or Y here. There is no SURVEY space,
//   no georeferencing and no Z in this program.
//
// Geometry policy:
//   Raw PDF path primitives are reported as PDFium reports them -
//   MOVE, LINE, CUBIC_BEZIER, CLOSE. No ARC, CIRCLE, POLYLINE or other
//   engineering primitive is recognised here. Recognition is a later stage.

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "fpdfview.h"
#include "fpdf_edit.h"
#include "fpdf_text.h"

namespace {

// PDFium's public path API returns single-precision floats. %.9g prints a float
// losslessly, so this formatting adds no rounding of its own. The precision
// ceiling is the engine's API, not this program - see README.md.
//
// NUM is spliced directly into printf format strings. It is NOT passed as an
// argument, which would make printf treat it as a string to print literally.
#define NUM "%.9g"

// Per-object output is capped so a real engineering drawing stays readable.
// Totals are always reported exactly; only the per-item detail is truncated.
constexpr int kMaxSegmentsReported = 64;
constexpr int kMaxCharsReported = 200;

// Guard against pathological or cyclic form nesting.
constexpr int kMaxFormDepth = 16;

struct Matrix {
  double a = 1.0;
  double b = 0.0;
  double c = 0.0;
  double d = 1.0;
  double e = 0.0;
  double f = 0.0;
};

// Affine composition in the PDFium FS_MATRIX convention:
//   u' = a*u + c*v + e
//   v' = b*u + d*v + f
//
// Returns the transform that applies |first| and then |second|, matching
// PDFium's own FS_Matrix::Multiply(first, second) ordering.
Matrix Multiply(const Matrix& first, const Matrix& second) {
  Matrix r;
  r.a = first.a * second.a + first.b * second.c;
  r.b = first.a * second.b + first.b * second.d;
  r.c = first.c * second.a + first.d * second.c;
  r.d = first.c * second.b + first.d * second.d;
  r.e = first.e * second.a + first.f * second.c + second.e;
  r.f = first.e * second.b + first.f * second.d + second.f;
  return r;
}

void PrintMatrix(const char* label, const Matrix& m) {
  std::printf("  %-28s = [a=" NUM " b=" NUM " c=" NUM " d=" NUM " e=" NUM
              " f=" NUM "]\n",
              label, m.a, m.b, m.c, m.d, m.e, m.f);
}

const char* ObjectTypeName(int type) {
  switch (type) {
    case FPDF_PAGEOBJ_UNKNOWN:
      return "UNKNOWN";
    case FPDF_PAGEOBJ_TEXT:
      return "TEXT";
    case FPDF_PAGEOBJ_PATH:
      return "PATH";
    case FPDF_PAGEOBJ_IMAGE:
      return "IMAGE";
    case FPDF_PAGEOBJ_SHADING:
      return "SHADING";
    case FPDF_PAGEOBJ_FORM:
      return "FORM";
    default:
      return "UNRECOGNISED";
  }
}

const char* SegmentTypeName(int type) {
  switch (type) {
    case FPDF_SEGMENT_MOVETO:
      return "MOVE";
    case FPDF_SEGMENT_LINETO:
      return "LINE";
    case FPDF_SEGMENT_BEZIERTO:
      return "CUBIC_BEZIER";
    case FPDF_SEGMENT_UNKNOWN:
      return "UNKNOWN";
    default:
      return "UNRECOGNISED";
  }
}

const char* ColorspaceName(int cs) {
  switch (cs) {
    case FPDF_COLORSPACE_DEVICEGRAY:
      return "DEVICEGRAY";
    case FPDF_COLORSPACE_DEVICERGB:
      return "DEVICERGB";
    case FPDF_COLORSPACE_DEVICECMYK:
      return "DEVICECMYK";
    case FPDF_COLORSPACE_CALGRAY:
      return "CALGRAY";
    case FPDF_COLORSPACE_CALRGB:
      return "CALRGB";
    case FPDF_COLORSPACE_LAB:
      return "LAB";
    case FPDF_COLORSPACE_ICCBASED:
      return "ICCBASED";
    case FPDF_COLORSPACE_SEPARATION:
      return "SEPARATION";
    case FPDF_COLORSPACE_DEVICEN:
      return "DEVICEN";
    case FPDF_COLORSPACE_INDEXED:
      return "INDEXED";
    case FPDF_COLORSPACE_PATTERN:
      return "PATTERN";
    default:
      return "UNKNOWN";
  }
}

// Minimal UTF-16LE -> UTF-8 conversion. Text is reported as-is and is never
// interpreted semantically (no coordinate/elevation parsing).
std::string Utf16LeToUtf8(const unsigned short* text, int length) {
  std::string out;
  out.reserve(static_cast<size_t>(length));
  for (int i = 0; i < length; ++i) {
    unsigned int cp = text[i];
    if (cp >= 0xD800 && cp <= 0xDBFF && i + 1 < length) {
      unsigned int lo = text[i + 1];
      if (lo >= 0xDC00 && lo <= 0xDFFF) {
        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
        ++i;
      }
    }
    if (cp < 0x80) {
      out.push_back(static_cast<char>(cp));
    } else if (cp < 0x800) {
      out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
      out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else if (cp < 0x10000) {
      out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
      out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
      out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else {
      out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
      out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
      out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
      out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    }
  }
  return out;
}

// Escapes control characters so the diagnostic output stays one record per line.
std::string Printable(const std::string& in) {
  std::string out;
  out.reserve(in.size());
  for (unsigned char c : in) {
    if (c == '\n') {
      out += "\\n";
    } else if (c == '\r') {
      out += "\\r";
    } else if (c == '\t') {
      out += "\\t";
    } else if (c < 0x20 || c == 0x7F) {
      char buf[8];
      std::snprintf(buf, sizeof(buf), "\\x%02X", c);
      out += buf;
    } else {
      out.push_back(static_cast<char>(c));
    }
  }
  return out;
}

void PrintCommonProperties(const char* indent, FPDF_PAGEOBJECT obj) {
  float left = 0.f, bottom = 0.f, right = 0.f, top = 0.f;
  if (FPDFPageObj_GetBounds(obj, &left, &bottom, &right, &top)) {
    // PDFium returns (left, bottom, right, top). Reported as u/v to keep PAGE
    // coordinates distinct from SURVEY X/Y.
    std::printf("%s%-28s = u_min=" NUM " v_min=" NUM " u_max=" NUM
                " v_max=" NUM "\n",
                indent, "bounds (PAGE space)", static_cast<double>(left),
                static_cast<double>(bottom), static_cast<double>(right),
                static_cast<double>(top));
  } else {
    std::printf("%s%-28s = not available\n", indent, "bounds (PAGE space)");
  }

  FS_MATRIX m;
  std::memset(&m, 0, sizeof(m));
  if (FPDFPageObj_GetMatrix(obj, &m)) {
    Matrix mx{static_cast<double>(m.a), static_cast<double>(m.b),
              static_cast<double>(m.c), static_cast<double>(m.d),
              static_cast<double>(m.e), static_cast<double>(m.f)};
    std::printf("%s", indent);
    PrintMatrix("object matrix (PAGE)", mx);
  } else {
    std::printf("%s%-28s = not available\n", indent, "object matrix (PAGE)");
  }

  FPDF_BOOL active = 0;
  if (FPDFPageObj_GetIsActive(obj, &active)) {
    std::printf("%s%-28s = %s\n", indent, "is-active (experimental API)",
                active ? "true" : "false");
  }
  // Source layer / PDF OCG is intentionally NOT printed: PDFium's public API
  // exposes no optional-content / layer information (see README.md).
}

void ReportPath(FPDF_PAGEOBJECT obj) {
  int count = FPDFPath_CountSegments(obj);
  if (count < 0) {
    std::printf("    segments                = not available\n");
    return;
  }
  std::printf("    segments                = %d\n", count);

  int reported = count < kMaxSegmentsReported ? count : kMaxSegmentsReported;
  for (int i = 0; i < reported; ++i) {
    FPDF_PATHSEGMENT seg = FPDFPath_GetPathSegment(obj, i);
    if (seg == nullptr) {
      std::printf("      seg[%d] <null handle>\n", i);
      continue;
    }
    int type = FPDFPathSegment_GetType(seg);
    float u = 0.f, v = 0.f;
    bool has_point = FPDFPathSegment_GetPoint(seg, &u, &v) != 0;
    bool close = FPDFPathSegment_GetClose(seg) != 0;

    if (has_point) {
      std::printf("      seg[%d] %-12s u=" NUM " v=" NUM " close=%s\n", i,
                  SegmentTypeName(type), static_cast<double>(u),
                  static_cast<double>(v), close ? "true" : "false");
    } else {
      std::printf("      seg[%d] %-12s point not available close=%s\n", i,
                  SegmentTypeName(type), close ? "true" : "false");
    }
    if (type == FPDF_SEGMENT_BEZIERTO) {
      std::printf(
          "               NOTE: public API exposes only ONE point per "
          "BEZIERTO segment\n");
    }
  }
  if (reported < count) {
    std::printf("      ... %d further segment(s) not shown (cap %d)\n",
                count - reported, kMaxSegmentsReported);
  }
}

void ReportImage(FPDF_PAGE page, FPDF_PAGEOBJECT obj) {
  FPDF_IMAGEOBJ_METADATA meta;
  std::memset(&meta, 0, sizeof(meta));
  if (!FPDFImageObj_GetImageMetadata(obj, page, &meta)) {
    std::printf("    image metadata          = not available\n");
    return;
  }
  std::printf(
      "    image pixel size        = %u x %u px (bits/pixel=%u)\n", meta.width,
      meta.height, meta.bits_per_pixel);
  std::printf("    image resolution        = " NUM " x " NUM " dpi\n",
              static_cast<double>(meta.horizontal_dpi),
              static_cast<double>(meta.vertical_dpi));
  std::printf("    image colorspace        = %s (%d)\n",
              ColorspaceName(meta.colorspace), meta.colorspace);
  if (meta.marked_content_id >= 0) {
    std::printf("    marked content id       = %d\n", meta.marked_content_id);
  }
}

void ReportObjectRecursive(FPDF_PAGE page, FPDF_PAGEOBJECT obj,
                           const Matrix& accumulated, int depth);

// Recursive FORM XObject traversal. |accumulated| is the composed transform from
// the page root down to the parent of |obj|.
void ReportForm(FPDF_PAGE page, FPDF_PAGEOBJECT form_obj,
                const Matrix& accumulated, int depth) {
  const std::string indent(depth * 2, ' ');

  int nested = FPDFFormObj_CountObjects(form_obj);
  if (nested < 0) {
    std::printf("%snested objects            = not available\n", indent.c_str());
    return;
  }
  std::printf("%snested objects            = %d\n", indent.c_str(), nested);

  if (depth >= kMaxFormDepth) {
    std::printf(
        "%sNOT TRAVERSED: maximum form nesting depth %d reached\n",
        indent.c_str(), kMaxFormDepth);
    return;
  }

  for (unsigned long i = 0; i < static_cast<unsigned long>(nested); ++i) {
    FPDF_PAGEOBJECT child = FPDFFormObj_GetObject(form_obj, i);
    if (child == nullptr) {
      std::printf("%s  [nested %lu] <null handle>\n", indent.c_str(), i);
      continue;
    }
    ReportObjectRecursive(page, child, accumulated, depth + 1);
  }
}

void ReportObjectRecursive(FPDF_PAGE page, FPDF_PAGEOBJECT obj,
                           const Matrix& accumulated, int depth) {
  const std::string indent(depth * 2, ' ');
  const int type = FPDFPageObj_GetType(obj);

  std::printf("%s[obj] type = %s\n", indent.c_str(), ObjectTypeName(type));
  PrintCommonProperties(indent.c_str(), obj);

  // Read this object's own matrix. Verified empirically against PDFium: for an
  // object reached through FPDFFormObj_GetObject, the object's own matrix is
  // reported in its LOCAL space and must be composed with the accumulated
  // transform to obtain PAGE space.
  FS_MATRIX local;
  std::memset(&local, 0, sizeof(local));
  const bool has_matrix = FPDFPageObj_GetMatrix(obj, &local) != 0;
  Matrix composed = accumulated;
  if (has_matrix) {
    const Matrix local_m{static_cast<double>(local.a),
                         static_cast<double>(local.b),
                         static_cast<double>(local.c),
                         static_cast<double>(local.d),
                         static_cast<double>(local.e),
                         static_cast<double>(local.f)};
    // Apply this object's own transform first, then everything above it.
    composed = Multiply(local_m, accumulated);
  }
  std::printf("%s", indent.c_str());
  PrintMatrix("accumulated matrix (PAGE)", composed);

  if (depth == 0) {
    std::printf("%s  source layer / OCG      = not available "
                "(no public PDFium API)\n",
                indent.c_str());
  }

  switch (type) {
    case FPDF_PAGEOBJ_PATH:
      ReportPath(obj);
      break;
    case FPDF_PAGEOBJ_IMAGE:
      ReportImage(page, obj);
      break;
    case FPDF_PAGEOBJ_FORM:
      // Children inherit the composed transform, not the bare parent matrix.
      ReportForm(page, obj, composed, depth);
      break;
    case FPDF_PAGEOBJ_TEXT: {
      std::printf("%s  text                    = see page-level TEXT "
                  "report (public API has no per-object text accessor)\n",
                  indent.c_str());
      break;
    }
    case FPDF_PAGEOBJ_SHADING:
      std::printf("%s  shading                 = no further public metadata "
                  "available\n",
                  indent.c_str());
      break;
    default:
      break;
  }
  std::printf("\n");
}

void ReportPageText(FPDF_PAGE page) {
  FPDF_TEXTPAGE text_page = FPDFText_LoadPage(page);
  if (text_page == nullptr) {
    std::printf("  TEXT: not available\n\n");
    return;
  }

  const int char_count = FPDFText_CountChars(text_page);
  std::printf("  TEXT: characters = %d\n", char_count);

  const int to_read = char_count < kMaxCharsReported ? char_count
                                                     : kMaxCharsReported;
  std::vector<unsigned short> buffer(static_cast<size_t>(to_read) + 1, 0);
  if (to_read > 0) {
    const int copied = FPDFText_GetText(text_page, 0, to_read, buffer.data());
    const int usable = copied > 0 ? copied : 0;
    const std::string text = Utf16LeToUtf8(buffer.data(), usable);
    std::printf("  TEXT: content (verbatim, not interpreted) = \"%s\"\n",
                Printable(text).c_str());
    std::printf("  TEXT: first character details:\n");
    double box[4] = {0.0, 0.0, 0.0, 0.0};  // left, right, bottom, top
    if (FPDFText_GetCharBox(text_page, 0, &box[0], &box[1], &box[2], &box[3])) {
      // PDFium returns (left, right, bottom, top). Reported as u/v to keep PAGE
      // coordinates distinct from SURVEY X/Y.
      std::printf("        char[0] bounds (PAGE space) = u_min=" NUM " v_min="
                      NUM " u_max=" NUM " v_max=" NUM "\n",
                  box[0], box[3], box[2], box[1]);
    }
    FS_MATRIX cm;
    std::memset(&cm, 0, sizeof(cm));
    if (FPDFText_GetMatrix(text_page, 0, &cm)) {
      std::printf("        char[0] matrix = [a=" NUM " b=" NUM " c=" NUM
                  " d=" NUM " e=" NUM " f=" NUM "]\n",
                  static_cast<double>(cm.a), static_cast<double>(cm.b),
                  static_cast<double>(cm.c), static_cast<double>(cm.d),
                  static_cast<double>(cm.e), static_cast<double>(cm.f));
    }
    std::printf("        char[0] font size = " NUM "\n",
                FPDFText_GetFontSize(text_page, 0));
    std::printf("        char[0] angle     = " NUM "\n",
                static_cast<double>(FPDFText_GetCharAngle(text_page, 0)));
  }
  if (to_read < char_count) {
    std::printf("  TEXT: ... %d further character(s) not shown (cap %d)\n",
                char_count - to_read, kMaxCharsReported);
  }
  FPDFText_ClosePage(text_page);
}

void ReportPage(FPDF_PAGE page, int index) {
  const int object_count = FPDFPage_CountObjects(page);

  std::printf("============================================================\n");
  std::printf("PAGE index                 = %d\n", index);
  std::printf("  page size (points)       = u=" NUM " v=" NUM "\n",
              static_cast<double>(FPDF_GetPageWidthF(page)),
              static_cast<double>(FPDF_GetPageHeightF(page)));
  std::printf("  page rotation            = %d degrees\n",
              FPDFPage_GetRotation(page));
  std::printf("  page object count        = %d\n", object_count);
  std::printf("  page has transparency    = %s\n",
              FPDFPage_HasTransparency(page) ? "true" : "false");
  std::printf("  source layer / OCG       = not available (no public PDFium "
              "API)\n");
  std::printf("------------------------------------------------------------\n");

  // PDFium's FPDFPage_GetObject hands back a page-owned handle. It is not
  // destroyed here. Nested form handles returned by FPDFFormObj_GetObject have
  // undocumented ownership and are likewise never destroyed.
  for (int i = 0; i < object_count; ++i) {
    FPDF_PAGEOBJECT obj = FPDFPage_GetObject(page, i);
    if (obj == nullptr) {
      std::printf("[obj %d] <null handle>\n\n", i);
      continue;
    }
    std::printf("[obj %d]\n", i);
    const Matrix identity;
    ReportObjectRecursive(page, obj, identity, 1);
  }

  ReportPageText(page);
  std::printf("\n");
}

void PrintUsage(const char* exe) {
  std::printf("usage: %s <input.pdf>\n", exe);
  std::printf("\n");
  std::printf("VP GeoConvert PDFium probe (research PoC).\n");
  std::printf("Reports PAGE-space geometry (u, v) only. No georeferencing.\n");
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 2) {
    std::fprintf(stderr, "error: expected exactly one argument\n\n");
    PrintUsage(argc > 0 ? argv[0] : "pdfium_probe");
    return 2;
  }

  const char* path = argv[1];

  std::printf("============================================================\n");
  std::printf("VP GeoConvert - PDFium probe\n");
  std::printf("============================================================\n");
  std::printf("document path             = %s\n", path);
  std::printf("coordinate domain         = PAGE (u, v)\n");
  std::printf("  (no SURVEY space, no georeferencing, no Z in this probe)\n");
  std::printf("============================================================\n\n");

  FPDF_InitLibrary();

  FPDF_DOCUMENT doc = FPDF_LoadDocument(path, nullptr);
  if (doc == nullptr) {
    const unsigned long err = FPDF_GetLastError();
    std::fprintf(stderr,
                 "error: could not open PDF '%s'\n"
                 "       FPDF_GetLastError() = %lu\n",
                 path, err);
    switch (err) {
      case FPDF_ERR_SUCCESS:
        std::fprintf(stderr, "       (no error reported)\n");
        break;
      case FPDF_ERR_UNKNOWN:
        std::fprintf(stderr, "       unknown error\n");
        break;
      case FPDF_ERR_FILE:
        std::fprintf(stderr, "       file not found or could not be opened\n");
        break;
      case FPDF_ERR_FORMAT:
        std::fprintf(stderr, "       file is not a valid PDF / bad format\n");
        break;
      case FPDF_ERR_PASSWORD:
        std::fprintf(stderr, "       password required or incorrect\n");
        break;
      case FPDF_ERR_SECURITY:
        std::fprintf(stderr, "       security / permission error\n");
        break;
      case FPDF_ERR_PAGE:
        std::fprintf(stderr, "       page not found\n");
        break;
      default:
        break;
    }
    FPDF_DestroyLibrary();
    return 4;
  }

  int file_version = 0;
  if (FPDF_GetFileVersion(doc, &file_version)) {
    std::printf("document file version     = %d\n", file_version);
  }
  const int page_count = FPDF_GetPageCount(doc);
  std::printf("document page count       = %d\n", page_count);

  for (int i = 0; i < page_count; ++i) {
    FPDF_PAGE page = FPDF_LoadPage(doc, i);
    if (page == nullptr) {
      std::fprintf(stderr,
                   "warning: could not load page %d (FPDF_GetLastError() = "
                   "%lu)\n",
                   i, FPDF_GetLastError());
      continue;
    }
    ReportPage(page, i);
    FPDF_ClosePage(page);
  }

  std::printf("============================================================\n");
  std::printf("probe finished\n");

  FPDF_CloseDocument(doc);
  FPDF_DestroyLibrary();
  return 0;
}