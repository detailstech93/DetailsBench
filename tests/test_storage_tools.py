import sys
import threading
from pathlib import Path

from app.automation.workload import RunContext
from app.storage import disk_tools as dt

DATA = Path(__file__).parent / "data"
# raw outputs from the development computer, 2026-10-18 (computer name redacted)
DISKSPD_XML = (DATA / "diskspd_2.3_seq1m_read.xml").read_text(encoding="utf-8")
FIO_JSON = (DATA / "fio_3.43_rnd4k_read.json").read_text(encoding="utf-8")
SEQ_READ, SEQ_WRITE, RND_READ, RND_WRITE = dt.TESTS


def test_commands_use_documented_options():
    assert dt.diskspd_command("d.exe", SEQ_READ, 1024, 15, 5) == [
        "d.exe", "-c1024M", "-d15", "-W5", "-b1M", "-o8", "-t1", "-w0", "-Sh", "-L", "-Rxml", dt.TEST_FILE]
    assert "-r" in dt.diskspd_command("d", RND_WRITE, 64, 1, 0) and "-w100" in dt.diskspd_command("d", RND_WRITE, 64, 1, 0)
    cmd = dt.fio_command("f.exe", RND_READ, 1024, 15, 5, "out.json")
    assert "--rw=randread" in cmd and "--bs=4k" in cmd and "--iodepth=32" in cmd and "--direct=1" in cmd
    assert f"--filename={dt.TEST_FILE}" in cmd and "--output=out.json" in cmd           # relative: no ':' in it
    assert "--rw=write" in dt.fio_command("f", SEQ_WRITE, 1, 1, 0, "o")


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
