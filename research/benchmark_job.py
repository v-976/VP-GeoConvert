#!/usr/bin/env python3
"""Independent controller/monitor for long research benchmark jobs.

The supervisor is a detached Python process, not an OpenCode shell child. It
starts benchmark_runner.py, records atomic state, watches both textual progress
and worker CPU time, and preserves the runner checkpoint on interruption.

RESEARCH TOOLING ONLY. No production code and no input file is modified.
"""

import argparse
import ctypes
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
RESULTS_ROOT = HERE / "results"
ACTIVE_STATES = {"STARTING", "RUNNING", "STALLED"}


def _configure_console() -> None:
    """Keep Russian diagnostics usable under legacy Windows code pages."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=1, ensure_ascii=False, allow_nan=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(temporary), str(path))


def _read_json(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _dataset_name(folder: str) -> str:
    return Path(folder).resolve().name or "dataset"


def job_paths(dataset: str, results_root: str = str(RESULTS_ROOT)) -> Dict[str, Path]:
    root = Path(results_root).resolve() / _dataset_name(dataset) / ".job"
    return {
        "root": root,
        "state": root / "state.json",
        "log": root / "benchmark.log",
        "events": root / "events.log",
        "lock": root / "active.lock",
        "stop": root / "stop.request",
    }


def process_exists(pid: Optional[int]) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) \
                == 0x00000102  # WAIT_TIMEOUT: process still running
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def process_cpu_seconds(pid: int) -> Optional[float]:
    """Kernel + user CPU seconds, using only Win32/stdlib facilities."""
    if os.name != "nt":
        return None
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    creation = ctypes.c_ulonglong()
    exit_time = ctypes.c_ulonglong()
    kernel = ctypes.c_ulonglong()
    user = ctypes.c_ulonglong()
    try:
        ok = ctypes.windll.kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exit_time),
            ctypes.byref(kernel), ctypes.byref(user))
        if not ok:
            return None
        return (kernel.value + user.value) / 10_000_000.0
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _append_event(paths: Dict[str, Path], level: str, message: str) -> None:
    paths["root"].mkdir(parents=True, exist_ok=True)
    with paths["events"].open("a", encoding="utf-8") as fh:
        fh.write("%s [%s] %s\n" % (utc_now(), level, message))


def _notify(paths: Dict[str, Path], title: str, message: str,
            enabled: bool = True) -> None:
    _append_event(paths, title, message)
    if not enabled or os.name != "nt":
        return
    caption = "VP GeoConvert — %s" % title
    msg_exe = shutil.which("msg.exe")
    if msg_exe:
        try:
            result = subprocess.run(
                [msg_exe, "*", "%s: %s" % (caption, message)], timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode == 0:
                _append_event(paths, "NOTICE",
                              "notification dispatched via msg.exe")
                return
            _append_event(paths, "NOTICE",
                          "msg.exe failed with exit code %d"
                          % result.returncode)
        except (OSError, subprocess.SubprocessError) as exc:
            _append_event(paths, "NOTICE", "msg.exe failed: %s" % exc)

    # Windows Home installations may not provide msg.exe. WScript.Shell is a
    # built-in Windows COM component; launching it through Windows PowerShell
    # keeps the monitor dependency-free and targets the same interactive user
    # session. Popup dismisses itself after 12 seconds.
    powershell = shutil.which("powershell.exe")
    if powershell:
        script = (
            "$w=New-Object -ComObject WScript.Shell; "
            "[void]$w.Popup($args[0],12,$args[1],64)")
        try:
            subprocess.Popen(
                [powershell, "-NoProfile", "-NonInteractive", "-Command",
                 script, message, caption],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _append_event(paths, "NOTICE",
                          "notification dispatched via WScript.Shell.Popup")
            return
        except OSError as exc:
            _append_event(paths, "NOTICE", "popup launch failed: %s" % exc)
    _append_event(paths, "NOTICE",
                  "Windows notification unavailable; event log only")


def _write_state(paths: Dict[str, Path], state: dict, **updates) -> None:
    state.update(updates)
    state["updated_at"] = utc_now()
    _atomic_json(paths["state"], state)


def _parse_progress(line: str, state: dict) -> bool:
    """Update current operation/progress. Return True on confirmed progress."""
    stripped = line.strip()
    if stripped.startswith("matrix") and "combination(s) expected" in stripped:
        import re
        m = re.search(r"(\d+) combination\(s\) expected \((\d+) already", stripped)
        if m:
            state["total"] = int(m.group(1))
            state["completed"] = int(m.group(2))
            return True
    if stripped.startswith("START "):
        state["current_operation"] = stripped[6:]
        return True
    if "resumed from checkpoint" in stripped:
        state["current_operation"] = stripped
        return True
    if " control=" in line and " check=" in line:
        state["completed"] = min(state.get("total", 0),
                                 state.get("completed", 0) + 1)
        state["current_operation"] = stripped
        return True
    if "combination(s) evaluated" in stripped:
        import re
        m = re.search(r"(\d+) combination", stripped)
        if m:
            state["completed"] = int(m.group(1))
            return True
    if stripped.startswith(("probing", "parsed dxf", "dxf ")):
        state["current_operation"] = stripped
        return True
    return False


def _tail_new_lines(path: Path, offset: int):
    if not path.exists():
        return offset, []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(offset)
        lines = fh.readlines()
        return fh.tell(), lines


def supervise(command: List[str], dataset: str, paths: Dict[str, Path],
              stall_seconds: float, poll_seconds: float = 2.0,
              notifications: bool = True) -> int:
    """Run and independently monitor one worker. Called by detached supervisor."""
    state = _read_json(paths["state"]) or {}
    state.update({
        "status": "STARTING", "dataset": str(Path(dataset).resolve()),
        "monitor_pid": os.getpid(), "worker_pid": None,
        "started_at": state.get("started_at") or utc_now(),
        "current_operation": "запуск benchmark runner",
        "completed": state.get("completed", 0),
        "total": state.get("total", 0),
        "last_progress_at": state.get("last_progress_at") or utc_now(),
        "last_activity_at": utc_now(), "last_activity_check_at": utc_now(),
        "log_path": str(paths["log"]), "exit_code": None,
        "reason": None, "stall_seconds": stall_seconds,
    })
    _write_state(paths, state)
    paths["root"].mkdir(parents=True, exist_ok=True)
    paths["stop"].unlink(missing_ok=True)
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    with paths["log"].open("a", encoding="utf-8", buffering=1) as log:
        log.write("\n=== JOB %s START %s ===\n" % (os.getpid(), utc_now()))
        log.flush()
        offset = log.tell()
        try:
            worker = subprocess.Popen(
                command, cwd=str(REPO_ROOT), stdout=log,
                stderr=subprocess.STDOUT, creationflags=creationflags)
        except Exception as exc:
            _write_state(paths, state, status="FAILED", exit_code=None,
                         reason="runner start failed: %s" % exc)
            _notify(paths, "FAILED", state["reason"], notifications)
            paths["lock"].unlink(missing_ok=True)
            return 1

        now = utc_now()
        _write_state(paths, state, status="RUNNING", worker_pid=worker.pid,
                     worker_started_at=now,
                     last_progress_at=now, last_activity_at=now,
                     current_operation="runner PID %d" % worker.pid)
        _append_event(paths, "RUNNING", "worker PID %d" % worker.pid)
        last_cpu = process_cpu_seconds(worker.pid)
        last_activity_monotonic = time.monotonic()
        stop_requested = False
        saw_error = False

        while True:
            offset, lines = _tail_new_lines(paths["log"], offset)
            progress = False
            for line in lines:
                if "Traceback" in line or line.lstrip().startswith("ERROR"):
                    saw_error = True
                progress = _parse_progress(line, state) or progress
            now_iso = utc_now()
            if progress:
                state["last_progress_at"] = now_iso
                state["last_activity_at"] = now_iso
                last_activity_monotonic = time.monotonic()

            cpu = process_cpu_seconds(worker.pid)
            if cpu is not None and last_cpu is not None and cpu > last_cpu + 0.01:
                state["last_activity_at"] = now_iso
                last_activity_monotonic = time.monotonic()
                if state.get("status") == "STALLED":
                    state["status"] = "RUNNING"
                    state["reason"] = None
                    _append_event(paths, "RUNNING", "CPU activity resumed")
            if cpu is not None:
                last_cpu = cpu
                state["worker_cpu_seconds"] = round(cpu, 3)
            state["last_activity_check_at"] = now_iso

            if paths["stop"].exists() and not stop_requested:
                stop_requested = True
                state["status"] = "RUNNING"
                state["reason"] = (
                    "ожидание безопасной границы после сохранения checkpoint")
                state["current_operation"] = "штатная остановка запрошена"
                _append_event(paths, "STOP", "operator stop requested")

            inactive = time.monotonic() - last_activity_monotonic
            if (inactive >= stall_seconds and worker.poll() is None
                    and state.get("status") != "STALLED"):
                state["status"] = "STALLED"
                message = ("нет textual progress и роста CPU %.0f s; "
                           "процесс всё ещё существует" % inactive)
                state["reason"] = message
                _notify(paths, "STALLED", message, notifications)

            code = worker.poll()
            _write_state(paths, state)
            if code is not None:
                # Parse output emitted immediately before exit.
                offset, lines = _tail_new_lines(paths["log"], offset)
                for line in lines:
                    if "Traceback" in line or line.lstrip().startswith("ERROR"):
                        saw_error = True
                    _parse_progress(line, state)
                if code == 0 and state.get("total", 0) > 0 \
                        and state.get("completed") == state.get("total"):
                    status = "COMPLETED"
                    reason = "runner завершён штатно; %d/%d" % (
                        state["completed"], state["total"])
                elif stop_requested:
                    status = "INTERRUPTED"
                    reason = "остановлено оператором; checkpoint сохранён"
                elif saw_error:
                    status = "FAILED"
                    reason = "runner завершён с ошибкой; см. benchmark.log"
                else:
                    status = "INTERRUPTED"
                    reason = ("worker исчез до штатного завершения; "
                              "checkpoint сохранён")
                _write_state(paths, state, status=status, exit_code=code,
                             reason=reason, finished_at=utc_now())
                _notify(paths, status, reason, notifications)
                paths["stop"].unlink(missing_ok=True)
                paths["lock"].unlink(missing_ok=True)
                return 0 if status == "COMPLETED" else 1
            time.sleep(poll_seconds)


def _acquire_lock(paths: Dict[str, Path]) -> None:
    paths["root"].mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(paths["lock"]), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            lock_owner = int(paths["lock"].read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            lock_owner = -1
        # This closes the small start-up window before state.json contains the
        # detached monitor PID.
        if process_exists(lock_owner):
            raise RuntimeError("для dataset уже запускается benchmark "
                               "(controller PID %d)" % lock_owner)
        state = _read_json(paths["state"]) or {}
        monitor_alive = process_exists(state.get("monitor_pid"))
        worker_alive = process_exists(state.get("worker_pid"))
        if state.get("status") in ACTIVE_STATES and (monitor_alive or worker_alive):
            raise RuntimeError("для dataset уже выполняется benchmark")
        paths["lock"].unlink(missing_ok=True)
        fd = os.open(str(paths["lock"]), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(str(os.getpid()))


def start_job(args, resume: bool = False) -> int:
    dataset = str(Path(args.dataset).resolve())
    if not Path(dataset).is_dir():
        print("ERROR: dataset не найден:", dataset, file=sys.stderr)
        return 2
    results_root = str(Path(args.results_root).resolve())
    paths = job_paths(dataset, results_root)
    checkpoint = Path(results_root) / _dataset_name(dataset) / "checkpoint.json"
    if resume and not checkpoint.is_file():
        print("ERROR: безопасное resume невозможно: checkpoint отсутствует:",
              checkpoint, file=sys.stderr)
        return 4
    try:
        _acquire_lock(paths)
    except RuntimeError as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 3
    state = {
        "status": "STARTING", "dataset": dataset,
        "started_at": utc_now(), "monitor_pid": None, "worker_pid": None,
        "current_operation": "запуск независимого supervisor",
        "completed": 0, "total": 0, "last_progress_at": utc_now(),
        "last_activity_at": utc_now(), "last_activity_check_at": utc_now(),
        "log_path": str(paths["log"]), "exit_code": None, "reason": None,
    }
    _atomic_json(paths["state"], state)
    command = [sys.executable, "-u", str(Path(__file__).resolve()),
               "_supervise", dataset, "--results-root", results_root,
               "--stall-seconds", str(args.stall_seconds)]
    if getattr(args, "no_notifications", False):
        command.append("--no-notifications")
    creationflags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    try:
        monitor = subprocess.Popen(
            command, cwd=str(REPO_ROOT), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, creationflags=creationflags)
    except Exception:
        paths["lock"].unlink(missing_ok=True)
        raise
    # The supervisor may already have advanced the state to RUNNING. Merge
    # only monitor_pid instead of overwriting worker_pid/progress in that race.
    current = _read_json(paths["state"]) or state
    current["monitor_pid"] = monitor.pid
    _atomic_json(paths["state"], current)
    print("STARTING: monitor PID %d" % monitor.pid)
    print("state:", paths["state"])
    print("log:  ", paths["log"])
    print("status command: python research/benchmark_job.py status %s"
          % args.dataset)
    return 0


def refresh_interrupted_state(paths: Dict[str, Path], state: dict) -> dict:
    if state.get("status") not in ACTIVE_STATES:
        return state
    monitor_alive = process_exists(state.get("monitor_pid"))
    worker_alive = process_exists(state.get("worker_pid"))
    if not monitor_alive and not worker_alive:
        reason = "monitor и worker исчезли до штатного завершения"
        _write_state(paths, state, status="INTERRUPTED", reason=reason,
                     finished_at=utc_now())
        _notify(paths, "INTERRUPTED", reason, enabled=True)
        paths["lock"].unlink(missing_ok=True)
    return state


def print_status(args) -> int:
    paths = job_paths(args.dataset, args.results_root)
    state = _read_json(paths["state"])
    if not state:
        print("Статус отсутствует:", paths["state"])
        return 1
    state = refresh_interrupted_state(paths, state)
    print("status:             ", state.get("status"))
    print("dataset:            ", state.get("dataset"))
    print("monitor PID:        ", state.get("monitor_pid"))
    print("worker PID:         ", state.get("worker_pid"))
    print("worker started:     ", state.get("worker_started_at"))
    print("progress:           ", "%s/%s" % (state.get("completed", 0),
                                                    state.get("total", 0)))
    print("current operation:  ", state.get("current_operation"))
    print("started:            ", state.get("started_at"))
    print("last progress:      ", state.get("last_progress_at"))
    print("last activity:      ", state.get("last_activity_at"))
    print("last activity check:", state.get("last_activity_check_at"))
    print("exit code:          ", state.get("exit_code"))
    print("reason:             ", state.get("reason"))
    print("log:                ", state.get("log_path"))
    return 0


def print_errors(args) -> int:
    paths = job_paths(args.dataset, args.results_root)
    for label, path in (("EVENTS", paths["events"]), ("LOG", paths["log"])):
        print("=== %s: %s ===" % (label, path))
        if not path.exists():
            print("(нет файла)")
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if label == "LOG":
            errors = [line for line in lines
                      if "ERROR" in line or "Traceback" in line]
            lines = errors[-args.lines:] if errors else lines[-args.lines:]
        else:
            lines = lines[-args.lines:]
        print("\n".join(lines))
    return 0


def stop_job(args) -> int:
    paths = job_paths(args.dataset, args.results_root)
    state = _read_json(paths["state"])
    if not state or state.get("status") not in ACTIVE_STATES:
        print("Нет активного задания.")
        return 1
    paths["stop"].write_text(utc_now(), encoding="ascii")
    _append_event(paths, "STOP", "stop request written by operator")
    print("Запрошена штатная остановка. Завершённые пары остаются в checkpoint.")
    print("Проверьте: python research/benchmark_job.py status %s" % args.dataset)
    return 0


def _synthetic_command(mode: str, stop_path: Optional[Path] = None) -> List[str]:
    if mode == "complete":
        code = ("print('matrix     : 1 combination(s) expected (0 already checkpointed)',flush=True);"
                "print('START synthetic|p0|synthetic',flush=True);"
                "print('  synthetic | synthetic control=1 check=1',flush=True);"
                "print('matrix     : 1 combination(s) evaluated, no early exit',flush=True)")
    elif mode == "fail":
        code = ("import sys; print('ERROR: synthetic failure',flush=True);"
                "sys.exit(5)")
    elif mode == "stop":
        code = (
            "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]);"
            "print('matrix     : 1 combination(s) expected "
            "(0 already checkpointed)',flush=True);"
            "print('START synthetic checkpoint-safe operation',flush=True);"
            "\nwhile not p.exists(): time.sleep(0.05)\n"
            "print('interrupted: synthetic checkpoint retained',flush=True);"
            "sys.exit(130)")
        return [sys.executable, "-u", "-c", code, str(stop_path)]
    else:
        code = ("import time; print('matrix     : 1 combination(s) expected "
                "(0 already checkpointed)',flush=True);"
                "print('START synthetic long operation',flush=True);time.sleep(60)")
    return [sys.executable, "-u", "-c", code]


def self_test() -> int:
    """Short COMPLETED and STALLED->forced-termination scenarios."""
    base = Path(tempfile.mkdtemp(prefix="vpgeo-job-selftest-"))
    results = []
    for mode in ("complete", "fail", "stop", "interrupt"):
        paths = job_paths(str(base / mode), str(base / "results"))
        paths["root"].mkdir(parents=True, exist_ok=True)
        paths["lock"].write_text("self-test", encoding="ascii")
        _atomic_json(paths["state"], {"status": "STARTING",
                                      "started_at": utc_now()})
        killer = None
        if mode in ("stop", "interrupt"):
            def kill_worker():
                deadline = time.time() + 10
                while time.time() < deadline:
                    state = _read_json(paths["state"]) or {}
                    pid = state.get("worker_pid")
                    if pid and process_exists(pid):
                        if mode == "stop":
                            paths["stop"].write_text(utc_now(), encoding="ascii")
                        else:
                            # Wait long enough for STALLED to be recorded first.
                            time.sleep(1.5)
                            subprocess.run(
                                ["taskkill", "/PID", str(pid), "/F"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
                        return
                    time.sleep(0.05)
            killer = threading.Thread(target=kill_worker, daemon=True)
            killer.start()
        code = supervise(_synthetic_command(mode, paths["stop"]),
                         str(base / mode), paths,
                         stall_seconds=0.5, poll_seconds=0.1,
                         notifications=False)
        if killer:
            killer.join(timeout=2)
        state = _read_json(paths["state"]) or {}
        events = paths["events"].read_text(encoding="utf-8")
        if mode == "complete":
            ok = code == 0 and state.get("status") == "COMPLETED"
        elif mode == "fail":
            ok = code != 0 and state.get("status") == "FAILED"
        elif mode == "stop":
            ok = (code != 0 and state.get("status") == "INTERRUPTED"
                  and "остановлено оператором" in state.get("reason", ""))
        else:
            ok = (code != 0 and state.get("status") == "INTERRUPTED"
                  and "STALLED" in events)
        results.append(ok)
        print("SELF-TEST %-9s %s" % (mode, "OK" if ok else "FAILED"))
        print("  final status:", state.get("status"), "reason:",
              state.get("reason"))
    return 0 if all(results) else 1


def notification_test() -> int:
    """Request one desktop notification without starting a benchmark job."""
    base = Path(tempfile.mkdtemp(prefix="vpgeo-notification-test-"))
    paths = job_paths(str(base / "manual"), str(base / "results"))
    _notify(paths, "MANUAL TEST",
            "Ручная проверка уведомления VP GeoConvert", enabled=True)
    print("Уведомление отправлено; появление окна необходимо подтвердить визуально.")
    print("events:", paths["events"])
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Управление длительным research benchmark")
    parser.add_argument("command", choices=("start", "status", "errors",
                                             "resume", "stop", "self-test",
                                             "notification-test",
                                             "_supervise"))
    parser.add_argument("dataset", nargs="?")
    parser.add_argument("--results-root", default=str(RESULTS_ROOT))
    parser.add_argument("--stall-seconds", type=float, default=300.0)
    parser.add_argument("--lines", type=int, default=40)
    parser.add_argument("--no-notifications", action="store_true")
    args = parser.parse_args(argv)
    if args.command not in ("self-test", "notification-test") \
            and not args.dataset:
        parser.error("dataset обязателен")
    return args


def main(argv=None) -> int:
    _configure_console()
    args = parse_args(argv)
    if args.command == "self-test":
        return self_test()
    if args.command == "notification-test":
        return notification_test()
    if args.command in ("start", "resume"):
        return start_job(args, resume=args.command == "resume")
    if args.command == "status":
        return print_status(args)
    if args.command == "errors":
        return print_errors(args)
    if args.command == "stop":
        return stop_job(args)
    if args.command == "_supervise":
        paths = job_paths(args.dataset, args.results_root)
        runner = [sys.executable, "-u", str(HERE / "benchmark_runner.py"),
                  str(Path(args.dataset).resolve()),
                  "--results-root", str(Path(args.results_root).resolve()),
                  "--stop-file", str(paths["stop"])]
        return supervise(runner, args.dataset, paths, args.stall_seconds,
                         notifications=not args.no_notifications)
    return 2


if __name__ == "__main__":
    sys.exit(main())
