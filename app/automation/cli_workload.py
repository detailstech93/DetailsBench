"""External command-line tool as a Workload, driven by its spec file in data/tools/.

Status mapping (a failure never gets a score):
  executable not found -> skipped;  could not start -> failed;
  stop -> cancelled;  timeout -> timed_out;  exit code not accepted -> failed;
  ran fine but no usable score in the output -> invalid;  otherwise completed.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from app.automation.parsers import parse_output
from app.automation.process import run_process
from app.automation.tool_spec import ToolSpec, build_command, resolve_executable, validate_spec
from app.automation.workload import RunContext, Workload, WorkloadResult

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "tools"


class CliWorkload(Workload):
    def __init__(self, spec: ToolSpec, env=None, exists: Callable[[str], bool] = os.path.isfile,
                 which: Callable[[str], Optional[str]] = shutil.which,
                 python_executable: str = sys.executable):
        self.spec = spec
        self.id = spec.id
        self.display_name = spec.display_name
        self.is_test = spec.id.startswith("selftest_")
        self._env = os.environ if env is None else env
        self._exists = exists
        self._which = which
        self._python = python_executable

    def executable(self) -> Tuple[Optional[str], str]:
        return resolve_executable(self.spec, self._env, self._exists, self._which, self._python)

    def availability(self) -> Tuple[bool, str]:
        path, how = self.executable()
        if path is None:
            return False, "not found"
        return True, f"{path} ({how})" + ("" if self.spec.verified else "; spec unverified")

    def version(self) -> Optional[str]:
        if self.spec.executable.get("python"):
            return sys.version.split()[0]
        return None   # read from file metadata in a later phase; never by running the tool

    def describe_command(self) -> Optional[str]:
        path, _ = self.executable()
        if path is None:
            return None
        return subprocess.list2cmdline(build_command(self.spec, path, "<run folder>"))

    def run(self, ctx: RunContext) -> WorkloadResult:
        path, how = self.executable()
        if path is None:
            return WorkloadResult(status="skipped", error=f"{self.spec.display_name}: executable {how}")
        command = build_command(self.spec, path, str(ctx.output_dir))
        out, err = Path(ctx.output_dir) / "stdout.txt", Path(ctx.output_dir) / "stderr.txt"
        timeout = ctx.timeout_seconds if ctx.timeout_seconds is not None else self.spec.timeout_seconds
        outcome = run_process(command, out, err, timeout, ctx.stop_event, cwd=str(ctx.output_dir))
        common = {"command": command, "exit_code": outcome.exit_code,
                  "duration_seconds": round(outcome.duration_seconds, 3),
                  "stdout_path": str(out), "stderr_path": str(err)}
        if outcome.start_error:
            return WorkloadResult(status="failed", error=f"could not start: {outcome.start_error}", **common)
        if outcome.cancelled:
            return WorkloadResult(status="cancelled", error="stopped by user", **common)
        if outcome.timed_out:
            return WorkloadResult(status="timed_out", error=f"timeout after {timeout:g} s", **common)
        if outcome.exit_code not in self.spec.success_exit_codes:
            return WorkloadResult(status="failed", error=f"exit code {outcome.exit_code}", **common)
        parsed = parse_output(self.spec.parser,
                              out.read_text(encoding="utf-8", errors="replace"),
                              err.read_text(encoding="utf-8", errors="replace"))
        if parsed.error:
            return WorkloadResult(status="invalid", error=f"output not usable: {parsed.error}", **common)
        return WorkloadResult(status="completed", score=parsed.score, unit=parsed.unit,
                              metrics=parsed.metrics, **common)


def load_tool_specs(directory: Path = TOOLS_DIR) -> Tuple[List[ToolSpec], List[str]]:
    """All valid specs in directory (*.json, sorted by file name) and one error line per bad file."""
    specs, errors = [], []
    seen = set()
    for path in sorted(Path(directory).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            errors.append(f"{path.name}: {e}")
            continue
        spec, problems = validate_spec(data)
        if problems:
            errors.append(f"{path.name}: " + "; ".join(problems))
        elif spec.id in seen:
            errors.append(f"{path.name}: duplicate id {spec.id}")
        else:
            seen.add(spec.id)
            specs.append(spec)
    return specs, errors


def load_workloads(directory: Path = TOOLS_DIR) -> Tuple[List[CliWorkload], List[str]]:
    specs, errors = load_tool_specs(directory)
    return [CliWorkload(s) for s in specs], errors
