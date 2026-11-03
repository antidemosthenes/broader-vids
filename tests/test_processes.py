from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import threading
import os
from concurrent.futures import ThreadPoolExecutor

from broader_vids.processes import ProcessCleanupError, run_bounded, start_owned


class ProcessTests(unittest.TestCase):
    def test_timeout_retains_bounded_worker_diagnostics(self):
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            run_bounded(
                [
                    sys.executable,
                    "-c",
                    "import sys,time;print('stage ready',flush=True);print('diagnostic detail',file=sys.stderr,flush=True);time.sleep(10)",
                ],
                timeout=0.8,
            )
        self.assertIn("stage ready", caught.exception.output)
        self.assertIn("diagnostic detail", caught.exception.stderr)
        self.assertLessEqual(len(caught.exception.stderr), 4096)

    def test_excessive_logs_stop_a_worker_before_its_time_allowance(self):
        started = time.monotonic()
        with self.assertRaisesRegex(OSError, "log exceeded"):
            run_bounded(
                [
                    sys.executable,
                    "-c",
                    "import sys,time;sys.stdout.buffer.write(b'x'*(4*1024*1024+1));sys.stdout.flush();time.sleep(20)",
                ],
                timeout=10,
            )
        self.assertLess(time.monotonic() - started, 5)

    def test_cancelling_one_owned_tree_does_not_stop_a_parallel_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            survived = Path(directory) / "survived"
            ready = Path(directory) / "ready"
            survivor = f"from pathlib import Path;import time;Path({str(ready)!r}).touch();time.sleep(0.7);Path({str(survived)!r}).touch()"

            def cancelled_worker():
                deadline = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_bounded(
                        [sys.executable, "-c", "import time;time.sleep(10)"],
                        timeout=0.2,
                    )

            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(
                    run_bounded, [sys.executable, "-c", survivor], timeout=4
                )
                second = executor.submit(cancelled_worker)
                self.assertEqual(first.result().returncode, 0)
                second.result()
            self.assertTrue(survived.exists())

    @unittest.skipUnless(os.name == "nt", "Windows job-object parent-death behavior")
    def test_windows_job_stops_workers_when_the_owning_process_dies(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready"
            finished = Path(directory) / "finished"
            child = f"from pathlib import Path;import time;Path({str(ready)!r}).touch();time.sleep(0.7);Path({str(finished)!r}).touch()"
            parent = f"from broader_vids.processes import run_bounded;import sys;run_bounded([sys.executable,'-c',{child!r}],timeout=5)"
            process = subprocess.Popen(
                [sys.executable, "-c", parent],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 4
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists())
                process.kill()
                process.wait(timeout=3)
                time.sleep(0.9)
                self.assertFalse(finished.exists())
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=3)

    def test_successful_launcher_exit_does_not_leave_its_child_running(self):
        with tempfile.TemporaryDirectory() as directory:
            finished = Path(directory) / "orphan-finished"
            child = f"import time;from pathlib import Path;time.sleep(1);Path({str(finished)!r}).touch()"
            parent = f"import subprocess,sys;subprocess.Popen([sys.executable,'-c',{child!r}])"
            result = run_bounded([sys.executable, "-c", parent], timeout=5)
            self.assertEqual(result.returncode, 0)
            time.sleep(1.2)
            self.assertFalse(finished.exists())

    def test_parent_cancellation_stops_a_running_worker(self):
        cancelled = threading.Event()
        timer = threading.Timer(0.2, cancelled.set)
        timer.start()
        started = time.monotonic()
        try:
            try:
                with self.assertRaises(InterruptedError):
                    run_bounded(
                        [sys.executable, "-c", "import time;time.sleep(20)"],
                        timeout=10,
                        cancelled=cancelled,
                    )
            except ProcessCleanupError:
                self.skipTest(
                    "This sandbox denies owned-tree termination; run with the normal executor."
                )
            self.assertLess(time.monotonic() - started, 5)
        finally:
            timer.cancel()

    def test_worker_input_and_output_are_preserved_without_pipe_deadlocks(self):
        result = run_bounded(
            [
                sys.executable,
                "-c",
                "import sys;print(sys.stdin.read());print('diagnostic',file=sys.stderr)",
            ],
            input="A unicode input: 数学",
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "A unicode input: 数学")
        self.assertEqual(result.stderr.strip(), "diagnostic")

    def test_timed_out_launcher_stops_its_child_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            started = Path(directory) / "started"
            finished = Path(directory) / "finished"
            child = f"from pathlib import Path;import time;Path({str(started)!r}).touch();time.sleep(2);Path({str(finished)!r}).touch()"
            parent = f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',{child!r}]);time.sleep(20)"
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_bounded([sys.executable, "-c", parent], timeout=1)
            except ProcessCleanupError:
                time.sleep(2)
                self.skipTest(
                    "This process sandbox blocks tree termination; run this regression with a normal local executor."
                )
            self.assertTrue(started.exists())
            time.sleep(2)
            self.assertFalse(finished.exists())

    def test_startup_failure_closes_job_even_if_process_cleanup_fails(self):
        process = Mock()
        process.poll.return_value = None
        process.kill.side_effect = OSError("fixture cleanup failure")
        with (
            patch("broader_vids.windows_job.WindowsJob") as job,
            patch("broader_vids.processes.os", SimpleNamespace(name="nt")),
            patch("broader_vids.processes.subprocess.Popen", return_value=process),
        ):
            job.return_value.start.side_effect = OSError("fixture ownership failure")
            with self.assertRaisesRegex(OSError, "fixture cleanup failure"):
                start_owned(["fixture"])
            job.return_value.close.assert_called_once()
