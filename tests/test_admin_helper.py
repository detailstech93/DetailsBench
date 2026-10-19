"""The administrator helper with fake connections, a fake PassMark and a plain folder: nothing runs elevated."""

import json
import sys
import threading
from multiprocessing import Pipe
from pathlib import Path

import pytest

from app.frames import admin_helper


def _talk(conn, message):
    conn.send_bytes(json.dumps(message).encode("utf-8"))
    return json.loads(conn.recv_bytes().decode("utf-8"))


def test_the_helper_does_only_its_fixed_job_and_ends_when_told():
    app_end, helper_end = Pipe()
    jobs = []

    def job(group, timeout, stop):
        jobs.append((group, timeout))
        return {"op": "done", "status": "exited", "exit_code": 0, "csv": "CPU Mark,1\n"}
    t = threading.Thread(target=admin_helper.serve, args=(helper_end,), kwargs={"job": job}, daemon=True)
    t.start()
    assert json.loads(app_end.recv_bytes())["hello"] == "pcbm-admin-helper"
    assert _talk(app_end, {"op": "passmark", "group": "CPU_ALL", "timeout": 60})["csv"] == "CPU Mark,1\n"
    answer = _talk(app_end, {"op": "run", "exe": r"C:\Windows\System32\cmd.exe", "args": ["/c", "whoami"]})
    assert answer["status"] == "refused" and "unknown job" in answer["error"]
    app_end.send_bytes(json.dumps({"op": "exit"}).encode("utf-8"))
    t.join(5)
    assert not t.is_alive() and jobs == [("CPU_ALL", 60.0)]


def test_the_helper_ends_when_the_app_goes_away():
    app_end, helper_end = Pipe()
    t = threading.Thread(target=admin_helper.serve, args=(helper_end,), daemon=True)
    t.start()
    app_end.recv_bytes()
    app_end.close()
    t.join(5)
    assert not t.is_alive()


class FakeProc:
    def __init__(self, command, cwd=None, startupinfo=None):
        self.command, self.returncode = command, None
        script = Path(command[2])
        text = script.read_text(encoding="ascii")
        FakeProc.seen = (command, cwd, text)
        csv_line = next(l for l in text.splitlines() if l.startswith("EXPORTCSV"))
        Path(csv_line.split('"')[1]).write_text("CPU Mark,31245.6\n", encoding="utf-8")
        self.returncode = 0

    def poll(self):
        return self.returncode

    def kill(self):
        pass

    def wait(self, timeout=None):
        return self.returncode


def test_passmark_job_uses_its_own_folder_and_names(tmp_path):
    work = tmp_path / "admin-only"
    program = tmp_path / "PerformanceTest" / "PerformanceTest64.exe"

    def make():
        work.mkdir()
        return work
    answer = admin_helper._passmark_job("CPU_ALL", 60, threading.Event(), popen=FakeProc, folder_maker=make,
                                        program=program)
    assert answer["status"] == "exited" and answer["csv"] == "CPU Mark,31245.6\n"
    command, cwd, text = FakeProc.seen
    assert command[0] == str(program) and command[1] == "/s" and Path(command[2]).parent == work
    assert cwd == str(program.parent) and "RUN CPU_ALL" in text and str(work) in text
    assert not work.exists()                       # the work folder is deleted afterwards
    bad = admin_helper._passmark_job("CPU_ALL\r\nEXECUTE x", 60, threading.Event(), popen=FakeProc,
                                     folder_maker=make, program=program)
    assert bad["status"] == "refused" and not work.exists()


def test_the_app_side_sends_a_job_and_reads_the_answer():
    app_end, helper_end = Pipe()
    t = threading.Thread(target=admin_helper.serve, args=(helper_end,),
                         kwargs={"job": lambda g, t, s: {"op": "done", "status": "exited", "csv": g}}, daemon=True)
    t.start()
    app_end.recv_bytes()
    h = admin_helper.AdminHelper(api=object())
    h._conn = app_end
    assert h.active() and h.passmark("G3D_ALL", 60)["csv"] == "G3D_ALL"
    h.close()
    t.join(5)
    assert not h.active() and not t.is_alive()
    assert h.passmark("CPU_ALL", 60)["status"] == "start_failed"


def test_the_helper_command_and_a_declined_prompt(monkeypatch):
    program, params = admin_helper.helper_command(r"\\.\pipe\pcbm-admin-x", "ab")
    assert program == sys.executable and params.endswith(r"main.py --admin-helper \\.\pipe\pcbm-admin-x ab")
    assert params.startswith("-I ")
    assert admin_helper.helper_main(["not-our-pipe", "ab"]) == 2


@pytest.mark.skipif(sys.platform != "win32", reason="named pipes of multiprocessing are Windows-only")
def test_a_declined_prompt_starts_nothing():
    class Api:
        def shell_execute(self, program, params, cwd, show):
            return None, 1223
    h = admin_helper.AdminHelper(api=Api())
    assert h.start() == "the administrator prompt was declined" and not h.active()
    assert admin_helper.begin(h) == "the administrator prompt was declined" and admin_helper.session() is None
