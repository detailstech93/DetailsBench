"""Simulation scripts: drive a program's own window the way a person would, then read its result.

A script is a JSON file in data/scripts: the program to start and a list of steps run in order:
  launch                                      start the program (no arguments unless "args" lists them)
  wait_window  name, class / title (regex)    wait until one of our processes shows such a window
  keys         window, keys ["alt+t", "b"]    focus that window and press each chord (gap seconds between)
  wait         seconds                        just wait
  wait_control window, control, pattern, min  wait until the control's text matches and group 1 >= min
  read         window, values {name: {control, pattern, unit}}   read the result from the window's controls
The program and everything it started is closed at the end (politely, then by force after a grace
time), whatever happened. A run with a value missing is "invalid"; a key is never sent unless the
focused window is ours (app/simulation/desktop.py); a script whose result was never seen on a real
machine stays "verified": false.
"""

import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional, Tuple

from app.automation.tool_spec import resolve_executable
from app.automation.workload import RunContext, Workload, WorkloadResult
from app.simulation.desktop import default_desktop, parse_chord

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "data" / "scripts"
LOG_FILE = "simulation_log.txt"
CONTROLS_FILE = "window_controls.json"
CLOSE_GRACE_SECONDS = 10.0
POLL_SECONDS = 0.5
STEP_KINDS = ("launch", "wait_window", "keys", "wait", "wait_control", "read")


class StepError(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def validate_script(data) -> List[str]:
    errors = []
    if not isinstance(data, dict):
        return ["a script must be a JSON object"]
    for key in ("id", "display_name", "executable", "steps", "score"):
        if key not in data:
            errors.append(f"missing {key!r}")
    steps = data.get("steps") if isinstance(data.get("steps"), list) else []
    if not steps or steps[0].get("do") != "launch":
        errors.append("the first step must be launch")
    names = set()
    for i, step in enumerate(steps):
        kind = step.get("do") if isinstance(step, dict) else None
        if kind not in STEP_KINDS:
            errors.append(f"step {i + 1}: unknown step {kind!r}")
            continue
        if kind == "wait_window":
            names.add(step.get("name"))
        if kind in ("keys", "wait_control", "read") and step.get("window") not in names:
            errors.append(f"step {i + 1}: window {step.get('window')!r} is not waited for before")
        if kind == "keys" and any(parse_chord(k) is None for k in step.get("keys", [])):
            errors.append(f"step {i + 1}: a key name is not known")
    reads = [n for s in steps if isinstance(s, dict) and s.get("do") == "read" for n in s.get("values", {})]
    if isinstance(data.get("score"), dict) and data["score"].get("value") not in reads:
        errors.append("the score value is not read by any step")
    return errors


class SimulationWorkload(Workload):
    def __init__(self, script: dict, env=None, exists: Callable[[str], bool] = os.path.isfile,
                 which: Callable[[str], Optional[str]] = shutil.which, desktop=None):
        self.script = script
        self.id = script["id"]
        self.display_name = script["display_name"]
        self.benchmark_type = script.get("benchmark_type", "cpu")
        self.timeout_seconds = script.get("timeout_seconds", 600)
        self.verified = bool(script.get("verified", False))
        self.warning = script.get("warning")
        self.env = os.environ if env is None else env
        self.exists = exists
        self.which = which
        self._desktop = desktop

    def executable(self) -> Tuple[Optional[str], str]:
        return resolve_executable(SimpleNamespace(executable=self.script["executable"]), self.env,
                                  self.exists, self.which, sys.executable)

    def availability(self) -> Tuple[bool, str]:
        path, how = self.executable()
        if path is None:
            return False, "not installed (looked in: " + ", ".join(self.script["executable"].get("candidates", [])) + ")"
        if self._desktop is None and os.name != "nt":
            return False, "simulation scripts run on Windows only"
        return True, f"found ({how})"

    def version(self) -> Optional[str]:
        return self.script.get("version")

    def describe_command(self) -> Optional[str]:
        path, _ = self.executable()
        steps = ", ".join(s["do"] for s in self.script["steps"])
        return f"{path or self.script['executable']} driven by a script: {steps}"

    def run(self, ctx: RunContext) -> WorkloadResult:
        path, _ = self.executable()
        if path is None:
            return WorkloadResult("skipped", error="the program is not installed")
        desktop = self._desktop or default_desktop()
        if desktop is None:
            return WorkloadResult("skipped", error="simulation scripts run on Windows only")
        return ScriptRun(self.script, path, desktop, ctx).run()


class ScriptRun:
    def __init__(self, script: dict, exe: str, desktop, ctx: RunContext,
                 clock: Callable[[], float] = time.monotonic):
        self.script, self.exe, self.desktop, self.ctx = script, exe, desktop, ctx
        self.clock = clock
        self.start = clock()
        limit = ctx.timeout_seconds or script.get("timeout_seconds", 600)
        self.deadline = self.start + limit
        self.root = None
        self.procs: Dict[int, object] = {}
        self.windows: Dict[str, int] = {}
        self.values: Dict[str, Tuple[float, Optional[str]]] = {}
        self.log: List[str] = []
        self.out = Path(ctx.output_dir)

    def note(self, text: str):
        self.log.append(f"{self.clock() - self.start:8.1f} s  {text}")

    def pids(self):
        if self.root is not None:
            for p in self.desktop.tree(self.root):
                self.procs.setdefault(p.pid, p)
        return set(self.procs)

    def pause(self, seconds: float):
        if self.ctx.stop_event.wait(seconds):
            raise StepError("cancelled", "stopped by the user")
        if self.clock() > self.deadline:
            raise StepError("timed_out", f"the script did not finish within {self.deadline - self.start:.0f} s")

    def until(self, what: str, timeout: float, check: Callable[[], object]):
        end = min(self.clock() + timeout, self.deadline)
        while True:
            found = check()
            if found:
                return found
            if self.clock() > end:
                raise StepError("timed_out", f"{what} did not happen within {timeout:.0f} s")
            self.pause(POLL_SECONDS)

    def window_matching(self, step):
        cls, title = step.get("class"), step.get("title")
        for win in self.desktop.windows(self.pids()):
            if (cls is None or win.cls == cls) and (title is None or re.search(title, win.title)):
                return win
        return None

    def focused(self, hwnd: int) -> bool:
        if self.desktop.foreground_pid() in self.pids():
            return True
        self.desktop.focus(hwnd)
        self.pause(0.3)
        return self.desktop.foreground_pid() in self.pids()

    def step(self, s: dict):
        kind = s["do"]
        if kind == "launch":
            self.root = self.desktop.start([self.exe] + list(s.get("args", [])), str(Path(self.exe).parent))
            self.note(f"started {Path(self.exe).name} (pid {self.root.pid})")
        elif kind == "wait_window":
            win = self.until(f"window {s['name']!r}", s.get("timeout", 30), lambda: self.window_matching(s))
            self.windows[s["name"]] = win.hwnd
            self.note(f"window {s['name']!r}: {win.cls} {win.title!r} (pid {win.pid})")
        elif kind == "keys":
            for chord in s["keys"]:
                if not self.focused(self.windows[s["window"]]):
                    raise StepError("failed", f"the {s['window']!r} window could not be brought to the front; "
                                              f"no key was sent (to keep keys out of other programs)")
                self.desktop.press(parse_chord(chord))
                self.note(f"pressed {chord}")
                self.pause(s.get("gap", 0.5))
        elif kind == "wait":
            self.pause(s["seconds"])
        elif kind == "wait_control":
            pattern = re.compile(s["pattern"])

            def reached():
                m = pattern.search(self.desktop.controls(self.windows[s["window"]]).get(s["control"], ""))
                return m and float(m.group(1)) >= s.get("min", 0)
            self.until(f"control {s['control']} reaching {s.get('min')}", s.get("timeout", 60), reached)
            self.note(f"control {s['control']} reached {s.get('min')}")
        elif kind == "read":
            controls = self.desktop.controls(self.windows[s["window"]])
            (self.out / CONTROLS_FILE).write_text(json.dumps({str(k): v for k, v in sorted(controls.items())},
                                                             indent=1, ensure_ascii=False), encoding="utf-8")
            for name, spec in s["values"].items():
                m = re.search(spec["pattern"], controls.get(spec["control"], ""))
                if m:
                    self.values[name] = (float(m.group(1).replace(",", "")), spec.get("unit"))
            self.note("read " + ", ".join(f"{k}={v[0]}" for k, v in self.values.items()))

    def close_all(self):
        if self.root is None:
            return
        for win in self.desktop.windows(self.pids()):
            self.desktop.close(win.hwnd)
        end = self.clock() + CLOSE_GRACE_SECONDS
        while self.clock() < end and any(p.is_running() for p in self.procs.values()):
            time.sleep(0.2)
        left = [p for p in self.procs.values() if p.is_running()]
        if left:
            self.note("forced to end pids " + ", ".join(str(p) for p in self.desktop.terminate(left)))
        else:
            self.note("program closed")

    def run(self) -> WorkloadResult:
        self.out.mkdir(parents=True, exist_ok=True)
        status, error = "completed", None
        try:
            for s in self.script["steps"]:
                self.step(s)
        except StepError as e:
            status, error = e.status, str(e)
            self.note(f"{status}: {error}")
        except OSError as e:
            status, error = "failed", f"could not start or drive the program: {e}"
            self.note(error)
        finally:
            self.close_all()
            (self.out / LOG_FILE).write_text("\n".join(self.log) + "\n", encoding="utf-8")
        duration = self.clock() - self.start
        score_name = self.script["score"]["value"]
        if status == "completed" and score_name not in self.values:
            status, error = "invalid", f"the result ({score_name}) was not found in the window"
        if status != "completed":
            return WorkloadResult(status, error=error, duration_seconds=duration, command=[self.exe])
        score, unit = self.values[score_name]
        metrics = {k: v for k, v in self.values.items() if k != score_name}
        return WorkloadResult("completed", score=score, unit=unit or self.script["score"].get("unit"),
                              metrics=metrics, duration_seconds=duration, command=[self.exe])


def load_scripts(directory: Path = SCRIPTS_DIR) -> Tuple[List[SimulationWorkload], List[str]]:
    workloads, errors = [], []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            errors.append(f"{path.name}: {e}")
            continue
        problems = validate_script(data)
        if problems:
            errors.append(f"{path.name}: " + "; ".join(problems))
        else:
            workloads.append(SimulationWorkload(data))
    return workloads, errors
