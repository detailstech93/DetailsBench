import sys
import threading
from pathlib import Path

import pytest

from app.automation.workload import RunContext
from app.storage import disk_tools as dt

DATA = Path(__file__).parent / "data"
# raw outputs from the development computer, 2026-10-18 (computer name redacted)
DISKSPD_XML = (DATA / "diskspd_2.3_seq1m_read.xml").read_text(encoding="utf-8")
FIO_JSON = (DATA / "fio_3.43_rnd4k_read.json").read_text(encoding="utf-8")
SEQ_READ, SEQ_WRITE, RND_READ, RND_WRITE = dt.TESTS


@pytest.fixture(autouse=True)
def no_rest(monkeypatch):
    """No rest between the fake disk runs of these tests (one test below checks the rest itself)."""
    monkeypatch.setattr(dt, "REST_SECONDS", 0)
    monkeypatch.setattr(dt, "_last_end", [None])


def test_commands_use_documented_options():
    assert dt.diskspd_command("d.exe", SEQ_READ, 1024, 15, 5) == [
        "d.exe", "-c1024M", "-d15", "-W5", "-b1M", "-o8", "-t1", "-w0", "-Sh", "-L", "-Rxml", dt.TEST_FILE]
    assert "-r" in dt.diskspd_command("d", RND_WRITE, 64, 1, 0) and "-w100" in dt.diskspd_command("d", RND_WRITE, 64, 1, 0)
    cmd = dt.fio_command("f.exe", RND_READ, 1024, 15, 5, "out.json")
    assert "--rw=randread" in cmd and "--bs=4k" in cmd and "--iodepth=32" in cmd and "--direct=1" in cmd
    assert f"--filename={dt.TEST_FILE}" in cmd and "--output=out.json" in cmd           # relative: no ':' in it
    assert "--rw=write" in dt.fio_command("f", SEQ_WRITE, 1, 1, 0, "o")


def test_fio_uses_the_systems_own_io_engine():
    # the full run on Linux failed with "engine windowsaio not loadable" (2026-10-21)
    assert dt.fio_engine("win32") == "windowsaio"
    assert dt.fio_engine("linux") == "libaio"
    assert dt.fio_engine("darwin") == "posixaio"
    assert "--ioengine=libaio" in dt.fio_command("fio", SEQ_READ, 1, 1, 0, "o", engine="libaio")


def test_parse_real_diskspd_and_fio():
    assert dt.parse_diskspd(DISKSPD_XML, write=False) == ({"mib_s": 6772.26, "iops": 6772.3, "latency_ms": 1.182}, None)
    assert dt.parse_diskspd(DISKSPD_XML, write=True)[1] == "no write bytes in the result"
    assert dt.parse_diskspd("<x", False)[1].startswith("not DiskSpd XML")
    assert dt.parse_fio(FIO_JSON, write=False) == ({"mib_s": 704.22, "iops": 180278.1, "latency_ms": 0.1161}, None)
    assert dt.parse_fio(FIO_JSON, write=True)[1] == "no write bandwidth in the result"
    assert dt.parse_fio("fio: error\n", False)[1].startswith("not fio JSON")


def test_find_tool(tmp_path):
    exe = tmp_path / "Tools" / "DiskSpd" / "amd64" / "diskspd.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert dt.find_tool(dt.DISKSPD, {"USERPROFILE": str(tmp_path)}, which=lambda n: None) == str(exe)
    assert dt.find_tool(dt.FIO, {"USERPROFILE": str(tmp_path)}, which=lambda n: None) is None
    assert dt.find_tool(dt.FIO, {}, which=lambda n: "C:/x/fio.exe") == "C:/x/fio.exe"


class FakeDiskSpd(dt.StorageWorkload):
    """Python plays DiskSpd: prints the real XML for reads and creates the test file like the tool does."""
    def __init__(self, body):
        super().__init__(dt.DISKSPD, exe=sys.executable)
        self.body = body

    def _command(self, test, output):
        return [sys.executable, "-c", self.body, "1" if test.write else "0"]


READ_ONLY = ("import sys\nopen('" + dt.TEST_FILE + "', 'wb').write(b'x')\n"
             "print(open(r'" + str(DATA / "diskspd_2.3_seq1m_read.xml") + "', encoding='utf-8').read())\n")


def test_workload_runs_four_tests_writes_report_and_removes_only_its_file(tmp_path):
    keep = tmp_path / "other.dat"
    keep.write_bytes(b"keep")
    result = FakeDiskSpd(READ_ONLY).run(RunContext(output_dir=tmp_path, timeout_seconds=60))
    assert result.status == "completed" and result.score == 6772.26 and result.unit == "MiB/s"
    assert result.metrics["seq1m_q8t1_read_iops"] == (6772.3, "IOPS")
    assert "seq1m_q8t1_write_mib_s" not in result.metrics           # the fake XML has no writes: absent, not 0
    assert "SEQ1M Q8T1 write" in result.metrics["tests_with_errors"]
    report = (tmp_path / dt.REPORT_FILE).read_text(encoding="utf-8")
    assert "| SEQ1M Q8T1 read | 6,772.26 | 6,772.3 | 1.182 |  |" in report
    assert "| RND4K Q32T1 write | - | - | - | no write bytes in the result |" in report
    assert not (tmp_path / dt.TEST_FILE).exists() and keep.read_bytes() == b"keep"


def test_workload_failures_never_score(tmp_path):
    for name, body, status in (("exit", "import sys; sys.exit(2)", "failed"), ("junk", "print('nope')", "invalid")):
        folder = tmp_path / name
        folder.mkdir()
        result = FakeDiskSpd(body).run(RunContext(output_dir=folder, timeout_seconds=60))
        assert result.status == status and result.score is None
    stop = threading.Event()
    stop.set()
    slow = FakeDiskSpd("import time; time.sleep(30)").run(RunContext(output_dir=tmp_path, stop_event=stop))
    assert slow.status == "cancelled"
    missing = dt.StorageWorkload(dt.FIO, exe="")
    missing.exe = None
    assert missing.availability()[0] is False and missing.run(RunContext(output_dir=tmp_path)).status == "skipped"


class Waits(threading.Event):
    def __init__(self):
        super().__init__()
        self.waited = []

    def wait(self, timeout=None):
        self.waited.append(timeout)
        return self.is_set()


def test_a_disk_run_right_after_another_lets_the_drive_rest(tmp_path, monkeypatch):
    # overnight run 2026-10-20: fio's sequential write 1,513 MiB/s right after DiskSpd (its warm-up: 5,765)
    monkeypatch.setattr(dt, "REST_SECONDS", 120)
    now = [1000.0]
    first, second = FakeDiskSpd(READ_ONLY), FakeDiskSpd(READ_ONLY)
    for wl in (first, second):
        wl.clock = lambda: now[0]
    stop = Waits()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    r1 = first.run(RunContext(output_dir=tmp_path / "a", timeout_seconds=60, stop_event=stop))
    assert r1.status == "completed" and r1.note is None
    assert all(w is None or w < 1 for w in stop.waited)          # the first: no rest (only the tool's polling)
    stop.waited.clear()
    now[0] += 30                                                                     # 30 s later
    r2 = second.run(RunContext(output_dir=tmp_path / "b", timeout_seconds=60, stop_event=stop))
    assert stop.waited[0] == 90 and r2.status == "completed"
    assert r2.note.startswith("the drive rested 90 s before this run")
    now[0] += 500
    stop.set()                                                       # long after: no wait, so stop is not seen here
    third = FakeDiskSpd(READ_ONLY)
    third.clock = lambda: now[0]        # the same fake clock: the real one counts from boot (a PC just turned on
    assert third.run(RunContext(output_dir=tmp_path / "b", timeout_seconds=60,   # could be near 1000 s)
                                stop_event=Waits())).note is None


def test_stop_during_the_rest_cancels(tmp_path, monkeypatch):
    monkeypatch.setattr(dt, "REST_SECONDS", 120)
    monkeypatch.setattr(dt, "_last_end", [dt.StorageWorkload.clock()])
    stop = Waits()
    stop.set()
    r = FakeDiskSpd(READ_ONLY).run(RunContext(output_dir=tmp_path, timeout_seconds=60, stop_event=stop))
    assert r.status == "cancelled" and not (tmp_path / dt.REPORT_FILE).exists()
