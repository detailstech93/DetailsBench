"""Simulation scripts: drive a program's own window the way a person would, then read its result.

A script is a JSON file in data/scripts: the program to start and a list of steps run in order:
  launch                                      start the program (no arguments unless "args" lists them)
  wait_window  name, class / title (regex)    wait until one of our processes shows such a window
  keys         window, keys ["alt+t", "b"]    focus that window and press each chord (gap seconds between)
  wait         seconds                        just wait
  wait_control window, control, pattern, min  wait until the control's text matches and group 1 >= min
  read         window, values {name: {control, pattern, unit}}   read the result from the window's controls
  launch_steam args                           a game ("steam_app" in the script, found in the local Steam
                                              libraries) through app/steam/launch.py; its processes are the
                                              new ones in its install folder, closed with app/frames/game_close.py
  screenshot   name                           save the screen as screenshot_<name>.png in the run folder
  wait_file    name, glob (%VARS%)            wait for a file written after the start (the game's own result
                                              file); a copy goes to the run folder
  read_json    file, values {name: {path [keys], unit}}   read numbers from that file; optional duration
               [keys]: how long the measured part lasted (it ended when the file appeared: telemetry is
               summarized over those seconds only); optional
               settings {label: {path} | {paths, join} | {any_true {name: path}, none}}: the settings the
               game says it used, kept as text with the run ("Resolution 2560x1440, Upscaler DLSS")
  read_frames  file, csv, column              the game's own frame-time file next to that result file:
               optional shown {column: [keys]}: with frame generation on (that key true in the result
               file read by read_json), each row is one rendered frame and that column the time each of
               the frames it became stayed on screen (rendered / shown of them: 4 with 4x, 1 at a camera
               cut): 1% low is taken over those shown frames (so it matches the game's average) and the
               rendered frames give rendered_fps
                                              1% and 0.1% low FPS (app/frames/stats.py), copied to the run folder
A Steam game with a settings adapter gets the settings chosen for it (a preset from the Tests screen, saved in
settings.json) written before the launch and the user's own put back after the run (ForcedSettings).
The program and everything it started is closed at the end (politely, then by force after a grace
time), whatever happened. A run with a value missing is "invalid"; a key is never sent unless the
focused window is ours (app/simulation/desktop.py); a script whose result was never seen on a real
machine stays "verified": false.
"""

import glob
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional, Tuple

from app.automation.tool_spec import expand_vars, resolve_executable
from app.automation.workload import RunContext, Workload, WorkloadResult
from app.simulation.desktop import default_desktop, parse_chord

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "data" / "scripts"
LOG_FILE = "simulation_log.txt"
CONTROLS_FILE = "window_controls.json"
CLOSE_GRACE_SECONDS = 10.0
POLL_SECONDS = 0.5
ENDED_GRACE_SECONDS = 10.0    # a launcher may end before the game it starts shows up: wait before "ended"
STEP_KINDS = ("launch", "launch_steam", "wait_window", "keys", "wait", "wait_control", "read", "screenshot",
              "wait_file", "read_json", "read_frames")


class StepError(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def validate_script(data) -> List[str]:
    errors = []
    if not isinstance(data, dict):
        return ["a script must be a JSON object"]
    for key in ("id", "display_name", "steps", "score"):
        if key not in data:
            errors.append(f"missing {key!r}")
    steam = data.get("steam_app")
    if steam is None and "executable" not in data:
        errors.append("missing 'executable' (or 'steam_app')")
    steps = data.get("steps") if isinstance(data.get("steps"), list) else []
    first = "launch_steam" if steam is not None else "launch"
    if not steps or not isinstance(steps[0], dict) or steps[0].get("do") != first:
        errors.append(f"the first step must be {first}")
    names, files = set(), set()
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
        if kind == "wait_file":
            files.add(step.get("name"))
        if kind in ("read_json", "read_frames") and step.get("file") not in files:
            errors.append(f"step {i + 1}: file {step.get('file')!r} is not waited for before")
    reads = [n for s in steps if isinstance(s, dict) and s.get("do") in ("read", "read_json")
             for n in s.get("values", {})]
    if isinstance(data.get("score"), dict) and data["score"].get("value") not in reads:
        errors.append("the score value is not read by any step")
    return errors


class SimulationWorkload(Workload):
    def __init__(self, script: dict, env=None, exists: Callable[[str], bool] = os.path.isfile,
                 which: Callable[[str], Optional[str]] = shutil.which, desktop=None,
                 find_game: Optional[Callable[[int], Optional[str]]] = None, steam_hooks=None,
                 data_dir: Optional[Path] = None, forced_factory=None):
        self.script = script
        self.data_dir = data_dir                    # settings.json and learned game values (None: the user's)
        self.forced_factory = forced_factory        # tests swap in a fake ForcedSettings
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
        self.find_game = find_game or find_steam_game
        self.steam_hooks = steam_hooks

    def executable(self) -> Tuple[Optional[str], str]:
        """The program, or for a Steam game its install folder."""
        if self.script.get("steam_app") is not None:
            folder = self.find_game(int(self.script["steam_app"]))
            return (folder, "Steam library") if folder else (None, "not installed")
        return resolve_executable(SimpleNamespace(executable=self.script["executable"]), self.env,
                                  self.exists, self.which, sys.executable)

    def availability(self) -> Tuple[bool, str]:
        path, how = self.executable()
        if path is None and self.script.get("steam_app") is not None:
            return False, f"Steam game {self.script['steam_app']} is not installed"
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
        what = path or self.script.get("executable") or f"Steam app {self.script.get('steam_app')}"
        return f"{what} driven by a script: {steps}"

    def run(self, ctx: RunContext) -> WorkloadResult:
        path, _ = self.executable()
        if path is None:
            return WorkloadResult("skipped", error="the program is not installed")
        # scripts drive Win32 windows and dialogs: Windows only (the X11 desktop only serves menu steps)
        desktop = self._desktop or (default_desktop() if os.name == "nt" else None)
        if desktop is None:
            return WorkloadResult("skipped", error="simulation scripts run on Windows only")
        forced = self._forced(ctx)
        if forced is not None:
            try:
                rec = forced.apply()
            except (OSError, ValueError) as e:
                forced.finish()                     # puts back whatever was written before the error
                return WorkloadResult("failed", error=f"the game settings could not be set: {e}")
            problem = settings_problem(rec)
            if problem:
                forced.finish()
                return WorkloadResult("failed", error=problem)
        try:
            result = ScriptRun(self.script, path, desktop, ctx, env=self.env,
                               steam=self.steam_hooks or SteamHooks()).run()
        finally:
            if forced is not None:
                forced.finish()                     # the game is closed by now: check, then put the user's back
        if forced is not None:
            result = with_settings_record(result, forced.record, Path(ctx.output_dir))
        return result

    def _forced(self, ctx: RunContext):
        """ForcedSettings for the values chosen for this game (Tests screen preset), or None."""
        app_id = self.script.get("steam_app")
        if app_id is None:
            return None
        from app.games.settings_file import adapter_for, saved_choice
        from app.paths import get_data_dir, load_settings
        if adapter_for(app_id) is None:
            return None
        data_dir = Path(self.data_dir) if self.data_dir is not None else get_data_dir()
        values = saved_choice(load_settings(data_dir)[0], app_id)
        if not values:
            return None
        from app.games.settings_file import ForcedSettings
        return (self.forced_factory or ForcedSettings)(app_id, values, Path(ctx.output_dir), data_dir)


def settings_problem(rec) -> Optional[str]:
    """Why the game must not start with these settings (not written as asked), or None. Values the preset
    could not map were never requested; a value the adapter refuses fails the run instead of being skipped."""
    if rec.refused:
        return "settings not set: " + "; ".join(f"{k}: {why}" for k, why in rec.refused.items())
    if rec.mismatch_before_launch:
        return "settings not written as asked: " + ", ".join(rec.mismatch_before_launch)
    return None


def with_settings_record(result: WorkloadResult, record, out: Path) -> WorkloadResult:
    """Keep the record in the run folder; a completed run whose settings the game changed is not valid."""
    try:
        (out / "game_settings.json").write_text(json.dumps(record.to_dict(), indent=1, ensure_ascii=False),
                                                encoding="utf-8")
    except OSError:
        pass
    forced = "Forced: " + ", ".join(f"{k} {v}" for k, v in record.requested.items())
    if record.restored is False:
        forced += "; your own settings were NOT put back: " + "; ".join(record.notes)
    if result.status == "completed" and record.mismatch_after_run:
        return WorkloadResult("invalid", error="the game did not keep the forced settings: " + ", ".join(
            f"{k} {exp} -> {act}" for k, (exp, act) in record.mismatch_after_run.items()) + ". " + forced,
            duration_seconds=result.duration_seconds, command=result.command)
    result.note = "; ".join(x for x in (result.note, forced) if x)
    if result.status != "completed":
        result.error = "; ".join(x for x in (result.error, forced) if x)
    return result


def find_steam_game(appid: int) -> Optional[str]:
    """Install folder of an installed Steam game, from the local libraries (registry read only)."""
    if os.name != "nt":
        return None
    try:
        import winreg
        from app.steam.library import library_paths, scan_library
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            root = winreg.QueryValueEx(key, "SteamPath")[0]
        for library in library_paths(root)[0]:
            for game in scan_library(library, "windows")[0]:
                if game["app_id"] == str(appid) and game["installed"]:
                    return game["install_path"]
    except (OSError, ValueError):
        return None
    return None


class SteamHooks:
    """How a Steam game is started, listed and closed (the same code the frame capture uses)."""

    def launch(self, appid, folder, args, timeout, clock):
        from app.benchmark_manager import _get_process_snapshot
        from app.steam.launch import launch_and_confirm
        return launch_and_confirm(appid, folder, [str(appid)], snapshot=_get_process_snapshot,
                                  timeout=timeout, poll=1.0, clock=clock, args=args)

    def folder_procs(self, folder):
        from app.frames.game_close import processes_in_folder
        return processes_in_folder(folder)

    def close(self, started):
        from app.frames.game_close import close_game
        return close_game(started)

    def alive(self, info) -> bool:
        from app.frames.game_close import is_alive
        return is_alive(info)


class ScriptRun:
    def __init__(self, script: dict, exe: str, desktop, ctx: RunContext,
                 clock: Callable[[], float] = time.monotonic, env=None, steam: Optional[SteamHooks] = None):
        self.script, self.exe, self.desktop, self.ctx = script, exe, desktop, ctx
        self.env = os.environ if env is None else env
        self.steam = steam or SteamHooks()
        self.clock = clock
        self.start = clock()
        self.wall_start = time.time()
        self.before: Optional[dict] = None          # Steam game: folder processes before the launch
        self.started: Dict[int, object] = {}        # Steam game: processes the launch started
        self.files: Dict[str, Path] = {}
        limit = ctx.timeout_seconds or script.get("timeout_seconds", 600)
        self.deadline = self.start + limit
        self.root = None
        self.procs: Dict[int, object] = {}
        self.windows: Dict[str, int] = {}
        self.values: Dict[str, Tuple[float, Optional[str]]] = {}
        self.settings_used: List[str] = []          # "Label value" the game reported using
        self.sources: Dict[str, Path] = {}          # wait_file name -> the game's own file
        self.found_at: Dict[str, float] = {}        # wait_file name -> seconds after the start it appeared
        self.parsed: Dict[str, object] = {}         # wait_file name -> its JSON (read_json)
        self.window: Optional[Tuple[float, float]] = None
        self.log: List[str] = []
        self.out = Path(ctx.output_dir)

    def note(self, text: str):
        self.log.append(f"{self.clock() - self.start:8.1f} s  {text}")

    def pids(self):
        if self.root is not None:
            for p in self.desktop.tree(self.root):
                self.procs.setdefault(p.pid, p)
        if self.before is not None:
            for pid, info in self.steam.folder_procs(self.exe).items():
                if pid not in self.before:
                    self.started.setdefault(pid, info)
        return set(self.procs) | set(self.started)

    def pause(self, seconds: float):
        if self.ctx.stop_event.wait(seconds):
            raise StepError("cancelled", "stopped by the user")
        if self.clock() > self.deadline:
            raise StepError("timed_out", f"the script did not finish within {self.deadline - self.start:.0f} s")

    def until(self, what: str, timeout: float, check: Callable[[], object]):
        end = min(self.clock() + timeout, self.deadline)
        ended_since = None
        while True:
            found = check()
            if found:
                return found
            if not self.program_ended():
                ended_since = None
            elif ended_since is None:
                ended_since = self.clock()
            if ended_since is not None and self.clock() - ended_since >= ENDED_GRACE_SECONDS:
                raise StepError("failed", f"the program ended before {what} happened (closed or crashed)")
            if self.clock() > end:
                raise StepError("timed_out", f"{what} did not happen within {timeout:.0f} s")
            self.pause(POLL_SECONDS)

    def program_ended(self) -> bool:
        """Every process the script started has ended (a Steam game: every new one in its folder)."""
        self.pids()
        if self.root is not None and self.procs:
            return not any(p.is_running() for p in self.procs.values())
        if self.before is not None and self.started:
            return not any(self.steam.alive(info) for info in self.started.values())
        return False

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
        elif kind == "launch_steam":
            appid = self.script["steam_app"]
            self.before = self.steam.folder_procs(self.exe)
            res = self.steam.launch(appid, self.exe, list(s.get("args", [])), s.get("timeout", 120),
                                    lambda: float("inf") if self.ctx.stop_event.is_set() else self.clock())
            if self.ctx.stop_event.is_set():
                raise StepError("cancelled", "stopped by the user while the game was starting")
            if res.get("status") != "launched":
                raise StepError("failed", f"the game did not start: {res.get('message')}")
            self.pids()
            self.note(f"started Steam app {appid}: {res.get('message')}")
        elif kind == "screenshot":
            name = f"screenshot_{s['name']}.png"
            try:
                self.desktop.screenshot(self.out / name)
                self.note(f"saved {name}")
            except Exception as e:          # evidence only: a failed screenshot does not fail the run
                self.note(f"screenshot {name} failed: {e}")
        elif kind == "wait_file":
            pattern, problem = expand_vars(s["glob"], self.env)
            if problem:
                raise StepError("failed", f"result file location unknown: {problem}")

            def newest():
                found = [f for f in glob.glob(pattern) if os.path.getmtime(f) >= self.wall_start - 1]
                return max(found, key=os.path.getmtime) if found else None
            path = Path(self.until(f"result file {s['name']!r}", s.get("timeout", 600), newest))
            self.found_at[s["name"]] = self.clock() - self.start
            self.pause(s.get("settle", 2))                  # let the game finish writing it
            copy = self.out / f"result_{path.parent.name}_{path.name}"
            shutil.copyfile(path, copy)
            self.files[s["name"]] = copy
            self.sources[s["name"]] = path
            self.note(f"result file {s['name']!r}: {path.name} (copied to {copy.name})")
        elif kind == "read_json":
            try:
                data = json.loads(self.files[s["file"]].read_text(encoding="utf-8-sig"))
            except ValueError as e:
                raise StepError("invalid", f"the result file is not JSON: {e}")
            self.parsed[s["file"]] = data
            for name, spec in s["values"].items():
                value = data
                for key in spec["path"]:
                    value = value.get(key) if isinstance(value, dict) else None
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self.values[name] = (float(value), spec.get("unit"))
            self.note("read " + ", ".join(f"{k}={v[0]}" for k, v in self.values.items()))
            seconds = _at(data, s["duration"]) if "duration" in s else None
            if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and 0 < seconds:
                end = self.found_at.get(s["file"])
                if end is not None:
                    self.window = (max(0.0, end - float(seconds) - POLL_SECONDS), end)
                    self.note(f"measured part: {self.window[0]:.0f}-{self.window[1]:.0f} s ({seconds:.0f} s)")
            self.settings_used = [f"{label}: {text}" for label, text in
                                  ((label, settings_text(data, spec)) for label, spec in s.get("settings", {}).items())
                                  if text is not None]
            if self.settings_used:
                self.note("settings used: " + ", ".join(self.settings_used))
        elif kind == "read_frames":
            source = self.sources[s["file"]].parent / s["csv"]
            if not source.is_file():
                self.note(f"no frame-time file {s['csv']} next to the result: 1% low not measured")
                return
            copy = self.out / f"frames_{source.parent.name}_{source.name}"
            shutil.copyfile(source, copy)
            text = copy.read_text(encoding="utf-8-sig", errors="replace")
            times = None
            for shown, flag in (s.get("shown") or {}).items():
                if _at(self.parsed.get(s["file"]), flag) is True:
                    rendered = frame_times(text, s["column"])
                    if rendered:
                        self.values["rendered_fps"] = (1000.0 * len(rendered) / sum(rendered), "FPS")
                    times = shown_frames(text, s["column"], shown)
                    self.note(f"frame generation on: {len(times)} frames shown on screen ({shown!r}) from "
                              f"{len(rendered)} rendered")
                    break
            if times is None:
                times = frame_times(text, s["column"])
            if len(times) < 100:
                self.note(f"{source.name}: only {len(times)} frame times: 1% low not measured")
                return
            from app.frames.stats import low_fps
            self.values["low_1pct_fps"] = (low_fps(times, 0.01), "FPS")
            self.values["low_01pct_fps"] = (low_fps(times, 0.001), "FPS")
            self.note(f"{source.name}: {len(times)} frames, 1% low {self.values['low_1pct_fps'][0]:.1f} FPS, "
                      f"0.1% low {self.values['low_01pct_fps'][0]:.1f} FPS")
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
        if self.before is not None:
            self.pids()
            if self.started:
                self.note(self.steam.close(self.started))
            else:
                self.note("no game process to close")
        if self.root is None:
            return
        for win in self.desktop.windows(set(self.procs) | {p.pid for p in self.desktop.tree(self.root)}):
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
                              metrics=metrics, duration_seconds=duration, command=[self.exe],
                              note=self.result_note(), measured_window=self.window)

    def result_note(self) -> Optional[str]:
        parts = []
        if self.settings_used:
            parts.append("Settings used: " + ", ".join(self.settings_used))
        if self.window is not None:
            parts.append(f"Readings: the benchmark scene only ({self.window[1] - self.window[0]:.0f} s)")
        return "; ".join(parts) or None


def _at(data, path):
    for key in path:
        data = data.get(key) if isinstance(data, dict) else None
    return data


def _plain(value) -> Optional[str]:
    if isinstance(value, bool):
        return "On" if value else "Off"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float, str)):
        return str(value)
    return None


def settings_text(data, spec: dict) -> Optional[str]:
    """One setting the game reported: a value, values joined ("2560x1440"), or the names that are true."""
    if "any_true" in spec:
        on = [name for name, path in spec["any_true"].items() if _at(data, path) is True]
        factor = spec.get("factor")         # frame generation: "DLSS 4x" (frames shown per rendered frame)
        if factor and on == [factor.get("only")]:
            count = _at(data, factor["path"]) if _at(data, factor.get("when", [])) is True else None
            n = count + factor.get("add", 0) if isinstance(count, int) and not isinstance(count, bool) else \
                factor.get("otherwise")
            if n:
                return f"{on[0]} {n}x"
        return ", ".join(on) if on else spec.get("none", "Off")
    if "paths" in spec:
        parts = [_plain(_at(data, p)) for p in spec["paths"]]
        return spec.get("join", " ").join(parts) if all(parts) else None
    return _plain(_at(data, spec.get("path", [])))


def shown_frames(text: str, rendered_column: str, shown_column: str) -> List[float]:
    """With frame generation: every frame shown on screen (ms). A row is one rendered frame that became
    round(rendered / shown) frames of the shown time each (e.g. 4 of 3.3 ms with 4x, 1 at a camera cut)."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = [h.strip().lower() for h in lines[0].split(",")]
    if rendered_column.strip().lower() not in header or shown_column.strip().lower() not in header:
        return []
    r, s = header.index(rendered_column.strip().lower()), header.index(shown_column.strip().lower())
    out: List[float] = []
    for line in lines[1:]:
        cells = line.split(",")
        try:
            rendered, shown = float(cells[r]), float(cells[s])
        except (IndexError, ValueError):
            continue
        if rendered > 0 and shown > 0:
            out += [shown] * max(1, round(rendered / shown))
    return out


def frame_times(text: str, column: str) -> List[float]:
    """Positive frame times (ms) from a CSV with a header row; the column is found by name."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = [h.strip().lower() for h in lines[0].split(",")]
    if column.strip().lower() not in header:
        return []
    i = header.index(column.strip().lower())
    out = []
    for line in lines[1:]:
        cells = line.split(",")
        try:
            value = float(cells[i])
        except (IndexError, ValueError):
            continue
        if value > 0:
            out.append(value)
    return out


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
