import os
import signal
import subprocess
import tempfile
import time
import sys
import json


class ProcessCleanupError(PermissionError):
    pass


def start_owned(command, **options):
    if os.name != "nt":
        return subprocess.Popen(command, **{**options, "start_new_session": True})
    from broader_vids.windows_job import WindowsJob

    scope = WindowsJob()
    process = None
    try:
        wrapper = [
            sys.executable,
            "-m",
            "broader_vids.owned_process",
            scope.event_name,
            json.dumps(command),
        ]
        # The bootstrap waits for job ownership before starting the command.
        process = subprocess.Popen(wrapper, **options)
        scope.start(process)
        process._broader_vids_job = scope
        return process
    except BaseException as error:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        scope.close()
        raise ProcessCleanupError(
            "Could not isolate a worker in its owned Windows job before execution."
        ) from error


def release_owned(process):
    if getattr(process, "_broader_vids_released", False):
        return
    scope = getattr(process, "_broader_vids_job", None)
    if scope is not None:
        try:
            scope.terminate()
        finally:
            scope.close()
            process._broader_vids_job = None
            process._broader_vids_released = True
    elif os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process._broader_vids_released = True


def _terminate(process):
    if getattr(process, "_broader_vids_job", None) is not None:
        release_owned(process)
        return
    if os.name != "nt":
        release_owned(process)
        return
    if process.poll() is not None:
        return
    raise ProcessCleanupError(
        "An unowned Windows process cannot be terminated through the worker-tree interface."
    )


def run_bounded(command, *, timeout, input=None, env=None, cwd=None, cancelled=None):
    options = (
        {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        }
        if os.name == "nt"
        else {"start_new_session": True}
    )
    with (
        tempfile.TemporaryFile() as source,
        tempfile.TemporaryFile() as stdout,
        tempfile.TemporaryFile() as stderr,
    ):
        if input is not None:
            source.write(input.encode("utf-8"))
            source.seek(0)
        process = start_owned(
            command,
            stdin=source if input is not None else subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=env,
            cwd=cwd,
            **options,
        )
        try:
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if cancelled is not None and cancelled.is_set():
                    raise InterruptedError("The parent cancelled this worker.")
                if any(
                    os.fstat(stream.fileno()).st_size > 4 * 1024 * 1024
                    for stream in (stdout, stderr)
                ):
                    raise OSError("The worker log exceeded its 4 MiB limit.")
                left = deadline - time.monotonic()
                if left <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    process.wait(timeout=min(0.2, left))
                except subprocess.TimeoutExpired:
                    pass
        except BaseException as error:
            try:
                _terminate(process)
            finally:
                process.wait(timeout=5)
            for name, stream in (("output", stdout), ("stderr", stderr)):
                stream.seek(max(0, os.fstat(stream.fileno()).st_size - 4096))
                setattr(
                    error, name, stream.read(4096).decode("utf-8", errors="replace")
                )
            raise
        finally:
            release_owned(process)
        outputs = []
        for stream in (stdout, stderr):
            stream.seek(0)
            raw = stream.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise OSError("The worker log exceeded its 4 MiB limit.")
            outputs.append(raw.decode("utf-8", errors="replace"))
    return subprocess.CompletedProcess(command, process.returncode, *outputs)
