"""One administrator prompt for a whole run of tests: a small helper started through the UAC prompt once, that
then runs the elevated tools of that run without asking again, and ends with the run (or with the app).

The owner asked for it on 2026-10-19: a run of several tests (PassMark asks for administrator rights at every
run) must not need someone at the PC to accept a prompt each time.

Safety (the helper runs as administrator; any program of the same user could try to talk to it):
- It does fixed jobs only, never a program, path or command sent to it. The only job today is "passmark":
  PassMark PerformanceTest from Program Files (only administrators can change it) with one of its test groups.
- The helper writes PassMark's script and PassMark writes its results into a new folder under Windows\\Temp
  that only administrators and SYSTEM can open (icacls, inheritance removed), with random file names; it sends
  the results back as text and deletes the folder. Nothing elevated writes into a folder the user can change
  (a link placed there could redirect the write), and the script cannot be swapped before PassMark reads it
  (PassMark scripts can start other programs).
- The connection is a named pipe with a random name and a random 32-byte key (multiprocessing.connection's
  HMAC challenge), messages are JSON (never pickle), and the helper ends when the app closes the connection.
- The UAC prompt is the operating system's; nothing bypasses it. Declined: the run goes on without the helper
  and each elevated test asks for itself.
"""

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

HELPER_FLAG = "--admin-helper"
CONNECT_SECONDS = 60
MAX_MESSAGE = 64 * 1024 * 1024
PASSMARK_GROUPS = ("CPU_ALL", "G2D_ALL", "G3D_ALL", "ME_ALL")
SW_HIDE = 0


def _send(conn, message: dict) -> None:
    conn.send_bytes(json.dumps(message).encode("utf-8"))


def _recv(conn) -> dict:
    data = json.loads(conn.recv_bytes(MAX_MESSAGE).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("not a message")
    return data


def helper_command(pipe: str, key_hex: str) -> tuple:
    """(program, parameters) that start the helper: the packaged app, or Python with scripts/main.py."""
    if getattr(sys, "frozen", False):
        return sys.executable, subprocess.list2cmdline([HELPER_FLAG, pipe, key_hex])
    main = Path(__file__).resolve().parents[2] / "scripts" / "main.py"
    return sys.executable, subprocess.list2cmdline(["-I", str(main), HELPER_FLAG, pipe, key_hex])


# ---------------------------------------------------------------- the app's side (not elevated)

class AdminHelper:
    """The app's handle on the helper. start() shows the UAC prompt once; passmark() runs a job through it."""

    def __init__(self, api=None, command: Callable = helper_command):
        self._api = api
        self._command = command
        self._conn = None
        self._handle = None
        self._lock = threading.Lock()           # one job at a time over the pipe

    def active(self) -> bool:
        return self._conn is not None

    def start(self) -> Optional[str]:
        """None when the helper runs, else why not (declined, could not start)."""
        if self._conn is not None:
            return None
        if sys.platform != "win32" and self._api is None:
            return "only needed on Windows"
        from multiprocessing.connection import Client, Listener
        from app.frames.elevated import ERROR_CANCELLED, WinApi
        api = self._api = self._api or WinApi()
        pipe = rf"\\.\pipe\pcbm-admin-{secrets.token_hex(16)}"
        key = secrets.token_bytes(32)
        listener = Listener(pipe, family="AF_PIPE", authkey=key)
        try:
            program, params = self._command(pipe, key.hex())
            handle, err = api.shell_execute(program, params, None, SW_HIDE)
            if handle is None:
                return ("the administrator prompt was declined" if err == ERROR_CANCELLED
                        else f"the helper could not start (Windows error {err})")
            got: Dict[str, object] = {}

            def accept():
                try:
                    got["conn"] = listener.accept()
                except Exception as e:      # noqa: BLE001 - reported below
                    got["error"] = e
            waiter = threading.Thread(target=accept, daemon=True)
            waiter.start()
            waiter.join(CONNECT_SECONDS)
            if waiter.is_alive():           # it never called: unblock accept() with our own call, then give up
                try:
                    Client(pipe, family="AF_PIPE", authkey=key).close()
                except Exception:           # noqa: BLE001
                    pass
                waiter.join(5)
                api.terminate(handle)
                api.close(handle)
                if got.get("conn") is not None:
                    got["conn"].close()
                return f"the helper did not answer within {CONNECT_SECONDS} s"
            conn = got.get("conn")
            if conn is None:
                api.close(handle)
                return f"the helper could not connect: {got.get('error')}"
            hello = _recv(conn)
            if hello.get("hello") != "pcbm-admin-helper":
                conn.close()
                api.close(handle)
                return "the helper did not answer as expected"
            self._conn, self._handle = conn, handle
            return None
        finally:
            listener.close()

    def passmark(self, group: str, timeout_seconds: float, stop_event: Optional[threading.Event] = None) -> dict:
        """Run one PassMark test group; {"status", "exit_code", "duration_seconds", "csv", "txt", "error"}."""
        with self._lock:
            if self._conn is None:
                return {"status": "start_failed", "error": "the administrator helper is not running"}
            try:
                _send(self._conn, {"op": "passmark", "group": group, "timeout": float(timeout_seconds)})
                while True:
                    if self._conn.poll(0.2):
                        answer = _recv(self._conn)
                        if answer.get("op") == "done":
                            return answer
                    elif stop_event is not None and stop_event.is_set():
                        _send(self._conn, {"op": "stop"})
                        stop_event = None   # sent once; the "done" answer follows
            except (EOFError, OSError, ValueError) as e:
                self._drop()
                return {"status": "start_failed", "error": f"the administrator helper stopped ({e})"}

    def _drop(self):
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
        if self._handle is not None and self._api is not None:
            self._api.close(self._handle)
        self._handle = None

    def close(self) -> None:
        """End the helper (it also ends by itself when the connection closes)."""
        with self._lock:
            if self._conn is not None:
                try:
                    _send(self._conn, {"op": "exit"})
                except (OSError, ValueError):
                    pass
            self._drop()


_session: Optional[AdminHelper] = None


def session() -> Optional[AdminHelper]:
    """The helper started for the run in progress, if any."""
    return _session if _session is not None and _session.active() else None


def begin(helper: Optional[AdminHelper] = None) -> Optional[str]:
    """Start the helper for a run (one UAC prompt); None when it runs, else why not."""
    global _session
    end()
    _session = helper or AdminHelper()
    why = _session.start()
    if why is not None:
        _session = None
    return why


def end() -> None:
    global _session
    if _session is not None:
        _session.close()
    _session = None


# ---------------------------------------------------------------- the helper's side (elevated)

def _admin_only_folder() -> Path:
    """A new folder under Windows\\Temp that only administrators and SYSTEM can open."""
    import tempfile
    base = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp"
    folder = Path(tempfile.mkdtemp(prefix="pcbm-", dir=str(base)))
    done = subprocess.run(["icacls", str(folder), "/inheritance:r", "/grant:r", "*S-1-5-32-544:(OI)(CI)F",
                           "/grant:r", "*S-1-5-18:(OI)(CI)F"], capture_output=True, timeout=30)
    if done.returncode != 0:
        shutil.rmtree(folder, ignore_errors=True)
        raise OSError("could not limit the work folder to administrators")
    return folder


def _passmark_job(group: str, timeout: float, stop: threading.Event, popen=subprocess.Popen,
                  folder_maker=_admin_only_folder, program=None) -> dict:
    from app.pro import passmark
    if group not in PASSMARK_GROUPS:
        return {"op": "done", "status": "refused", "error": f"unknown PassMark test group {group!r}"}
    exe = program or passmark.program_path()
    if exe is None:
        return {"op": "done", "status": "start_failed", "error": "PassMark PerformanceTest is not installed"}
    folder = folder_maker()
    try:
        tag = secrets.token_hex(8)
        csv_path, txt_path = folder / f"r{tag}.csv", folder / f"r{tag}.txt"
        script = folder / f"s{tag}.ptscript"
        with open(script, "x", encoding="ascii", newline="") as f:
            f.write(passmark.script_text(group, csv_path, txt_path))
        info = None
        if sys.platform == "win32":
            info = subprocess.STARTUPINFO()
            info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = passmark.SW_SHOWNORMAL
        started = time.monotonic()
        try:
            proc = popen([str(exe), "/s", str(script)], cwd=str(Path(exe).parent), startupinfo=info)
        except OSError as e:
            return {"op": "done", "status": "start_failed", "error": f"could not start: {e}"}
        status = "exited"
        while proc.poll() is None:
            if stop.is_set() or time.monotonic() - started >= timeout:
                status = "stopped" if stop.is_set() else "timed_out"
                proc.kill()                 # our own child only
                proc.wait(10)
                break
            time.sleep(0.2)

        def read(p: Path) -> str:
            try:
                return p.read_text(encoding="utf-8-sig", errors="replace")[:16 * 1024 * 1024]
            except OSError:
                return ""
        return {"op": "done", "status": status, "exit_code": proc.returncode,
                "duration_seconds": time.monotonic() - started, "csv": read(csv_path), "txt": read(txt_path)}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def serve(conn, job=_passmark_job) -> None:
    """Answer jobs until the app says exit or closes the connection."""
    _send(conn, {"hello": "pcbm-admin-helper", "pid": os.getpid()})
    stop = threading.Event()
    result: Dict[str, dict] = {}
    worker: Optional[threading.Thread] = None
    while True:
        try:
            if not conn.poll(0.2):
                if worker is not None and not worker.is_alive():
                    _send(conn, result.pop("done"))
                    worker = None
                continue
            message = _recv(conn)
        except (EOFError, OSError, ValueError):
            stop.set()                      # the app is gone: stop PassMark and end
            if worker is not None:
                worker.join(15)
            return
        op = message.get("op")
        if op == "exit":
            stop.set()
            if worker is not None:
                worker.join(15)
            return
        if op == "stop":
            stop.set()
        elif op == "passmark" and worker is None:
            stop.clear()
            group, timeout = str(message.get("group")), float(message.get("timeout") or 1800)

            def work(group=group, timeout=timeout):
                try:
                    result["done"] = job(group, timeout, stop)
                except Exception as e:      # noqa: BLE001 - sent back, the helper keeps serving
                    result["done"] = {"op": "done", "status": "start_failed", "error": str(e)}
            worker = threading.Thread(target=work, daemon=True)
            worker.start()
        elif op == "passmark":
            _send(conn, {"op": "done", "status": "refused", "error": "a job is already running"})
        else:
            _send(conn, {"op": "done", "status": "refused", "error": f"unknown job {op!r}"})


def helper_main(argv) -> int:
    """Entry of the elevated helper: <pipe> <key hex>."""
    from multiprocessing.connection import Client
    try:
        pipe, key_hex = argv[0], argv[1]
        if not pipe.startswith("\\\\.\\pipe\\pcbm-admin-"):
            return 2
        conn = Client(pipe, family="AF_PIPE", authkey=bytes.fromhex(key_hex))
    except (IndexError, ValueError, OSError):
        return 2
    try:
        serve(conn)
    finally:
        conn.close()
    return 0
