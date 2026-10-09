#!/usr/bin/env python3
"""Canonical VP GeoConvert research benchmark harness.

RESEARCH TOOLING ONLY. Not the VP GeoConvert production matching engine.

Usage
-----
    python research/benchmark_runner.py <dataset-folder> [options]

Example
-------
    python research/benchmark_runner.py benchmark/Set_B2_Test

The dataset folder is scanned for *.pdf and *.dxf. File names are treated as
opaque identifiers and are never used as evidence of correspondence. Every
page x dxf combination is evaluated; nothing stops early.

Outputs (under research/results/<dataset-name>/, never inside the input
folder):
    inventory.json   forensics and provenance, including input SHA-256
    matrix.json      every measured quantity for every candidate pair
    report.md        human-readable rendering of the JSON
    diagnostics.json run metadata, parameters, seeds, timings

Large probe stdout goes to a temporary work directory outside the repository
and is never written into the results directory.

Requirements
------------
    * Python 3.8+ standard library only. No third-party packages.
    * A built poc/pdfium_probe (PDFium). The harness reuses it and refuses to
      guess; it does not reimplement PDF extraction.
"""

import argparse
import atexit
import ctypes
import datetime
import hashlib
import json
import os
import platform
import sys
import tempfile
import time
from typing import List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from benchmark import HARNESS_VERSION                                # noqa: E402
from benchmark.crossref import build_cross_reference                 # noqa: E402
from benchmark.dxf_extract import load_dxf                           # noqa: E402
from benchmark.matrix import (BenchmarkStopped, discover,
                              run_matrix)                            # noqa: E402
from benchmark.matching import MatchConfig                           # noqa: E402
from benchmark.models import Dataset, UnitInfo                       # noqa: E402
from benchmark.pdf_extract import (ProbeUnavailable, build_pdf_document,
                                   locate_probe)                     # noqa: E402
from benchmark.reporting import (render_report, write_inventory,
                                 write_matrix, write_report)         # noqa: E402

REPO_ROOT = os.path.dirname(HERE)
RESULTS_ROOT = os.path.join(HERE, "results")


def _configure_console() -> None:
    """Avoid UnicodeEncodeError for diagnostics on legacy Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) \
                == 0x00000102
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_runner_lock(out_dir: str) -> str:
    """Prevent two benchmark_runner instances for the same dataset."""
    job_dir = os.path.join(out_dir, ".job")
    os.makedirs(job_dir, exist_ok=True)
    path = os.path.join(job_dir, "runner.lock")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            with open(path, "r", encoding="ascii") as fh:
                pid = int(fh.read().strip())
        except (OSError, ValueError):
            pid = -1
        if _pid_exists(pid):
            raise RuntimeError("benchmark_runner уже выполняется для dataset "
                               "(PID %d)" % pid)
        os.unlink(path)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(str(os.getpid()))
    atexit.register(lambda: os.path.exists(path) and os.unlink(path))
    return path


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Research benchmark harness for VP GeoConvert "
                    "(measurements only, not production logic)")
    p.add_argument("dataset", help="folder containing PDF and DXF files")
    p.add_argument("--results-root", default=RESULTS_ROOT,
                   help="parent directory for output folders "
                        "(default: research/results)")
    p.add_argument("--probe", default=None,
                   help="explicit path to the pdfium_probe executable")
    p.add_argument("--tolerance", type=float, default=1.5,
                   help="matching tolerance in PAGE points (default 1.5)")
    p.add_argument("--direction-tolerance", type=float, default=1.0,
                   help="direction tolerance in degrees (default 1.0)")
    p.add_argument("--samples", type=int, default=11,
                   help="sample points per segment (default 11)")
    p.add_argument("--candidate-page-segments", type=int, default=20000,
                   help="longest PAGE segments considered for bucketing")
    p.add_argument("--candidate-cad-segments", type=int, default=200,
                   help="longest CAD segments considered for hypothesis fits")
    p.add_argument("--selectivity-sample", type=int, default=250,
                   help="spread CONTROL entities used for selectivity probes")
    p.add_argument("--random-transforms", type=int, default=40,
                   help="number of random wrong transforms for the floor")
    p.add_argument("--seed", type=int, default=20261007,
                   help="deterministic seed, recorded in the output")
    p.add_argument("--work-dir", default=None,
                   help="directory for large probe stdout "
                        "(default: a system temp directory)")
    p.add_argument("--quiet", action="store_true", help="suppress progress")
    p.add_argument("--restart", action="store_true",
                   help="discard a compatible/incompatible checkpoint and "
                        "restart the matrix (input files are never touched)")
    p.add_argument("--stop-file", default=None, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def _checkpoint_fingerprint(dataset: Dataset, config: MatchConfig,
                            selectivity_sample: int,
                            random_transform_count: int) -> str:
    payload = {
        "harness_version": HARNESS_VERSION,
        "dataset": dataset.provenance(),
        "parameters": config.to_dict(),
        "selectivity_sample": selectivity_sample,
        "random_transform_count": random_transform_count,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                     default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _atomic_json(path: str, value: dict) -> None:
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=1, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temporary, path)


def main(argv: Optional[List[str]] = None) -> int:
    _configure_console()
    args = parse_args(argv)
    started = time.time()
    started_utc = datetime.datetime.now(
        datetime.timezone.utc).isoformat(timespec="seconds")

    def log(message: str) -> None:
        if not args.quiet:
            print(message, flush=True)

    dataset_folder = os.path.abspath(args.dataset)
    if not os.path.isdir(dataset_folder):
        print("ERROR: dataset folder not found: %s" % dataset_folder,
              file=sys.stderr)
        return 2

    dataset_name = os.path.basename(dataset_folder.rstrip("\\/")) or "dataset"
    out_dir = os.path.join(os.path.abspath(args.results_root), dataset_name)
    os.makedirs(out_dir, exist_ok=True)
    try:
        _acquire_runner_lock(out_dir)
    except RuntimeError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 7

    work_dir = args.work_dir or tempfile.mkdtemp(prefix="vpgeo-bench-")
    os.makedirs(work_dir, exist_ok=True)
    log("dataset    : %s" % dataset_folder)
    log("results    : %s" % out_dir)
    log("work dir   : %s  (large probe stdout, outside the repository)"
        % work_dir)

    config = MatchConfig(tolerance_points=args.tolerance,
                         direction_tolerance_deg=args.direction_tolerance,
                         samples=args.samples,
                         candidate_page_segments=args.candidate_page_segments,
                         candidate_cad_segments=args.candidate_cad_segments,
                         random_seed=args.seed)

    pdf_paths, dxf_paths, skipped = discover(dataset_folder)
    log("discovered : %d PDF, %d DXF (%d files skipped)"
        % (len(pdf_paths), len(dxf_paths), len(skipped)))
    if not pdf_paths and not dxf_paths:
        print("ERROR: no PDF or DXF files found in %s" % dataset_folder,
              file=sys.stderr)
        return 3

    try:
        probe_exe = locate_probe(REPO_ROOT, args.probe)
    except ProbeUnavailable as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 4
    log("probe      : %s" % probe_exe)

    dataset = Dataset(folder=dataset_folder, name=dataset_name,
                      skipped_files=skipped)

    for path in pdf_paths:
        t0 = time.time()
        log("probing    : %s" % os.path.basename(path))
        try:
            doc = build_pdf_document(path, probe_exe, work_dir)
        except ProbeUnavailable as exc:
            print("ERROR: %s" % exc, file=sys.stderr)
            return 5
        dataset.pdfs.append(doc)
        log("  pages %d, LINE primitives %d, straight PAGE segments %d "
            "(%.1f s)" % (doc.page_count,
                          sum(p.line_count for p in doc.pages),
                          sum(len(p.segments) for p in doc.pages),
                          time.time() - t0))

    for path in dxf_paths:
        t0 = time.time()
        doc = load_dxf(path)
        dataset.dxfs.append(doc)
        log("parsed dxf : %s  entities %d (with straight geometry %d), "
            "units %s%s (%.1f s)"
            % (doc.name, len(doc.entities), len(doc.matching_entities()),
               doc.units.name, "" if doc.units.declared else " NOT declared",
               time.time() - t0))

    checkpoint_path = os.path.join(out_dir, "checkpoint.json")
    fingerprint = _checkpoint_fingerprint(
        dataset, config, args.selectivity_sample, args.random_transforms)
    recovered = {}
    if args.restart and os.path.exists(checkpoint_path):
        os.unlink(checkpoint_path)
        log("checkpoint : discarded by --restart")
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        if saved.get("fingerprint") != fingerprint:
            print("ERROR: checkpoint does not match this harness version, "
                  "parameters, or input hashes. Re-run with --restart only "
                  "after reviewing the mismatch: %s" % checkpoint_path,
                  file=sys.stderr)
            return 6
        from benchmark.models import CandidateEvidence
        recovered = {key: CandidateEvidence.from_dict(value)
                     for key, value in saved.get("candidates", {}).items()}
        log("checkpoint : resumed %d completed combination(s)"
            % len(recovered))

    checkpoint_doc = {
        "harness_version": HARNESS_VERSION,
        "fingerprint": fingerprint,
        "parameters": config.to_dict(),
        "dataset": dataset.provenance(),
        "candidates": {key: value.to_dict()
                       for key, value in recovered.items()},
    }

    def save_checkpoint(pair_id, evidence):
        checkpoint_doc["candidates"][pair_id] = evidence.to_dict()
        _atomic_json(checkpoint_path, checkpoint_doc)

    # Establish the run identity before the first expensive pair. Atomic
    # replacement means an interruption can leave either the previous complete
    # checkpoint or this complete new one, never a partially written JSON file.
    _atomic_json(checkpoint_path, checkpoint_doc)

    log("matrix     : %d combination(s) expected (%d already checkpointed)"
        % (dataset.n_pairs, len(recovered)))
    try:
        results = run_matrix(
            dataset, config,
            selectivity_sample=args.selectivity_sample,
            random_transform_count=args.random_transforms,
            progress=log, existing=recovered,
            checkpoint=save_checkpoint,
            should_stop=(lambda: bool(args.stop_file)
                         and os.path.exists(args.stop_file)))
    except BenchmarkStopped as exc:
        log("interrupted: %s; checkpoint retained" % exc)
        return 130
    log("matrix     : %d combination(s) evaluated, no early exit"
        % len(results))

    inventory = write_inventory(os.path.join(out_dir, "inventory.json"),
                                dataset, config, probe_exe)
    extra = {
        "pair_count_expected": dataset.n_pairs,
        "checkpoint_pairs_resumed": len(recovered),
        "work_dir": work_dir,
        "probe_executable": probe_exe,
    }
    write_matrix(os.path.join(out_dir, "matrix.json"), dataset, results,
                 config, extra)

    units_by_dxf = {d.name: d.units for d in dataset.dxfs}
    run_meta = {
        "started_utc": started_utc,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "probe_executable": probe_exe,
        "seed": str(args.seed),
        "work_dir": work_dir,
    }
    report = render_report(dataset, results, config, inventory, units_by_dxf,
                           cross=build_cross_reference(results),
                           run_meta=run_meta)
    write_report(os.path.join(out_dir, "report.md"), report)

    diagnostics = {
        "harness_version": HARNESS_VERSION,
        "environment": inventory["environment"],
        "parameters": config.to_dict(),
        "run": run_meta,
        "wall_time_seconds": round(time.time() - started, 1),
        "probe_work_dir": work_dir,
        "probe_stdout_is_temporary": not bool(args.work_dir),
        "pairs_expected": dataset.n_pairs,
        "pairs_evaluated": len(results),
        "checkpoint_pairs_resumed": len(recovered),
        "skipped_files": skipped,
    }
    with open(os.path.join(out_dir, "diagnostics.json"), "w",
              encoding="utf-8") as fh:
        json.dump(diagnostics, fh, indent=1, default=str)

    # Final artifacts are safely present; the checkpoint is no longer needed.
    if os.path.exists(checkpoint_path):
        os.unlink(checkpoint_path)

    log("wrote      : %s" % ", ".join(sorted(os.listdir(out_dir))))
    log("done       : %.1f s" % (time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
