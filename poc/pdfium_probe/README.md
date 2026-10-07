# pdfium_probe — VP GeoConvert PDFium proof of concept

Research probe, not production code. It exists to answer one question:

> Can PDFium's **public API** expose engineering vector geometry from a real
> Civil 3D / OpenRoads vector PDF with enough fidelity to justify building the
> real VP PDF Vector Extractor on top of it?

It deliberately contains no GUI, no DXF writer, no georeferencing, no QA
subsystem and no production architecture.

## Coordinate model

This stage has **PAGE coordinates only**.

```
u = PDF/page horizontal coordinate
v = PDF/page vertical coordinate
```

PDF/page coordinates are never named `X` or `Y`. There is no SURVEY space, no
georeferencing, no Helmert/affine transformation and no `Z` anywhere in this
program. See `AGENTS.md` and `docs/GEOMETRY_MODEL.md`.

## Geometry policy

Raw PDF path primitives are reported exactly as PDFium reports them:

```
MOVE   (FPDF_SEGMENT_MOVETO)
LINE   (FPDF_SEGMENT_LINETO)
CUBIC_BEZIER (FPDF_SEGMENT_BEZIERTO)
CLOSE  (FPDFPathSegment_GetClose)
```

No `ARC`, `CIRCLE`, `POLYLINE` or other engineering primitive is recognised.
Recognition is a separate, later stage and is out of scope here.

## Prerequisites

| Requirement | Detail |
| --- | --- |
| OS | Windows x64 |
| Compiler | MSVC (Visual Studio 2022 Build Tools, v143 x64 toolset) |
| Build system | CMake 3.21 or newer |
| PDFium | Prebuilt Windows x64 package, see below |

### PDFium version and provenance

| Field | Value |
| --- | --- |
| Version | **157.0.8086.0** |
| Upstream tag | `chromium/8086` |
| Upstream project | PDFium, <https://pdfium.googlesource.com/pdfium/> |
| Binary distribution | <https://github.com/bblanchon/pdfium-binaries> (not affiliated with Google or Foxit) |
| Asset | `pdfium-win-x64.tgz` |
| Archive SHA-256 | `1FD8AF952832DBB0EB16D9249F68FE09E5F5EBF7C3DD9F6066EA2720CC28487D` |
| Build configuration | `is_debug=false`, `pdf_enable_v8=false`, `pdf_enable_xfa=false`, `pdf_is_standalone=true`, `target_cpu=x64`, `target_os=win` |
| Install location used here | `C:\Sandbox\pdfium-deps\pdfium` |

The package is pinned to a specific release tag rather than `latest`, and the
archive hash is recorded so the build can be reproduced and re-verified.

`pdf_enable_xfa=false` matters: the well-known XFA patent area is not compiled
into this build. `pdf_enable_v8=false` means no JavaScript engine is bundled.

### Licence audit

The distribution ships its own third-party licence inventory in `licenses/`.
Every component of the shipped `pdfium.dll` was identified:

| Component | Licence |
| --- | --- |
| PDFium | BSD-3-Clause |
| `pdfium-binaries` packaging | MIT |
| Abseil | Apache-2.0 |
| Anti-Grain Geometry 2.3 | BSD-style |
| dragonbox | Apache-2.0 WITH LLVM-exception, or BSL-1.0 (both permissive) |
| fast_float | MIT |
| FreeType | FreeType Project License (FTL) — **dual-licensed FTL/GPL-2.0 upstream, FTL selected** |
| HarfBuzz | MIT ("Old MIT") |
| ICU | Unicode License V3 |
| Little CMS | MIT |
| libjpeg-turbo | BSD-style (IJG + BSD, mutually compatible) |
| LLVM libc | Apache-2.0 WITH LLVM-exception |
| OpenJPEG | BSD-2-Clause |
| libpng | PNG Reference Library License v2 |
| simdutf | MIT |
| zlib | zlib licence |

**No GPL, LGPL, AGPL, MPL or unknown-licensed component is shipped in this
build.** FreeType and libjpeg-turbo are dual-licensed upstream; the permissive
option is the one compiled in, and this must be re-confirmed against
`THIRD_PARTY_NOTICES.md` before any distributable release.

`pdfium.dll` imports only Windows system libraries — `kernel32.dll`,
`user32.dll`, `gdi32.dll`, `advapi32.dll`. Its CRT is statically linked, so no
Microsoft Visual C++ redistributable is required.

Per `THIRD_PARTY_NOTICES.md`, **no PDF engine or dependency is approved for
distribution** until the concrete version/build and its complete redistributed
dependency set have passed the release licence audit. That audit has been done
for this PoC only and is not a distribution approval.

## Build

```
cmake -S . -B build -DPDFium_DIR=C:/Sandbox/pdfium-deps/pdfium
cmake --build build --config Release
```

Visual Studio generator builds produce `build/Release/pdfium_probe.exe`; single
configuration generators produce `build/pdfium_probe.exe`.

## Run

```
pdfium_probe.exe <input.pdf>
```

Diagnostics go to stdout. Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Probe completed |
| 2 | Wrong number of command-line arguments |
| 4 | PDFium refused to open the file. `FPDF_GetLastError()` is printed and mapped: `2` file not found or unreadable (also reported when the path is a directory), `3` not a valid PDF, bad format or truncated, `4` password required or incorrect, `5` unsupported security scheme |

PDFium's own `FPDF_ERR_FILE` does not distinguish "does not exist" from "exists
but is not a PDF". The probe therefore treats a missing file as an open failure
and reports the engine's error code rather than inventing a distinction.

## What the probe reports

For the document: path and page count.

For each page: index, size in points (`u`, `v`), rotation, page object count,
transparency flag.

For each page object: index, type, bounds, object matrix, active state. Then,
depending on type:

- **PATH** — segment count and every segment with its primitive type, `u`/`v`
  coordinates and the close flag.
- **IMAGE** — pixel dimensions, bits per pixel, resolution, colourspace,
  marked-content id when available.
- **FORM** — recursive traversal into the form's content stream with the
  composed ("accumulated") matrix reported at every level.
- **TEXT** — per-object bounds and matrix. Page-level text content, character
  count, and first-character position, matrix, font size and angle.
- **SHADING** — bounds and matrix only.

Per-object output is capped so that a real engineering drawing stays readable.
Caps are `64` segments per path and `200` characters per page. Totals are always
reported exactly; only detail is truncated, and truncation is stated explicitly
in the output.

## PDFium public API actually used

Library and document:

```
FPDF_InitLibrary          FPDF_DestroyLibrary
FPDF_LoadDocument         FPDF_GetLastError
FPDF_GetPageCount         FPDF_GetFileVersion
```

Page:

```
FPDF_LoadPage             FPDF_ClosePage       FPDF_CloseDocument
FPDF_GetPageWidthF        FPDF_GetPageHeightF
FPDFPage_GetRotation      FPDFPage_HasTransparency
FPDFPage_CountObjects     FPDFPage_GetObject
```

Page object:

```
FPDFPageObj_GetType       FPDFPageObj_GetBounds
FPDFPageObj_GetMatrix     FPDFPageObj_GetIsActive
```

Path:

```
FPDFPath_CountSegments    FPDFPath_GetPathSegment
FPDFPathSegment_GetType   FPDFPathSegment_GetPoint
FPDFPathSegment_GetClose
```

Form XObject:

```
FPDFFormObj_CountObjects  FPDFFormObj_GetObject
```

Text:

```
FPDFText_LoadPage         FPDFText_ClosePage    FPDFText_CountChars
FPDFText_GetText          FPDFText_GetCharBox   FPDFText_GetMatrix
FPDFText_GetFontSize      FPDFText_GetCharAngle
```

Image:

```
FPDFImageObj_GetImageMetadata
```

All of the above were confirmed present in the public headers **and** exported
from the shipped `pdfium.dll`.

## Known limitations of the public API

These are properties of PDFium's public interface, verified against the shipped
headers. They are recorded rather than worked around.

### 1. No OCG / layer information at all

The strings `OCG`, `OptionalContent` and `Layer` do not occur in any public
header, and no optional-content accessor is exported. PDFium's public API
exposes **no layer/OCG information**, so the probe prints
`not available (no public PDFium API)` instead of inventing a layer assignment.

### 2. Cubic Bézier segments expose only one point

`FPDFPathSegment_GetPoint` returns exactly one `(u, v)` pair per segment. A
cubic Bézier needs three points (two control points and an endpoint), so the
control points and endpoint of a `CUBIC_BEZIER` **cannot be fully reconstructed
from the public API**. The probe reports the one available point and prints a
`NOTE:` line for every such segment.

This is the most significant fidelity finding of the PoC and needs to be
resolved before committing to PDFium for engineering geometry. Options to
investigate later: reading the content stream at a lower level, or accepting
reduced Bézier fidelity.

### 3. Coordinate precision is limited by the API, not by this program

`FPDFPathSegment_GetPoint` returns `float`, not `double`. The internal VP
geometry model is specified as 64-bit double precision, but full double
precision **cannot be recovered through this API**. The probe stores values in
`double` and prints with `%.9g`, which is lossless for a `float`, so the probe
adds no rounding of its own.

### 4. FORM traversal works; accumulated composition verified only synthetically

`FPDFFormObj_CountObjects` and `FPDFFormObj_GetObject` exist and are exported, so
recursive FORM traversal **is** possible. They are not labelled "Experimental
API" in the headers, unlike neighbouring entry points.

The headers do not state whether returned objects are in the form's local space
or already in page space. The probe treats them as local and composes each
object's own matrix into an accumulated matrix.

This composition was **verified against synthetic PDFs only**, and the result
was counter-intuitive in a way that needs real-data confirmation. A form with
`/Matrix[1 0 0 1 10 5]` containing a path whose own matrix is
`[1 0 0 1 0 0]` yielded an accumulated matrix of `[a=1 b=0 c=0 d=1 e=10 f=5]`,
consistent with composition. A form with `/Matrix[0.5 0 0 0.5 100 100]`
containing an identity-matrix path yielded `[a=0.5 b=0 c=0 d=0.5 e=100 f=100]`,
also consistent. Three-level nesting was traversed successfully.

**This is NOT VERIFIED against real Civil 3D / OpenRoads drawings.** Real
drawings commonly nest forms far more deeply and with clipping and transparency
groups that this probe does not model.

Ownership of handles returned by `FPDFFormObj_GetObject` is undocumented. The
probe never destroys page or form child handles; only page-level handles from
`FPDF_LoadPage` are closed.

### 4a. The page object list is not the whole page content

On synthetic input, `FPDFPage_CountObjects` returned **zero** page objects for a
page whose content stream consisted only of a bare cubic Bézier curve
(`100 30 120 40 140 50 160 60 c S`). Adding a leading `m` (moveto) made the
same geometry appear as a PATH object with its segments.

Separately, a content stream containing a stroked line path followed by a
stroked Bézier path reported **one** object containing only the line segments;
the Bézier subpath was absent from the object list.

The cause is most likely that PDFium's `FPDFPage_CountObjects` reflects a parsed
object tree rather than a literal flattening of the content stream, and may
merge, reorder or drop content. **This must be investigated with a real
engineering drawing before PDFium can be relied upon for complete extraction.**
It is a more serious risk than the Bézier precision issue, because content can
be missing rather than merely less precise.

### 5. No per-object text accessor

Text objects expose bounds and matrix, but text content is only available
page-wide through `FPDFText_*`. Mapping individual characters back to the
specific `FPDF_PAGEOBJ_TEXT` object that produced them is not provided by the
public API.

### 6. `FS_Matrix` helpers are not exported

`FS_Matrix::Multiply` and friends are internal. The probe implements its own
affine composition matching the documented `FS_MATRIX` convention, because the
accumulated-transform logic is needed regardless.

## Verification status

- `IMPLEMENTED` — the probe is written.
- `BUILD VERIFIED` — clean configure and build from an empty build directory,
  MSVC 19.44 / VS 2022 17.14.41, Windows SDK 10.0.26100.0, CMake 4.4.4. No
  warnings, no errors.

  Note on reproducibility: the *build configuration* is reproducible and pinned,
  but the produced `.exe` is **not bit-identical** between two clean rebuilds
  (`SHA-256` differed). MSVC embeds a build timestamp in the PE header by
  default. Bit-identical output would require a deterministic-build flag that
  this PoC deliberately does not add. Verify behaviour, not the binary hash.
- `RUNTIME VERIFIED` — the following were actually executed:

  | Case | Exit | `FPDF_GetLastError()` |
  | --- | --- | --- |
  | no argument | 2 | — |
  | two arguments | 2 | — |
  | nonexistent file | 4 | 2 |
  | directory as input | 4 | 2 |
  | non-PDF text file | 4 | 3 |
  | empty file | 4 | 3 |
  | truncated PDF | 4 | 3 |

  Positive extraction paths were also executed against **synthetic PDFs written
  by hand for this purpose**: PATH with `MOVE`/`LINE`/`CLOSE` (the `CLOSE` flag
  is correctly reported `close=true` on the closing segment), PATH with
  `CUBIC_BEZIER`, TEXT (character count, verbatim content, per-character bounds,
  matrix, font size, angle), IMAGE (pixel size, bits per pixel, resolution,
  colourspace), and FORM traversal to three levels deep.

  The synthetic inputs were constructed only to prove the code paths execute.
  They prove nothing about real drawing fidelity.

- `TESTED WITH REAL DATA` — **NOT VERIFIED.** No engineering vector PDF has been
  supplied to the repository; `**/*.pdf` matches nothing. No third-party PDF was
  downloaded and passed off as an engineering benchmark. Nothing here may be
  read as evidence that PDFium extracts Civil 3D / OpenRoads geometry with
  sufficient fidelity.
- `NOT VERIFIED` — the limitations above, in particular the missing-object-count
  behaviour in section 4a, the Bézier single-point issue, and the form matrix
  composition against real drawings.

## What the PoC has and has not established

Established on synthetic input:

- PDFium opens PDFs, enumerates pages and page objects, and reports object type,
  bounds and matrix through its public API.
- Raw path primitives `MOVE`, `LINE`, `CUBIC_BEZIER` and `CLOSE` are obtainable,
  including the close flag.
- Text, image metadata and recursive form traversal are all reachable.
- The shipped binaries and this probe need only Windows system DLLs.

**Not established, and still open:**

- Whether real Civil 3D / OpenRoads vector PDFs yield complete and faithful
  geometry.
- Whether `FPDFPage_CountObjects` omits real drawing content (see 4a).
- Whether Bézier fidelity loss is acceptable or must be solved another way.
- Whether OCG/layer information is obtainable by any route. If it proves to be
  essential, PDFium's public API cannot currently supply it and that is an
  architectural decision to make before building the real extractor.

## Licence note for this directory

This PoC and VP GeoConvert's own code are intended for a permissive open-source
licence. PDFium is a separate, third-party BSD-3-Clause component consumed as an
external build dependency and is never committed to this repository.