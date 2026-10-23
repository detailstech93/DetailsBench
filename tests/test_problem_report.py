"""Problem reports (owner, 2026-10-23): a zip with what we need to see a failure, nothing personal, nothing sent."""

import json
import logging
import sys
import zipfile
from urllib.parse import parse_qs, urlparse

import pytest

from app import app_log, problem_report as pr

HOME = r"C:\Users\Sami"
NAMES = ["Sami", "SAMI-LAPTOP"]
RUNS = [{"benchmark_name": "fio", "run_index": 1, "status": "failed", "is_warmup": 0,
         "note": r"could not open C:\Users\Sami\AppData\x.dat on SAMI-LAPTOP (sami@mail.com, 192.168.1.28)"},
        {"benchmark_name": "fio", "run_index": 1, "status": "completed", "is_warmup": 1, "note": ""},
        {"benchmark_name": "vray_benchmark_gpu", "run_index": 1, "status": "skipped", "note": "no NVIDIA GPU"},
        {"benchmark_name": "speedometer", "run_index": 1, "status": "timed_out", "note": "timeout after 600 s"}]


def test_scrub_removes_home_names_email_and_ip():
    text = pr.scrub(RUNS[0]["note"] + r" C:/Users/Sami/x 127.0.0.1", home=HOME, names=NAMES)
    assert "Sami" not in text and "SAMI" not in text and "mail.com" not in text and "192.168" not in text
    assert r"~\AppData\x.dat" in text and "~/x" in text and "<user>" in text and "<email>" in text
    assert "<ip>" in text and "127.0.0.1" in text            # localhost says nothing about the tester


def test_settings_lose_anything_secret():
    out = pr.clean_settings({"reports_folder": "D:/r", "remote": {"pairing_code": "1234", "port": 8765},
                             "api_token": "x", "theme": "dark"})
    assert out == {"reports_folder": "D:/r", "remote": {"pairing_code": "<removed>", "port": 8765},
                   "api_token": "<removed>", "theme": "dark"}


def test_only_runs_that_did_not_complete_count():
    assert [r["status"] for r in pr.failed_runs(RUNS)] == ["failed", "timed_out"]     # a skip says why: no problem


def test_the_zip_holds_the_report_log_failed_runs_and_clean_settings(tmp_path):
    data = tmp_path / "data"
    session = data / "automation_runs" / "session_7_20261023_090000"
    (session / "fio_r1").mkdir(parents=True)
    (session / "fio_r1" / "stderr.txt").write_text(r"error in C:\Users\Sami\x", encoding="utf-8")
    (session / "fio_r1" / "big.log").write_text("a" * (pr.MAX_FILE_BYTES + 10) + "END", encoding="utf-8")
    (session / "fio_r1" / "pcbm_storage_test.dat").write_bytes(b"\0" * 10)        # not text: left out
    (session / "jetstream_r1").mkdir()
    (session / "jetstream_r1" / "stdout.txt").write_text("fine", encoding="utf-8")  # completed: left out
    (data / "logs").mkdir()
    (data / "logs" / "detailsbench.log").write_text("started by Sami", encoding="utf-8")
    (data / "logs" / "last_error.txt").write_text("Traceback: boom", encoding="utf-8")
    (data / "settings.json").write_text(json.dumps({"remote_password": "p", "theme": "dark"}), encoding="utf-8")
    path = pr.make_zip(tmp_path / "out", "fio stopped", RUNS, 7, data, {"cpu_brand": "Snapdragon"}, "abc",
                       home=HOME, names=NAMES)
    with zipfile.ZipFile(path) as z:
        names = sorted(z.namelist())
        assert names == ["log/detailsbench.log", "log/last_error.txt", "report.txt", "runs/fio_r1/big.log",
                         "runs/fio_r1/stderr.txt", "settings.json"]
        report = z.read("report.txt").decode()
        assert "fio stopped" in report and "CPU: Snapdragon" in report and "Commit: abc" in report
        assert "fio run 1: failed" in report and "speedometer run 1: timed_out" in report
        assert "Traceback: boom" in report and "vray" not in report
        assert "Sami" not in "".join(z.read(n).decode() for n in names)
        big = z.read("runs/fio_r1/big.log").decode()
        assert big.startswith("[... the first 13 bytes left out ...]") and big.endswith("END")
        assert json.loads(z.read("settings.json")) == {"remote_password": "<removed>", "theme": "dark"}


def test_the_issue_is_prefilled_and_asks_for_the_zip():
    url = pr.issue_url("it hung", pr.failed_runs(RUNS), "report.zip", ["DetailsBench 0.1.0", "OS: Windows"])
    parsed = urlparse(url)
    assert parsed.netloc == "github.com" and parsed.path == f"/{pr.REPO}/issues/new"
    q = parse_qs(parsed.query)
    assert q["title"] == ["Problem: fio, speedometer"] and q["labels"] == ["bug"]
    assert "it hung" in q["body"][0] and "report.zip" in q["body"][0] and "OS: Windows" in q["body"][0]
    long = pr.issue_url("x" * 10000, [], "r.zip", ["a"])
    assert len(parse_qs(urlparse(long).query)["body"][0]) <= pr.ISSUE_BODY_LIMIT + 4


def test_the_log_file_and_an_uncaught_error(tmp_path, monkeypatch):
    root = logging.getLogger()
    before_handlers, before_level = list(root.handlers), root.level
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    seen = []
    try:
        assert app_log.setup(tmp_path, on_error=seen.append) == tmp_path / "logs" / app_log.LOG_NAME
        try:
            raise ValueError("boom")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        for h in root.handlers:
            h.flush()
        assert "boom" in seen[0] and "boom" in app_log.last_error(tmp_path)
        assert "Error the app did not catch" in (tmp_path / "logs" / app_log.LOG_NAME).read_text(encoding="utf-8")
        assert [p.name for p in app_log.log_files(tmp_path)] == [app_log.LOG_NAME, app_log.ERROR_NAME]
    finally:
        for h in root.handlers:
            if h not in before_handlers:
                root.removeHandler(h)
                h.close()
        root.setLevel(before_level)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def test_the_window_makes_the_zip_and_opens_the_issue(qapp, tmp_path, monkeypatch):
    from app.ui import problem_report_ui as ui
    monkeypatch.setattr(ui, "out_dir", lambda window: tmp_path / "out")
    monkeypatch.setattr(ui, "data_dir", lambda: tmp_path / "data")
    opened, shown = [], []
    d = ui.ProblemDialog(None, 7, RUNS, {"cpu_brand": "X"}, open_url=opened.append, show_folder=shown.append)
    d.text.setPlainText("fio stopped")
    path = d.make()
    assert path.is_file() and path.parent == tmp_path / "out"
    assert opened[0].startswith(f"https://github.com/{pr.REPO}/issues/new?") and d.folder_btn.isEnabled()
    d.show_file()
    assert shown == [tmp_path / "out"]


def test_the_bar_offers_a_report(qapp):
    from app.ui.problem_report_ui import ProblemBar
    asked = []
    bar = ProblemBar(lambda: asked.append(1))
    bar.offer("Did not complete: fio.")
    assert not bar.isHidden() and bar.label.text().startswith("Did not complete: fio.")
    bar.report_btn.click()
    assert asked == [1]
    bar.dismiss_btn.click()
    assert bar.isHidden()
