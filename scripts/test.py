import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
import subprocess
from pathlib import Path
import sys
import threading
import tempfile
import time
import unittest

from broader_vids.processes import run_bounded


ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
TIMINGS = ROOT / ".cache" / "test-durations.json"


def load_timings():
    try:
        if TIMINGS.stat().st_size > 65536:
            return {}
        values = json.loads(TIMINGS.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            return {}
        return {
            name: seconds
            for name, seconds in values.items()
            if type(seconds) in (int, float) and 0 <= seconds <= 3600
        }
    except (OSError, ValueError):
        return {}


def save_timings(previous, results):
    values = {**previous, **{r["file"]: r["duration"] for r in results}}
    available = {file.name for file in TESTS.glob("test_*.py")}
    temporary = None
    try:
        TIMINGS.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=TIMINGS.parent,
            prefix="test-durations-",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump(
                {name: value for name, value in values.items() if name in available},
                output,
            )
        temporary.replace(TIMINGS)
    except OSError:
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def worker(filename):
    progress = sys.stderr
    print(f"Discovering {filename}", file=progress, flush=True)

    class ProgressResult(unittest.TextTestResult):
        def startTest(self, test):
            print(f"Running {test.id()}", file=progress, flush=True)
            super().startTest(test)

    output = io.StringIO()
    sys.path.insert(0, str(TESTS))
    with redirect_stdout(output), redirect_stderr(output):
        suite = unittest.defaultTestLoader.discover(str(TESTS), pattern=filename)
        result = unittest.TextTestRunner(
            stream=output, buffer=True, resultclass=ProgressResult
        ).run(suite)
    return {
        "file": filename,
        "count": result.testsRun,
        "skipped": len(result.skipped),
        "success": result.wasSuccessful(),
        "log": output.getvalue() if not result.wasSuccessful() else "",
        "durations": [
            (str(test), seconds) for test, seconds in result.collectedDurations
        ],
    }


def run_file(filename, cancelled):
    environment = {**os.environ, "CODEX_THREAD_ID": "", "CODEX_SESSION_ID": ""}
    started = time.perf_counter()
    try:
        process = run_bounded(
            [sys.executable, str(Path(__file__).resolve()), "--worker", filename],
            timeout=180,
            cwd=ROOT,
            env=environment,
            cancelled=cancelled,
        )
        if process.returncode:
            raise ValueError(process.stderr or process.stdout or "Test worker exited.")
        return {**json.loads(process.stdout), "duration": time.perf_counter() - started}
    except (OSError, ValueError, InterruptedError, subprocess.SubprocessError) as error:
        return {
            "file": filename,
            "count": 0,
            "skipped": 0,
            "success": False,
            "log": "\n".join(
                str(value)
                for value in (
                    error,
                    getattr(error, "stderr", ""),
                    getattr(error, "output", ""),
                )
                if value
            ),
            "durations": [],
            "duration": time.perf_counter() - started,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run isolated test files concurrently without extra dependencies."
    )
    parser.add_argument(
        "--jobs", type=int, default=min(4, os.cpu_count() or 1), choices=range(1, 9)
    )
    parser.add_argument("--group", choices=("all", "core", "dashboard"), default="all")
    parser.add_argument(
        "--pattern", default="test_*.py", help="Test filename or glob within tests"
    )
    parser.add_argument("--slowest", type=int, default=5)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print each test file's start and result on separate lines",
    )
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        if Path(args.worker).name != args.worker:
            parser.error("Worker names must be test filenames.")
        print(json.dumps(worker(args.worker)))
        return 0
    if args.slowest < 0:
        parser.error("--slowest must be zero or greater.")
    files = [
        file.name
        for file in sorted(TESTS.glob(args.pattern))
        if file.is_file()
        and file.parent == TESTS
        and (
            args.group == "all"
            or ("dashboard" in file.stem) == (args.group == "dashboard")
        )
    ]
    if not files:
        parser.error("No test files matched this group and pattern.")
    timings = load_timings()
    files.sort(key=lambda name: (-timings.get(name, 0), name))
    started = time.perf_counter()
    cancelled = threading.Event()
    results = []
    print(
        f"Running {len(files)} files with {min(args.jobs, len(files))} workers...",
        flush=True,
    )
    if args.verbose:
        print(
            f"Python {sys.version.split()[0]}; scratch: {tempfile.gettempdir()}; browser: {os.environ.get('BROADER_VIDS_TEST_BROWSER', 'system default')}",
            flush=True,
        )
    browser_files = {
        name
        for name in files
        if "sync_playwright" in (TESTS / name).read_text(encoding="utf-8")
    }
    browsers = deque(name for name in files if name in browser_files)
    core = deque(name for name in files if name not in browser_files)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = {}
        try:
            while browsers or core or pending:
                while len(pending) < args.jobs:
                    if browsers and not any(info[1] for info in pending.values()):
                        filename, browser = browsers.popleft(), True
                    elif core:
                        filename, browser = core.popleft(), False
                    else:
                        break
                    if args.verbose:
                        print(f"START {filename}", flush=True)
                    pending[pool.submit(run_file, filename, cancelled)] = (
                        filename,
                        browser,
                        time.monotonic(),
                    )
                done, _ = wait(pending, timeout=15, return_when=FIRST_COMPLETED)
                if not done:
                    active = ", ".join(
                        f"{info[0]} ({time.monotonic() - info[2]:.0f}s)"
                        for info in pending.values()
                    )
                    print(f"\nStill running: {active}", flush=True)
                for future in done:
                    pending.pop(future)
                    result = future.result()
                    results.append(result)
                    if args.verbose:
                        label = "PASS" if result["success"] else "FAIL"
                        print(
                            f"{label} {result['file']} ({result['duration']:.2f}s, {result['count']} tests)",
                            flush=True,
                        )
                        if not result["success"]:
                            print(result["log"], flush=True)
                    else:
                        print("." if result["success"] else "F", end="", flush=True)
        except KeyboardInterrupt:
            cancelled.set()
            for future in pending:
                future.cancel()
            print("\nCancelled; stopping test workers.", flush=True)
            return 130
        except BaseException:
            cancelled.set()
            for future in pending:
                future.cancel()
            raise
    print()
    save_timings(timings, results)
    failures = [result for result in results if not result["success"]]
    for result in failures:
        print(f"\n{result['file']}\n{result['log']}")
    durations = sorted(
        (value for result in results for value in result["durations"]),
        key=lambda value: value[1],
        reverse=True,
    )
    for name, seconds in durations[: args.slowest]:
        print(f"{seconds:6.2f}s  {name}")
    print(
        f"{sum(result['count'] for result in results)} tests, {sum(result['skipped'] for result in results)} skipped, {len(failures)} failed files; {time.perf_counter() - started:.2f}s total."
    )
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
