# Research benchmark harness

RESEARCH TOOLING ONLY. Nothing in this directory is the VP GeoConvert
production matching engine, and nothing here may be presented as one.

## Purpose

One canonical, reproducible, self-checking harness for measuring whether an
engineering PDF and a reference DXF share a geometric relationship, and how
well that relationship can be established from geometry alone.

It replaces the accumulated Set A / Set B1 / Set B2 research scripts, several
of which contained defects that had already invalidated results (see
`archive/README.md`).

## What this harness is not

It measures evidence. It does not decide.

* No production thresholds
* No confidence score
* No AUTO / MANUAL / UNSAFE policy
* No "X % is enough" or "Y mm is normal" assumption
* No generator-specific behaviour
* No verdict field

Candidate rows carry descriptive research labels, each accompanied by the
number that produced it, so a reader may weigh the measurements differently.

## Requirements

* Python 3.8+ , standard library only. No third-party packages.
* A built `poc/pdfium_probe` (PDFium). The harness reuses the approved probe
  and refuses to guess when it is missing: it does not reimplement PDF
  extraction, it raises a diagnostic error carrying build instructions.

Build the probe with:

```
call vcvars64.bat
cmake -S poc/pdfium_probe -B poc/pdfium_probe/build -DPDFium_DIR=<path-to-pdfium>
cmake --build poc/pdfium_probe/build --config Release
```

## How to run

```
python research/benchmark_runner.py <dataset-folder>
```

For example:

```
python research/benchmark_runner.py benchmark/Set_B2_Test
```

Useful options (all recorded verbatim in the output):

| option | meaning | default |
|---|---|---|
| `--tolerance` | matching tolerance in **PAGE points** | 1.5 |
| `--direction-tolerance` | line direction tolerance, degrees, modulo 180 | 1.0 |
| `--samples` | sample points per CAD segment | 11 |
| `--candidate-page-segments` | longest PAGE segments bucketed during search | 20000 |
| `--candidate-cad-segments` | longest CAD segments used for hypothesis fits | 200 |
| `--selectivity-sample` | spread CONTROL entities used for selectivity probes | 250 |
| `--random-transforms` | random wrong transforms for the floor | 40 |
| `--seed` | deterministic seed, recorded in the output | 20261007 |
| `--probe` | explicit probe executable path | auto-detected |
| `--work-dir` | where large probe stdout is written | system temp dir |
| `--results-root` | parent directory for output folders | `research/results` |

Run the self-tests with:

```
python -m unittest discover -s research/tests
```

## Управление длительными заданиями (Windows PowerShell)

Для расчётов дольше нескольких минут используйте независимый supervisor. Он
остаётся работать после закрытия OpenCode, хранит атомарный статус и не
допускает два одновременных runner для одного dataset.

```powershell
# Запуск (runner автоматически использует совместимый checkpoint)
python research\benchmark_job.py start benchmark\Set_B3_Test

# Текущий статус, операция, PID и прогресс N/M
python research\benchmark_job.py status benchmark\Set_B3_Test

# Последние события и ошибки
python research\benchmark_job.py errors benchmark\Set_B3_Test

# Resume, только если checkpoint действительно существует
python research\benchmark_job.py resume benchmark\Set_B3_Test

# Штатная остановка на границе пары после атомарной записи checkpoint
python research\benchmark_job.py stop benchmark\Set_B3_Test
```

Состояния: `STARTING`, `RUNNING`, `STALLED`, `COMPLETED`, `FAILED`,
`INTERRUPTED`. `STALLED` означает отсутствие наблюдаемого textual/CPU progress,
а не доказательство зависания. Автоматического restart нет. События всегда
пишутся в `research/results/<dataset>/.job/events.log`; на Windows дополнительно
предпринимается попытка показать уведомление. Короткая синтетическая проверка,
не использующая PDF/DXF:

```powershell
python research\benchmark_job.py self-test --no-notifications
```

Прямая ручная проверка desktop notification без запуска benchmark или
synthetic job:

```powershell
python research\benchmark_job.py notification-test
```

## Dataset layout

A flat folder containing any number of `*.pdf` and `*.dxf` files. File names
are treated as **opaque identifiers only** and are never used as evidence of
correspondence. Sub-folders are not searched.

Every page of every PDF is paired with every DXF. One DXF may match several
PDFs, one PDF may partially overlap several DXFs, and a PDF may have no usable
reference. The matrix never stops early.

## Coordinate domains

Kept strictly separate, and never merged:

| domain | symbols | notes |
|---|---|---|
| PDF raw/object | `p, q` | read only to interpret an object matrix; never used as geometry |
| PDF PAGE | `u, v` | PAGE points; the matching tolerance lives here |
| DXF CAD | `cad_x, cad_y` | DXF native units, declared by `$INSUNITS` or undeclared |
| VP SURVEY | `X = Northing`, `Y = Easting` | **not used**; the CAD to SURVEY mapping is not established here |

`cad_x` is never asserted to equal SURVEY `X`. No geographic assumption is
made anywhere.

Strictly 2D: DXF group codes 30-39 are never read, and no elevation is
computed, transformed or inferred.

Geometry objects carry their domain and reject construction in the wrong one,
so a PAGE value cannot silently be treated as a CAD value or vice versa.

## Units

The unit is what the file **declares** via `$INSUNITS`. Nothing is inferred
from the magnitude of the coordinates. A drawing whose coordinates look like
millimetres while declaring metres is reported as declaring metres, and the
fact is left visible.

When units are not declared, residuals stay in raw CAD units and no mm or m
label is emitted: `threshold_pass_rates` returns `None` rather than inventing
a millimetre threshold.

Unit conversion is a reporting concern only. It never enters the transform,
the matching criterion, or any hypothesis decision.

## Output

Written to `research/results/<dataset-name>/`, never into the input folder:

| file | contents |
|---|---|
| `inventory.json` | forensics and provenance, including input SHA-256, PDF structural markers, DXF entity inventory, declared units |
| `matrix.json` | every measured quantity for every candidate pair |
| `report.md` | human-readable rendering of the JSON, with descriptive labels |
| `diagnostics.json` | harness version, parameters, seeds, environment, timings |

During a run, `checkpoint.json` is atomically replaced after every completed
PDF-page × DXF pair. A rerun automatically resumes it only when harness
version, all parameters and every input SHA-256 match. A mismatch is a hard
error; `--restart` is the explicit operator action that discards the
checkpoint. The checkpoint is removed only after all final artifacts are
written successfully.

Large probe stdout (tens of megabytes per drawing) goes to a temporary work
directory outside the repository and is not written into the results
directory. `research/results/` is git-ignored.

`matrix.json` is the primary artefact: it holds enough detail to compare later
benchmark stages without re-parsing probe output.

## Reproducibility

The same dataset plus the same harness version produces the same numbers.

* No uncontrolled randomness. The default CONTROL/CHECK split consumes no
  randomness at all; the seed exists for the selectivity probes and for future
  randomised strategies, and is recorded in the output.
* The blind search is deterministic: it iterates sorted structures and fixed
  index ranges.
* Recorded in `diagnostics.json`: harness version, timestamp, Python version,
  platform, probe executable path, every parameter, the seed, and input
  SHA-256 values from `inventory.json`.

## Method summary

1. Discover PDFs and DXFs; run the PDFium probe and the structural scan.
2. Parse DXF geometry into entity records.
3. Split entities into CONTROL and CHECK **once**, before any hypothesis
   exists, at entity level, spatially distributed, deterministically.
4. Blind search, CONTROL segments only, for both reflection hypotheses: scan
   the data-derived scale range with overlapping windows and an overlapping
   rotation scan, bucket candidates by length and direction, produce exact
   2-point fits, filter them by agreement with the bucket hypothesis, then
   cluster the fits in (log scale, rotation, translation).
5. Freeze the CONTROL-selected cluster representative. There is deliberately
   no midpoint "refit": deriving PAGE midpoints with the inverse of the same
   transform is circular and returns that transform by construction. A future
   refit requires explicit, independently established correspondences.
6. Touch CHECK once, to measure.
7. Measure selectivity: translations, rotations and scales in multiples of the
   match tolerance, plus axis swap, opposite reflection, 90 degree rotation,
   and a floor of random wrong transforms, all on a **spread** CONTROL sample.
8. Compute residuals in CAD domain, with a millimetre view only when units are
   declared.
9. Emit descriptive research labels.

## Known limitations

* **The probe bounds printed geometry.** It prints a limited number of
  segments per path. The harness records the truncation count, so PAGE geometry
  is a lower bound, not a complete picture.
* **No affine diagnostic.** A similarity is never replaced by a more flexible
  model. An affine variant is absent by design.
* **Curves are not matching features.** CIRCLE, ARC, ELLIPSE and HATCH are
  inventoried with their centre and radius but are not used as matching
  segments. Bezier primitives in the PDF are counted but never converted into
  arcs or circles.
* **OCG status is three-valued.** `PRESENT`, `ABSENT` or `NOT_VERIFIED`. The
  byte-level scan cannot see inside an object stream or an uncompressed
  cross-reference stream, so absence found by the scan is `NOT_VERIFIED`. An
  earlier stage reported "no OCG" from exactly that reasoning and was wrong.
* **Unmatched geometry is not classified.** Whether an unmatched PDF feature
  indicates a revision difference, clipping, or a discipline absent from the
  DXF is not decidable by geometry alone and is left as `UNRESOLVED`.
* **No CAD to SURVEY mapping**, by design.
* **No INSERT expansion.** Block references are inventoried, not expanded.
* **Single geometry class per version.** Straight segments only.

## Layout

```
research/
  benchmark_runner.py     CLI entry point
  benchmark/
    __init__.py           harness version
    models.py             explicit research data structures
    units.py              $INSUNITS handling, isolated because unit mistakes
                          silently corrupt every residual number
    geometry.py           PAGE segment index + the correspondence criterion
    similarity.py         canonical 2D similarity (Helmert) fit and application
    matching.py           blind search and explicit MatchConfig
    split.py              entity-level CONTROL/CHECK, spread sampling,
                          cell coverage, layer and type statistics
    validation.py         CAD-domain residuals and threshold tables
    selectivity.py        perturbation and false-match defence
    dxf_extract.py        read-only DXF parser
    pdf_extract.py        probe runner and stdout parser
    forensics.py          structural PDF scan, three-valued OCG status
    matrix.py             full N x M orchestration
    crossref.py           candidate comparison within one PDF
    reporting.py          JSON and Markdown writers
  tests/
    test_harness.py       stdlib unittest self-tests
  archive/                superseded research scripts, kept as a record
```

## Historical record

`archive/` holds the superseded Set A / Set B1 / Set B2 scripts. They are not
importable by the harness and exist only so the path taken is readable. Their
defects are catalogued in `archive/README.md`. Do not import from `archive/`
into new work: several of those functions are known to be unsafe.

The file-level correction to the Set B2 OCG conclusion is recorded separately
in [`OCG_FINDINGS.md`](OCG_FINDINGS.md), including input SHA-256 values, exact
byte offsets and a read-only reproduction command.
