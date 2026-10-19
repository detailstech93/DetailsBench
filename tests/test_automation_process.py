"""run_process with REAL child processes (the current Python), no mocks."""

import os
import sys
import threading
import time

import psutil
import pytest

from app.automation.process import run_process
from app.automation.workload import WorkloadResult

PY = sys.executable


def run(tmp_path, code, timeout=30, stop=None):
    stop = stop or threading.Event()
    return run_process([PY, "-c", code], tmp_path / "out.txt", tmp_path / "err.txt", timeout, stop)


def test_exit_code_and_output_files(tmp_path):
    outcome = run(tmp_path, "import sys; print('hello'); print('oops', file=sys.stderr); sys.exit(3)")
    assert outcome.exit_code == 3 and not outcome.timed_out and not outcome.cancelled
    assert (tmp_path / "out.txt").read_text(encoding="utf-8").strip() == "hello"
    assert (tmp_path / "err.txt").read_text(encoding="utf-8").strip() == "oops"


def test_large_output_does_not_block(tmp_path):
    outcome = run(tmp_path, "print('x' * 5_000_000)")
    assert outcome.exit_code == 0
    assert (tmp_path / "out.txt").stat().st_size >= 5_000_000


def test_timeout_terminates_only_our_tree(tmp_path):
    code = ("import subprocess, sys, time\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(c.pid, flush=True)\n"
            "time.sleep(60)\n")
    start = time.monotonic()
    outcome = run(tmp_path, code, timeout=2)
    assert outcome.timed_out and not outcome.cancelled
    assert time.monotonic() - start < 15
    child_pid = int((tmp_path / "out.txt").read_text(encoding="utf-8").split()[0])
    assert child_pid in outcome.terminated
    time.sleep(0.5)
    assert not psutil.pid_exists(child_pid) or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE


def test_stop_event_cancels(tmp_path):
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    outcome = run(tmp_path, "import time; time.sleep(60)", timeout=None, stop=stop)
    assert outcome.cancelled and not outcome.timed_out
    assert outcome.duration_seconds < 15


def test_missing_executable(tmp_path):
    outcome = run_process([str(tmp_path / "nope.exe")], tmp_path / "o", tmp_path / "e", 5, threading.Event())
    assert outcome.exit_code is None and outcome.start_error


@pytest.mark.parametrize("kwargs", [
    {"status": "failed", "score": 0.0},
    {"status": "completed"},
    {"status": "weird"},
])
def test_workload_result_rejects_inconsistent(kwargs):
    with pytest.raises(ValueError):
        WorkloadResult(**kwargs)


@pytest.mark.skipif(os.name != "nt", reason="Windows consoles only")
def test_a_hidden_console_still_writes_to_the_files(tmp_path):
    out, err = tmp_path / "out.txt", tmp_path / "err.txt"
    r = run_process([sys.executable, "-c", "print('CB 123.4')"], out, err, 30, threading.Event(), hidden_console=True)
    assert r.exit_code == 0 and "CB 123.4" in out.read_text()
