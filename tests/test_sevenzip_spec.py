import json
from pathlib import Path

from app.automation.cli_workload import TOOLS_DIR, load_tool_specs
from app.automation.parsers import parse_output

# raw output of `7za.exe b` (7-Zip 26.04 x64) on the development computer, 2026-10-18
RAW = (Path(__file__).parent / "data" / "7z_b_26.04_x64.txt").read_text(encoding="utf-8")


def test_spec_is_valid():
    specs, errors = load_tool_specs(TOOLS_DIR)
    spec = next(s for s in specs if s.id == "sevenzip_benchmark")
    assert not [e for e in errors if "sevenzip" in e]
    assert spec.args == ("b",) and spec.verified is True and not spec.requires_elevation


def test_parses_total_rating_from_real_output():
    spec = json.loads((Path(TOOLS_DIR) / "sevenzip_benchmark.json").read_text(encoding="utf-8"))
    result = parse_output(spec["parser"], RAW)
    assert result.error is None and result.score == 173712 and result.unit == "MIPS"
    assert result.metrics["usage_percent"] == 2169 and result.metrics["ru_mips"] == 8005
    assert parse_output(spec["parser"], "ERROR: Can't allocate required memory!").error
