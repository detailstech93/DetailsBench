"""One administrator prompt for a whole run of tests: a small helper started through the UAC prompt once, that
then runs the elevated tools of that run without asking again, and ends with the run (or with the app).

The owner asked for it on 2026-10-19: a run of several tests (PassMark asks for administrator rights at every
run) must not need someone at the PC to accept a prompt each time.

Safety (the helper runs as administrator; any program of the same user could try to talk to it):
- It does fixed jobs only, never a command sent to it. The jobs: "passmark": PassMark PerformanceTest from
  Program Files (only administrators can change it) with one of its test groups; and "presentmon" (owner,
  2026-11-04: a line of games asks once, not once per game): the PresentMon the owner picked (an .exe named
  PresentMon*, not FrameView's copy) with the app's own capture flags only (each flag checked, process names
  without a folder); it writes its CSV into the admin-only folder and the text is sent back.
  shortcut: the PresentMon path is the one in settings.json (the user's), as each UAC prompt before ran it; a
  signature check would close that, if the helper ever serves anyone but the owner.
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
import re
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
PART = 4 * 1024 * 1024                  # a long capture's CSV goes back in parts of this size
PM_FLAGS = ("--terminate_on_proc_exit", "--terminate_after_timed", "--stop_existing_session")
PM_VALUES = {"--process_name": re.compile(r"^[^\\/:*?\"<>|]{1,120}\.exe$", re.IGNORECASE),
             "--process_id": re.compile(r"^\d{1,10}$"), "--timed": re.compile(r"^\d{1,6}$"),
             "--session_name": re.compile(r"^PCBenchmarkManager$"), "--output_file": re.compile(r"")}


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
        return self._job({"op": "passmark", "group": group, "timeout": float(timeout_seconds)}, stop_event)

    def presentmon(self, program: str, args, timeout_seconds: float, stop_event: Optional[threading.Event] = None,
                   on_started: Optional[Callable] = None) -> dict:
        """One PresentMon capture; {"status", "exit_code", "duration_seconds", "text", "error"} (text: its CSV)."""
        return self._job({"op": "presentmon", "program": str(program), "args": [str(a) for a in args],
                          "timeout": float(timeout_seconds)}, stop_event, on_started)

    def _job(self, message: dict, stop_event: Optional[threading.Event], on_started: Optional[Callable] = None):
        with self._lock:
            if self._conn is None:
                return {"status": "start_failed", "error": "the administrator helper is not running"}
            parts = []
            try:
                _send(self._conn, message)
                while True:
                    if self._conn.poll(0.2):
                        answer = _recv(self._conn)
                        if answer.get("op") == "started" and on_started is not None:
                            on_started()
                        elif answer.get("op") == "part":
                            parts.append(str(answer.get("text", "")))
                        elif answer.get("op") == "done":
                            if parts:
                                answer["text"] = "".join(parts)
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


def run_presentmon(executable: str, args, cwd: Optional[str], timeout_seconds: float,
                   stop_event: Optional[threading.Event] = None, on_started: Optional[Callable] = None, **kw):
    """run_elevated's place for game captures: through the helper when one runs (no new prompt), else as before.
    The CSV comes back as text and the app itself writes it where it asked (--output_file)."""
    from app.frames.elevated import ElevatedOutcome, run_elevated
    helper = session()
    if helper is None:
        return run_elevated(executable, args, cwd, timeout_seconds, stop_event, on_started=on_started, **kw)
    args = [str(a) for a in args]
    output = args[args.index("--output_file") + 1] if "--output_file" in args else None
    answer = helper.presentmon(executable, args, timeout_seconds, stop_event, on_started)
    if output and answer.get("text"):
        Path(output).write_text(answer["text"], encoding="utf-8")
    status = answer.get("status")
    if status not in ("exited", "timed_out", "stopped"):
        return ElevatedOutcome("start_failed", error="administrator helper: " + str(answer.get("error")))
    return ElevatedOutcome(status, exit_code=answer.get("exit_code"),
                           duration_seconds=float(answer.get("duration_seconds") or 0.0),
                           terminated=None if status == "exited" else True)


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


def presentmon_args(args, output_file: str) -> list:
    """The app's capture flags with the output in our folder; ValueError for anything else."""
    args, out, i = [str(a) for a in args], [], 0
    while i < len(args):
        flag = args[i]
        if flag in PM_FLAGS:
            out.append(flag)
            i += 1
            continue
        if flag not in PM_VALUES or i + 1 >= len(args) or not PM_VALUES[flag].match(args[i + 1]):
            raise ValueError(f"flag not allowed: {flag!r}")
        out += [flag, output_file if flag == "--output_file" else args[i + 1]]
        i += 2
    if "--output_file" not in out or not any(f in out for f in ("--process_name", "--process_id")):
        raise ValueError("no output file or no process to record")
    return out


def _presentmon_job(program: str, args, timeout: float, stop: threading.Event, started: threading.Event,
                    popen=subprocess.Popen, folder_maker=_admin_only_folder) -> dict:
    from app.frames.presentmon import check_presentmon_path
    why = check_presentmon_path(program)
    if why is None and not Path(program).name.lower().startswith("presentmon"):
        why = "not a PresentMon program"
    if why is not None:
        return {"op": "done", "status": "refused", "error": why}
    try:
        command = [program] + presentmon_args(args, "-")
    except ValueError as e:
        return {"op": "done", "status": "refused", "error": str(e)}
    folder = folder_maker()
    try:
        csv_path = folder / f"r{secrets.token_hex(8)}.csv"
        command[command.index("--output_file") + 1] = str(csv_path)
        started_at = time.monotonic()
        try:
            proc = popen(command, cwd=str(folder), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            return {"op": "done", "status": "start_failed", "error": f"could not start: {e}"}
        started.set()
        status = "exited"
        while proc.poll() is None:
            if stop.is_set() or time.monotonic() - started_at >= timeout:
                status = "stopped" if stop.is_set() else "timed_out"
                proc.kill()                 # our own child only
                proc.wait(10)
                break
            time.sleep(0.2)
        try:
            text = csv_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        return {"op": "done", "status": status, "exit_code": proc.returncode,
                "duration_seconds": time.monotonic() - started_at, "text": text}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _send_done(conn, done: dict) -> None:
    text = done.pop("text", None)
    if text:
        for i in range(0, len(text), PART):
            _send(conn, {"op": "part", "text": text[i:i + PART]})
    _send(conn, done)


def serve(conn, job=_passmark_job, presentmon_job=_presentmon_job) -> None:
    """Answer jobs until the app says exit or closes the connection."""
    _send(conn, {"hello": "pcbm-admin-helper", "pid": os.getpid()})
    stop, started = threading.Event(), threading.Event()
    result: Dict[str, dict] = {}
    worker: Optional[threading.Thread] = None
    while True:
        try:
            if not conn.poll(0.2):
                if started.is_set():
                    started.clear()
                    _send(conn, {"op": "started"})
                if worker is not None and not worker.is_alive():
                    _send_done(conn, result.pop("done"))
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
        elif op in ("passmark", "presentmon") and worker is None:
            stop.clear()
            started.clear()
            timeout = float(message.get("timeout") or 1800)
            if op == "passmark":
                run = lambda g=str(message.get("group")), t=timeout: job(g, t, stop)     # noqa: E731
            else:
                run = lambda m=message, t=timeout: presentmon_job(                        # noqa: E731
                    str(m.get("program")), list(m.get("args") or []), t, stop, started)

            def work(run=run):
                try:
                    result["done"] = run()
                except Exception as e:      # noqa: BLE001 - sent back, the helper keeps serving
                    result["done"] = {"op": "done", "status": "start_failed", "error": str(e)}
            worker = threading.Thread(target=work, daemon=True)
            worker.start()
        elif op in ("passmark", "presentmon"):
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
