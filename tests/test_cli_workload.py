"""CliWorkload with REAL child processes (this Python) and real spec validation."""

import json
import sys
import threading

import pytest

from app.automation.cli_workload import TOOLS_DIR, CliWorkload, load_tool_specs, load_workloads
from app.automation.tool_spec import validate_spec
from app.automation.workload import RunContext


def spec_for(code, **over):
    data = {"id": "selftest_t", "display_name": "T", "executable": {"python": True}, "args": ["-c", code],
            "timeout_seconds": 20, "parser": {"type": "regex", "pattern": r"^SCORE=(?P<score>\S+)$", "unit": "u"},
            "verified": True}
    data.update(over)
    spec, errors = validate_spec(data)
    assert not errors, errors
    return spec


def run(tmp_path, spec, timeout=None, stop=None):
    ctx = RunContext(output_dir=tmp_path, timeout_seconds=timeout, stop_event=stop or threading.Event())
    return CliWorkload(spec).run(ctx)


def test_completed(tmp_path):
    result = run(tmp_path, spec_for("print('noise'); print('SCORE=12.5')"))
    assert (result.status, result.score, result.unit, result.exit_code) == ("completed", 12.5, "u", 0)
    assert result.command[0] == sys.executable


def test_output_dir_placeholder(tmp_path):
    spec = spec_for("import sys; open(sys.argv[1] + '/f.txt', 'w').write('x'); print('SCORE=1')",
                    args=["-c", "import sys; open(sys.argv[1] + '/f.txt', 'w').write('x'); print('SCORE=1')",
                          "{output_dir}"])
    assert run(tmp_path, spec).status == "completed"
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "x"


def test_bad_exit_code_is_failed_without_score(tmp_path):
    result = run(tmp_path, spec_for("print('SCORE=5'); raise SystemExit(4)"))
    assert (result.status, result.score, result.error) == ("failed", None, "exit code 4")


def test_accepted_nonzero_exit_code(tmp_path):
    assert run(tmp_path, spec_for("print('SCORE=5'); raise SystemExit(1)", success_exit_codes=[0, 1])).score == 5.0


def test_unusable_output_is_invalid(tmp_path):
    for code in ("print('done')", "print('SCORE=0')"):
        result = run(tmp_path, spec_for(code))
        assert result.status == "invalid" and result.score is None and result.error.startswith("output not usable")


def test_timeout(tmp_path):
    result = run(tmp_path, spec_for("import time; time.sleep(30)"), timeout=1)
    assert (result.status, result.score) == ("timed_out", None)
    assert result.error == "timeout after 1 s"


def test_stop(tmp_path):
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    assert run(tmp_path, spec_for("import time; time.sleep(30)"), stop=stop).status == "cancelled"


def test_missing_executable_is_skipped(tmp_path):
    spec = spec_for("", executable={"candidates": ["%NOPE_VAR%\\x.exe", "C:\\missing\\tool.exe"],
                                    "path_names": ["surely-not-installed-tool"]})
    wl = CliWorkload(spec, env={}, exists=lambda p: False, which=lambda n: None)
    assert wl.availability() == (False, "not found")
    result = wl.run(RunContext(output_dir=tmp_path))
    assert result.status == "skipped" and result.score is None


def test_is_test_and_version():
    wl = CliWorkload(spec_for("print(1)"))
    assert wl.is_test is True and wl.version() == sys.version.split()[0]
    assert sys.executable in wl.describe_command()


def test_load_specs_reports_bad_files(tmp_path):
    good = {"id": "tool_ok", "display_name": "ok", "executable": {"python": True}, "timeout_seconds": 5,
            "parser": {"type": "last_number"}, "verified": False}
    (tmp_path / "a.json").write_text(json.dumps(good), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(good), encoding="utf-8")
    (tmp_path / "c.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "d.json").write_text(json.dumps(dict(good, id="BAD ID")), encoding="utf-8")
    specs, errors = load_tool_specs(tmp_path)
    assert [s.id for s in specs] == ["tool_ok"]
    assert [e.split(":")[0] for e in errors] == ["b.json", "c.json", "d.json"]
    assert "duplicate id" in errors[0]


def test_shipped_specs_are_valid():
    workloads, errors = load_workloads(TOOLS_DIR)
    assert errors == []
    assert "selftest_python" in [w.id for w in workloads]


def test_shipped_selftest_runs(tmp_path):   # about 2 seconds
    wl = next(w for w in load_workloads()[0] if w.id == "selftest_python")
    result = wl.run(RunContext(output_dir=tmp_path))
    assert result.status == "completed" and result.score > 0 and result.unit == "loops/s"
