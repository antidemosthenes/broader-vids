import argparse
from contextlib import closing, contextmanager
import errno
import hashlib
import http.client
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import webbrowser

from broader_vids import __version__
from broader_vids.evidence.service import atomic_json
from broader_vids.storage import Store
from broader_vids.web.server import make_server


_children = {}


@contextmanager
def lifecycle_lock(root):
    folder = session_path(root).parent
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "control.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            def acquire():
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

            def release():
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        deadline = time.monotonic() + 15
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError(
                        "Another dashboard operation is still active."
                    ) from None
                time.sleep(0.05)
        try:
            yield
        finally:
            release()


def data_key(root):
    return hashlib.sha256(
        os.path.normcase(str(Path(root).resolve())).encode()
    ).hexdigest()


def session_path(root):
    return Path(root) / ".dashboard" / "server.json"


def read_session(root):
    try:
        value = json.loads(session_path(root).read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("data_key") != data_key(root):
            return None
        if type(value.get("port")) is not int or not 1 <= value["port"] <= 65535:
            return None
        if not all(
            isinstance(value.get(key), str) for key in ("instance_id", "stop_token")
        ):
            return None
        if (
            len(value["instance_id"]) != 32
            or len(value["stop_token"]) != 64
            or type(value.get("pid")) is not int
            or value["pid"] <= 0
            or not isinstance(value.get("version"), str)
        ):
            return None
        return value
    except (OSError, ValueError):
        return None


def request(session, path, *, stop=False):
    connection = http.client.HTTPConnection("127.0.0.1", session["port"], timeout=1)
    try:
        connection.request(
            "POST" if stop else "GET",
            path,
            headers={"X-Broader-Vids-Control": session["stop_token"]} if stop else {},
        )
        response = connection.getresponse()
        if response.status != 200:
            return None
        return json.loads(response.read(8192))
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def active(session):
    if not session:
        return False
    health = request(session, "/api/health")
    return (
        isinstance(health, dict)
        and health.get("instance_id") == session["instance_id"]
        and health.get("data_key") == session["data_key"]
    )


def public_state(root, session):
    return {
        "status": "running",
        "url": f"http://127.0.0.1:{session['port']}",
        "pid": session["pid"],
        "data_directory": str(root),
        "version": session["version"],
    }


def dashboard(root, operation="open", *, port=8765, open_browser=True):
    for key, child in list(_children.items()):
        if child.poll() is not None:
            child.wait()
            _children.pop(key, None)
    root = Path(root).resolve()
    if (
        operation not in {"open", "status", "stop"}
        or type(port) is not int
        or not 0 <= port <= 65535
    ):
        raise ValueError("Use open, status, or stop with a valid local port.")
    with lifecycle_lock(root):
        with closing(Store(root)):
            pass
        session = read_session(root)
        running = active(session)
        if operation == "status":
            return (
                public_state(root, session)
                if running
                else {"status": "stopped", "data_directory": str(root)}
            )
        if operation == "stop":
            if not running:
                return {"status": "stopped", "data_directory": str(root)}
            if request(session, "/api/shutdown", stop=True) != {"stopping": True}:
                raise ValueError("The dashboard did not accept its stop request.")
            deadline = time.monotonic() + 5
            while active(session) and time.monotonic() < deadline:
                time.sleep(0.05)
            child = _children.get(session["instance_id"])
            if child:
                try:
                    child.wait(timeout=5)
                    _children.pop(session["instance_id"], None)
                except subprocess.TimeoutExpired:
                    pass
            return {
                "status": "stopping" if active(session) else "stopped",
                "data_directory": str(root),
            }
        if not running:
            folder = session_path(root).parent
            folder.mkdir(parents=True, exist_ok=True)
            log = folder / "startup.log"
            if log.exists() and log.stat().st_size > 262144:
                log.replace(folder / "startup.previous.log")
            instance = secrets.token_hex(16)
            options = (
                {
                    "creationflags": subprocess.CREATE_NO_WINDOW
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                }
                if os.name == "nt"
                else {"start_new_session": True}
            )
            with log.open("ab") as output:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "broader_vids.web.runtime",
                        "--root",
                        str(root),
                        "--port",
                        str(port),
                        "--instance",
                        instance,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    cwd=root,
                    **options,
                )
            deadline = time.monotonic() + 10
            try:
                while time.monotonic() < deadline:
                    session = read_session(root)
                    if (
                        session
                        and session["instance_id"] == instance
                        and active(session)
                    ):
                        break
                    if child.poll() is not None:
                        raise ValueError(f"The dashboard could not start. Check {log}.")
                    time.sleep(0.05)
                else:
                    raise ValueError(
                        f"The dashboard did not start within ten seconds. Check {log}."
                    )
            except BaseException:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=5)
                raise
            _children[instance] = child
        result = public_state(root, session)
    if open_browser:
        try:
            result["browser_opened"] = bool(webbrowser.open(result["url"], new=2))
        except (OSError, webbrowser.Error) as error:
            result["browser_opened"] = False
            result["browser_error"] = (
                f"Could not start the system browser ({type(error).__name__})."
            )
        result["browser_status"] = (
            "launch_requested" if result["browser_opened"] else "manual_open_required"
        )
        if not result["browser_opened"]:
            result["browser_message"] = (
                "Open the returned URL in your system browser. The dashboard server is ready."
            )
    return result


def worker(root, port, instance):
    handler = RotatingFileHandler(
        session_path(root).parent / "server.log",
        maxBytes=262144,
        backupCount=1,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("broader_vids.web")
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    control = {
        "instance_id": instance,
        "data_key": data_key(root),
        "stop_token": secrets.token_hex(32),
    }
    try:
        server = make_server(root, port, control=control)
    except OSError as error:
        if (
            error.errno != errno.EADDRINUSE
            and getattr(error, "winerror", None) != 10048
        ):
            raise
        server = make_server(root, 0, control=control)
    path = session_path(root)
    atomic_json(
        path,
        {
            **control,
            "port": server.server_port,
            "pid": os.getpid(),
            "version": __version__,
        },
    )
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
        session = read_session(root)
        if session and session["instance_id"] == instance:
            path.unlink(missing_ok=True)
        handler.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--instance", required=True)
    args = parser.parse_args()
    worker(Path(args.root), args.port, args.instance)
