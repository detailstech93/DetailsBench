"""Benchmark one Steam game end to end: start PresentMon, launch the game, capture, close it, store.

One UAC prompt per benchmark: PresentMon starts elevated ONCE, before the game, filtered to every exe
in the game's install folder (--process_name repeated; the game's exact exe is known only after it
starts) with --terminate_on_proc_exit. Then the game is launched through Steam (app/steam/launch.py
rules: the appid comes from a local manifest, a new process inside the install folder confirms it).

PresentMon keeps its CSV locked while it writes (checked on 2026-10-17: reading it gave "Permission
denied"), so nothing is read live. The app waits by the clock for the settle time plus every window
(plus 15 % and 20 s for loading), then closes the game (app/frames/game_close.py: only the processes
the launch started inside the install folder, politely first, by force after 30 s). PresentMon then ends
by itself and writes the whole CSV. The game's process is the started one with the most frames. Game
time is the sum of its frame times; the recording is cut into warmup and measured windows
(app/frames/segments.py), each stored as one run (score = average FPS). A window the recording does
not reach is "invalid" without a score. Telemetry samples are cut into the same windows, aligned on
the moment the game was asked to close (approximate, about one second).

A built-in benchmark reached through menu steps is measured whole instead (owner, 2026-10-23, after reading how
reviewers measure: the game's own result covers its whole scripted scene, and a part of it is a different scene):
the game is started once per run, each run measures the benchmark from its start for its length, the first run is
the warm-up (shaders and files loaded the first time) and is not counted. One PresentMon records every run.
"""

import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from app.automation.workload import RunContext, Workload, WorkloadResult
from app.frames.elevated import run_elevated
from app.frames.presentmon import CSV_NAME, START_MARGIN_SECONDS, capture_game_args, workload_id_for
from app.frames.presentmon_csv import parse_presentmon_csv, summarize_processes
from app.frames.segments import (
    first_frame_offset, game_seconds, pick_game_process, slice_samples, split_frames, window_bounds,
)
from app.frames.stats import compute_frame_stats, stats_to_metrics

logger = logging.getLogger(__name__)

UAC_WAIT_SECONDS = 120.0          # time to answer the UAC prompt
LAUNCH_TIMEOUT_SECONDS = 120.0    # Steam may show its own dialog or update check first
LOAD_FACTOR, LOAD_MARGIN_SECONDS = 1.15, 20.0   # wall time waited per second of needed game time, plus margin
MAX_EXE_NAMES = 200
LOG_NAME = "capture_log.txt"
FRONT_WAIT_SECONDS = 600.0         # longest wait for the game's window before keys and route count anyway
ROUTE_POLL_SECONDS = 0.05         # a route's held keys and mouse moves need finer timing than 1 s
REMOTE_TEXT = ("captured in a Remote Desktop session: the game drew to the remote screen (32 Hz on the owner's "
               "RDP on 2026-10-19, with a stall of about 8 s every 30 s), so its FPS is not this PC's")


MARK_SETTLE_SECONDS = 5           # after the benchmark's own start is seen, only its first seconds are skipped
MIN_WINDOW_SECONDS = 20
# after the measured time of a built-in benchmark: the game's own results screen is kept as a screenshot for the
# report (owner, 2026-10-23: the result the game showed, not its first frame); words that tell it is there
RESULT_WORDS = ("result", "average", "avg fps", "score")
RESULT_WAIT_SECONDS = 60.0
RESULT_LOOK_SECONDS = 2.0
RESULT_SHOT = "benchmark_result.png"
CLOSE_SECONDS = 60.0              # closing a game: politely, by force after 30 s (app/frames/game_close.py)
PASS_GAP_SECONDS = 10.0           # between two runs of a whole benchmark: Steam sees the game closed first


def steps_bound_seconds(steps) -> int:
    """The longest the menu steps can take (every word waited for to its limit, every pause and hold): MangoHud
    must still be logging when the benchmark starts that late."""
    total = 0.0
    for s in steps or []:
        total += float(s.get("within", 0)) + float(s.get("wait", 0)) + float(s.get("settle", 0))
        total += float(s.get("seconds", 1.0)) if "hold" in s else 0.0
        total += 0.5 * len([c for c in str(s.get("press", "")).split(",") if c.strip()])
    return int(total) + 60


def fit_to_benchmark(spec: "GameBenchmarkSpec", length: int) -> Optional[str]:
    """A built-in benchmark runs `length` s after its start mark, then shows its results screen: every window
    must end before that, or the results screen's FPS would be measured. Shortens the settle time and, when
    needed, each window. Returns what was changed (None when it already fit); a run with too many windows to
    fit even at MIN_WINDOW_SECONDS keeps them, and the last ones come out invalid (past the benchmark)."""
    notes = []
    if spec.settle_seconds > MARK_SETTLE_SECONDS:
        spec.settle_seconds = MARK_SETTLE_SECONDS
    room = length - spec.settle_seconds
    if spec.seconds * spec.count > room:
        seconds = max(MIN_WINDOW_SECONDS, int(room // spec.count))
        notes.append(f"each window {seconds} s instead of {spec.seconds} s, so the {spec.count} windows fit in "
                     f"the game's benchmark (about {length} s)")
        spec.seconds = seconds
        if seconds * spec.count > room:
            notes.append(f"only {int(room // seconds)} of {spec.count} windows fit; fewer runs would all count")
    return "; ".join(notes) or None


def is_remote_session() -> bool:
    """True inside a Remote Desktop session (GetSystemMetrics SM_REMOTESESSION). Linux: False."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetSystemMetrics(0x1000))
    except Exception:
        return False


def game_exe_names(folder: str, walk: Callable = os.walk, limit: int = MAX_EXE_NAMES) -> List[str]:
    """Distinct .exe file names anywhere in the game's folder (case-insensitive), sorted."""
    names = {}
    for _, _, files in walk(folder):
        for name in files:
            if name.lower().endswith(".exe"):
                names.setdefault(name.lower(), name)
        if len(names) >= limit:
            break
    return sorted(names.values(), key=str.lower)[:limit]


@dataclass
class GameBenchmarkSpec:
    game_name: str
    app_id: str
    install_path: str
    discovered_appids: List[str]
    presentmon_path: str
    seconds: int = 60
    warmup_runs: int = 1
    repetitions: int = 3
    settle_seconds: int = 30
    min_frames: int = 100
    launch_args: List[str] = field(default_factory=list)   # from the game's adapter (app/games)
    start_keys: List[Tuple[float, str]] = field(default_factory=list)   # (s after launch, chord): data/game_start_keys
    route: Optional[dict] = None        # keys held and mouse moves during the capture: app/frames/route.py
    steps: Optional[list] = None        # checked menu steps to a built-in benchmark: app/frames/menu_steps.py
    benchmark_seconds: Optional[int] = None     # the built-in benchmark's length after its start mark
    version: Optional[str] = None       # the game's build: Steam's buildid from its appmanifest (app/steam/library)
    relaunches: bool = False            # its first process ends and its launcher starts it again (Far Cry 6)

    @property
    def count(self) -> int:
        return self.warmup_runs + self.repetitions

    def windows(self) -> List[Tuple[float, float]]:
        return window_bounds(self.settle_seconds, self.seconds, self.count)

    def wait_seconds(self) -> float:
        """Wall time to wait after the launch: settle plus every window, with room for loading."""
        return (self.settle_seconds + self.seconds * self.count) * LOAD_FACTOR + LOAD_MARGIN_SECONDS

    def max_record_seconds(self) -> int:
        """PresentMon's --timed upper limit; normally it ends earlier, when the game is closed."""
        if self.whole_benchmark:
            return int(UAC_WAIT_SECONDS + self.pass_seconds() * self.count + 120)
        front = FRONT_WAIT_SECONDS if self.route or self.start_keys or self.steps else 0
        return int(UAC_WAIT_SECONDS + LAUNCH_TIMEOUT_SECONDS + front + self.steps_seconds() + self.wait_seconds() + 120)

    @property
    def whole_benchmark(self) -> bool:
        """A built-in benchmark reached by menu steps with a known length: measured whole, once per launch."""
        return bool(self.steps and self.benchmark_seconds)

    def pass_seconds(self) -> float:
        """Longest one launch of a whole benchmark can take, from the launch to the game closed."""
        return (LAUNCH_TIMEOUT_SECONDS + FRONT_WAIT_SECONDS + self.steps_seconds()
                + (self.benchmark_seconds or 0) * LOAD_FACTOR + RESULT_WAIT_SECONDS + CLOSE_SECONDS + PASS_GAP_SECONDS)

    def steps_seconds(self) -> float:
        """Longest time the menu steps can take before the benchmark starts."""
        total = 0.0
        for s in self.steps or []:
            total += float(s.get("within", 0)) + float(s.get("wait", 0)) + float(s.get("seconds", 0)) + 5
        return total


@dataclass
class WindowOutcome:
    is_warmup: bool
    run_index: int
    result: WorkloadResult
    samples: list = field(default_factory=list)
    frames: list = field(default_factory=list)    # frame times (ms) of the window: its FPS over time in the report


@dataclass
class GameCapture:
    status: str                                   # "recorded" (windows below), "cancelled", "failed"
    message: str = ""
    process: Optional[Tuple[str, Optional[int]]] = None
    windows: List[WindowOutcome] = field(default_factory=list)
    command: Optional[list] = None
    telemetry_unavailable: dict = field(default_factory=dict)
    display_mode: Optional[str] = None            # the screen's mode at the end of the benchmark ("1920x1080 at 60 Hz")


def presentmon_text(out, ended_after: Optional[float]) -> str:
    """Why there may be no CSV: how PresentMon ended (it runs elevated, so its own messages cannot be read)."""
    parts = []
    if ended_after is not None:
        parts.append(f"PresentMon ended by itself {ended_after:.0f} s after the game was launched, before the "
                     "app closed the game")
    if out is not None:
        how = {"exited": "exit code {}".format(getattr(out, "exit_code", None)),
               "stopped": "stopped by the app", "timed_out": "stopped at its time limit"}.get(out.status, out.status)
        parts.append(f"PresentMon: {how} after {getattr(out, 'duration_seconds', 0) or 0:.0f} s"
                     + (f" ({out.error})" if getattr(out, "error", None) else ""))
    return " (" + "; ".join(parts) + ")" if parts else ""


def _current_screen_mode() -> Optional[str]:
    from app.games import screen_modes
    return screen_modes.current_mode()


def _own_result(app_id, since: float) -> Optional[dict]:
    """The game's own numbers for the run started at since (time.time()), from the report it writes; None when it
    writes none the app can read (app/games/settings_file.py)."""
    from app.games.settings_file import game_own_result
    return game_own_result(app_id, since)


OWN_LABELS = (("avg", "Average"), ("min", "Minimum"), ("max", "Maximum"))


class GameBenchmark:
    def __init__(self, spec: GameBenchmarkSpec, run_dir: Path, stop_event: Optional[threading.Event] = None,
                 on_progress: Callable[[str], None] = lambda m: None,
                 sampler_factory: Optional[Callable[[], object]] = None,
                 elevated_runner: Callable = run_elevated, launcher: Optional[Callable] = None,
                 snapshot: Optional[Callable] = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, poll_seconds: float = 1.0,
                 folder_procs: Optional[Callable[[], dict]] = None, closer: Optional[Callable[[dict], str]] = None,
                 exe_lister: Callable[[str], List[str]] = game_exe_names, desktop=None,
                 step_player: Optional[Callable] = None, remote_session: Optional[Callable[[], bool]] = None,
                 linux: Optional[bool] = None, mangohud_hooks=None, screen_mode: Optional[Callable] = None,
                 screen_reader: Optional[Callable] = None, own_result: Optional[Callable] = None):
        self.spec = spec
        self.run_dir = Path(run_dir)
        self.stop_event = stop_event or threading.Event()
        self.on_progress = on_progress
        self.sampler_factory = sampler_factory
        self.elevated_runner = elevated_runner
        if launcher is None:
            from app.steam.launch import launch_and_confirm
            launcher = launch_and_confirm
        self.launcher = launcher
        if snapshot is None:
            from app.benchmark_manager import _get_process_snapshot
            snapshot = _get_process_snapshot
        self.snapshot = snapshot
        self.clock = clock
        self.sleep = sleep
        self.poll_seconds = poll_seconds
        self.csv_path = self.run_dir / CSV_NAME
        self.exe_lister = exe_lister
        from app.frames import game_close
        self.folder_procs = folder_procs or (lambda: game_close.processes_in_folder(spec.install_path))
        self.closer = closer or game_close.close_game
        self._before: Optional[dict] = None
        self._started: dict = {}
        self._desktop = desktop                 # app/simulation/desktop.py; made on the first key
        self.key_notes: List[str] = []
        self.step_player = step_player          # makes the menu step player (tests pass a fake)
        self.remote_session = remote_session or is_remote_session
        self.linux = sys.platform.startswith("linux") if linux is None else linux
        self.mangohud_hooks = mangohud_hooks     # app/frames/mangohud_run.py Hooks (tests pass a fake)
        self.screen_mode = screen_mode or _current_screen_mode
        self.screen_reader = screen_reader      # app/frames/screen_text.read_screen (tests pass a fake)
        self.display_mode: Optional[str] = None
        self.result_shot: Optional[str] = None
        self.shots_dir = self.run_dir               # a whole benchmark: one folder of screenshots per run
        self.own_result = own_result                # the game's own numbers (tests pass a fake)

    def _keep_result_screen(self) -> None:
        """The game's own results screen, kept for the report: looked for up to RESULT_WAIT_SECONDS by its words;
        when they never show, the last screenshot is kept anyway (said in the notes)."""
        reader = self.screen_reader
        if reader is None:
            from app.frames.screen_text import read_screen as reader
        from app.frames.screen_text import find_word
        path = self.shots_dir / RESULT_SHOT
        deadline = self.clock() + RESULT_WAIT_SECONDS
        self.on_progress(f"{self.spec.game_name}: waiting for its results screen (up to {RESULT_WAIT_SECONDS:.0f} s) "
                         "to keep it for the report...")
        seen = None
        while not self.stop_event.is_set():
            try:
                lines, error = reader(self._desk(), path)
            except Exception as e:
                lines, error = [], str(e)
            if error and not path.is_file():       # no screenshot at all: nothing to wait for
                self.key_notes.append(f"results screen not kept ({error})")
                return
            seen = None if error else find_word(lines, list(RESULT_WORDS))
            if seen or self.clock() >= deadline:
                break
            self.sleep(RESULT_LOOK_SECONDS)
        if path.is_file():
            self.result_shot = str(path)
            self.key_notes.append(f"results screen kept ({seen!r} seen)" if seen else
                                  "results screen not recognized; the last screenshot was kept")

    def _desk(self):
        if self._desktop is None:
            from app.simulation.desktop import default_desktop
            self._desktop = default_desktop()
        return self._desktop

    def _steps(self):
        if self.step_player is not None:
            return self.step_player(self.spec.steps)
        from app.frames.menu_steps import StepPlayer
        return StepPlayer(self.spec.steps, self._desk(), self._game_in_front, self.shots_dir)

    def _game_in_front(self) -> bool:
        return self._desk().foreground_pid() in self._started

    def _press_due(self, pending: List[Tuple[float, str]], elapsed: float):
        """Press the start keys whose time has come, only into the game: a key is skipped (and noted) when the
        foreground window is not one of the processes the launch started."""
        from app.simulation.desktop import parse_chord
        while pending and pending[0][0] <= elapsed:
            at, chord = pending.pop(0)
            self._desk()
            codes = parse_chord(chord)
            if codes is None:
                self.key_notes.append(f"{chord!r} at {at:g} s: unknown key, not sent")
            elif self._desktop.foreground_pid() in self._started:
                self._desktop.press(codes)
                self.key_notes.append(f"pressed {chord} at {elapsed:.0f} s")
            else:
                self.key_notes.append(f"{chord} at {at:g} s not sent: the game was not in front")

    def _note_started(self):
        """Remember processes in the game's folder that were not running before the launch."""
        if self._before is None:
            return
        for pid, info in self.folder_procs().items():
            if pid not in self._before:
                self._started[pid] = info

    def _close_game(self) -> str:
        if self._before is None:
            return ""
        self._note_started()
        if not self._started:
            return "; no game process to close"
        self.on_progress(f"Closing {self.spec.game_name}...")
        try:
            return "; " + self.closer(self._started)
        except Exception as e:
            logger.exception("Could not close the game")
            return f"; the game could not be closed ({e}); close it yourself"

    def _read_csv(self) -> Optional[str]:
        try:
            return self.csv_path.read_text(encoding="utf-8", errors="replace")
        except OSError:           # not written, or still locked by PresentMon
            return None

    def run(self) -> GameCapture:
        capture = (self._run_mangohud() if self.linux else
                   self._run_passes() if self.spec.whole_benchmark else self._run())
        try:        # the message stays in the run folder even when the app is closed before it is read
            (self.run_dir / LOG_NAME).write_text(
                f"status: {capture.status}\nmessage: {capture.message}\n"
                f"command: {subprocess.list2cmdline(capture.command or [])}\n", encoding="utf-8")
        except OSError:
            pass
        return capture

    def _run(self) -> GameCapture:
        spec = self.spec
        self.run_dir.mkdir(parents=True, exist_ok=True)
        exes = self.exe_lister(spec.install_path)
        if not exes:
            return GameCapture("failed", f"no .exe file found in {spec.install_path}; the game was not started")
        args = capture_game_args(str(self.csv_path), exes, spec.max_record_seconds(), until_exit=not spec.relaunches)
        command = [spec.presentmon_path] + args
        halt = threading.Event()        # ends our elevated PresentMon by force (only if it does not end itself)
        running = threading.Event()     # set once the UAC prompt was accepted
        outcome = {}

        def record():
            outcome["out"] = self.elevated_runner(spec.presentmon_path, args, str(self.run_dir),
                                                  spec.max_record_seconds() + START_MARGIN_SECONDS, halt,
                                                  on_started=running.set)
        thread = threading.Thread(target=record, name="presentmon", daemon=True)
        self.on_progress("Starting PresentMon (accept the UAC prompt)...")
        thread.start()

        def end_presentmon() -> str:
            """Close the game; PresentMon then ends by itself (--terminate_on_proc_exit) and writes its CSV."""
            closed = self._close_game()
            if not self._started or spec.relaunches:
                halt.set()              # nothing would end PresentMon: no game process, or not told to end with it
            thread.join(30)
            if thread.is_alive():
                halt.set()
                thread.join(15)
                closed += "; PresentMon did not end by itself and was stopped (its last data may be missing)"
            return closed

        def finish(status, message, **kw) -> GameCapture:
            return GameCapture(status, message + end_presentmon(), command=command, **kw)

        deadline = self.clock() + UAC_WAIT_SECONDS
        while not running.is_set():
            if not thread.is_alive():
                out = outcome.get("out")
                if out is not None and out.status == "uac_declined":
                    return finish("cancelled", "UAC prompt declined; the game was not started")
                return finish("failed", "PresentMon did not start: "
                              + (getattr(out, "error", None) or f"exit code {getattr(out, 'exit_code', None)}"))
            if self.stop_event.is_set():
                return finish("cancelled", "stopped by user before the game was started")
            if self.clock() >= deadline:
                return finish("failed", f"the UAC prompt was not answered within {UAC_WAIT_SECONDS:g} s; "
                              "the game was not started")
            self.sleep(0.5)

        self.on_progress(f"PresentMon recording; launching {spec.game_name} through Steam...")
        self._before = self.folder_procs()
        extra = {"args": list(spec.launch_args)} if spec.launch_args else {}
        launched = self.launcher(spec.app_id, spec.install_path, spec.discovered_appids, snapshot=self.snapshot,
                                 timeout=LAUNCH_TIMEOUT_SECONDS, poll=1.0, sleep=self.sleep,
                                 clock=lambda: float("inf") if self.stop_event.is_set() else self.clock(), **extra)
        if self.stop_event.is_set():
            return finish("cancelled", "stopped by user while the game was starting")
        if launched.get("status") != "launched":
            return finish("failed", f"the game did not start: {launched.get('message')}")

        sampler = None
        if self.sampler_factory is not None:
            try:
                sampler = self.sampler_factory().start()
            except Exception as e:
                self.on_progress(f"telemetry not started: {e}")
        t0 = self.clock()
        wait = spec.wait_seconds()
        stopped = False
        presentmon_ended = None          # s after the launch when PresentMon ended by itself (before the close)
        pending = sorted(spec.start_keys)
        player = None
        if spec.route:
            from app.frames.route import RoutePlayer
            player = RoutePlayer(spec.route, self._desk, self._game_in_front, wait)
        shown = None
        # keys and a route are timed from the moment the game's window first comes to the front, not from the
        # launch: a game loading from a slow drive showed its window 3.7 min after the launch (The Witcher 3
        # from a network share, 2026-10-18). The measured windows already start at the first frame.
        t_front = t0 if not pending and player is None and not spec.steps else None
        steps = None                    # menu step player, started when the game is in front
        t_mark = None                   # clock() when the steps saw the benchmark start: measuring starts there
        try:
            while True:
                if self.stop_event.is_set():
                    stopped = True
                    break
                if not thread.is_alive():
                    presentmon_ended = self.clock() - t0
                    break                   # PresentMon ended: the game was closed, or its time limit
                self._note_started()
                if t_front is None:
                    now = self.clock()
                    try:
                        front = self._game_in_front()
                    except Exception:
                        front = True        # cannot tell: count from now
                    if front or now - t0 >= FRONT_WAIT_SECONDS:
                        t_front = now
                        if now - t0 >= 5 or not front:
                                self.key_notes.append(f"the game came to the front {now - t0:.0f} s after the launch"
                                                  if front else f"the game was not in front after {now - t0:.0f} s; "
                                                  "keys and route counted from then")
                    else:
                        if int(now - t0) != shown:
                            shown = int(now - t0)
                            self.on_progress(f"{spec.game_name}: waiting for its window ({shown} s; it may be "
                                             "loading). Keys and route start when it is in front.")
                        self.sleep(self.poll_seconds)
                        continue
                if spec.steps and steps is None:
                    steps = self._steps().start()
                if steps is not None and t_mark is None:
                    if steps.mark_time is not None:
                        t_mark = steps.mark_time
                    elif steps.done.is_set():
                        break           # the menus did not lead to the benchmark: failed below
                    else:
                        now = int(self.clock() - t_front)
                        if now != shown:
                            shown = now
                            self.on_progress(f"{spec.game_name}: going through its menus to the benchmark "
                                             f"({now} s); keep the game in front.")
                        self.sleep(self.poll_seconds)
                        continue
                elapsed = self.clock() - (t_mark if t_mark is not None else t_front)
                if elapsed >= wait:
                    break
                if pending:
                    try:
                        self._press_due(pending, elapsed)
                    except Exception as e:          # keys only help; the capture goes on without them
                        self.key_notes.append(f"keys stopped: {e}")
                        pending.clear()
                if player is not None:
                    player.tick(elapsed)
                if int(elapsed) != shown:
                    shown = int(elapsed)
                    self.on_progress(f"{spec.game_name}: {elapsed:.0f} of {wait:.0f} s. The first "
                                     f"{spec.settle_seconds} s are not measured; keep the game in front. It is "
                                     "closed at the end.")
                self.sleep(min(self.poll_seconds, ROUTE_POLL_SECONDS) if player is not None else self.poll_seconds)
        finally:
            if steps is not None:
                steps.stop.set()
                steps.join(5)
            if player is not None:
                try:
                    player.release_all()
                except Exception as e:
                    player.problem = player.problem or f"keys could not be released: {e}"
        if thread.is_alive() and not stopped:       # the game is still open: in its benchmark or its results
            try:
                self.display_mode = self.screen_mode()
            except Exception:
                self.display_mode = None
            if steps is not None and t_mark is not None:
                self._keep_result_screen()

        t_close = self.clock() - t0
        closed = end_presentmon()
        if self.key_notes:
            closed += "; start keys: " + ", ".join(self.key_notes)
        if player is not None:
            closed += "; " + player.summary()
        if steps is not None:
            closed += "; " + steps.summary()
            if t_mark is None and not stopped:
                if sampler is not None:
                    sampler.stop()
                return GameCapture("failed", "the app did not reach the game's benchmark" + closed, command=command)
        tel = sampler.stop() if sampler is not None else None
        text = self._read_csv()
        started_names = {os.path.basename(i.exe) for i in self._started.values()}
        prefix = "stopped by user; " if stopped else ""
        if text is None:
            return GameCapture("cancelled" if stopped else "failed",
                               prefix + "PresentMon wrote no capture file" + presentmon_text(outcome.get("out"),
                                                                                             presentmon_ended)
                               + closed, command=command)
        pick = pick_game_process(summarize_processes(text), started_names or set(exes))
        if pick is None:
            return GameCapture("cancelled" if stopped else "failed",
                               prefix + "PresentMon recorded no frames from the game (started: "
                               + (", ".join(sorted(started_names)) or "none")
                               + "; a Remote Desktop session records none)" + closed, command=command)
        parsed = parse_presentmon_csv(text, process_id=pick[1])
        windows = spec.windows()
        mark_shift = 0.0
        frametimes = parsed.frametimes_ms if not parsed.error else []
        recorded = game_seconds(frametimes)
        frame_windows = split_frames(frametimes, windows)
        # the game stops presenting when it is asked to close, so its first frame was `recorded` s earlier
        offset = first_frame_offset(t_close, recorded)
        if t_mark is not None:          # windows count from the benchmark's start, not the game's first frame
            mark_shift = max(0.0, (t_mark - t0) - offset)
            windows = [(a + mark_shift, b + mark_shift) for a, b in windows]
            frame_windows = split_frames(frametimes, windows)
        sample_windows = slice_samples(tel.samples if tel else [], offset, windows)
        out = self._outcomes(frame_windows, windows, recorded, sample_windows, stopped, steps, command)
        return GameCapture("recorded", f"{recorded:.0f} s of {pick[0]} recorded" + closed, process=pick,
                           windows=out, command=command,
                           telemetry_unavailable=dict(tel.unavailable) if tel else {}, display_mode=self.display_mode)

    def _outcomes(self, frame_windows, windows, recorded, sample_windows, stopped, steps, command):
        """One WindowOutcome per window: a score only for a usable window of a local (not remote) session."""
        spec = self.spec
        common = {"command": command}
        remote = self.remote_session()
        out = []
        for k, frames in enumerate(frame_windows):
            is_warmup = k < spec.warmup_runs
            run_index = k if is_warmup else k - spec.warmup_runs + 1
            a, b = windows[k]
            result = self._window_result(frames, a, b, recorded, stopped, remote, command,
                                         self.result_shot or getattr(steps, "started_shot", None))
            out.append(WindowOutcome(is_warmup, run_index, result, sample_windows[k],
                                     frames=list(frames) if frames else []))
        return out

    def _window_result(self, frames, a, b, recorded, stopped, remote, command, shot) -> WorkloadResult:
        """A score only for a usable window of a local (not remote) session."""
        common = {"command": command}
        if frames is None:
            if stopped:
                return WorkloadResult(status="cancelled", error="stopped by user", **common)
            return WorkloadResult(status="invalid", error=(
                f"the recording ended at {recorded:.0f} s of game time; this window needs {a:g}-{b:g} s"), **common)
        stats = compute_frame_stats(frames, self.spec.min_frames)
        if stats.error:
            return WorkloadResult(status="invalid", error=f"capture not usable: {stats.error}", **common)
        if remote:              # a number, but not this PC's: never stored as a score
            return WorkloadResult(status="invalid", error=f"{REMOTE_TEXT} (it showed {stats.avg_fps:.1f} "
                                  "FPS; not stored)", **common)
        metrics = stats_to_metrics(stats)
        metrics["window_start_s"] = (float(a), "s")
        result = WorkloadResult(status="completed", score=stats.avg_fps, unit="FPS",
                                metrics=metrics, duration_seconds=float(b - a), **common)
        if shot:
            result.note = f"Proof screenshot: {shot}"
        return result

    # ---- a built-in benchmark measured whole, once per launch (owner, 2026-10-23) -------------------------
    def _run_passes(self) -> GameCapture:
        spec = self.spec
        self.run_dir.mkdir(parents=True, exist_ok=True)
        exes = self.exe_lister(spec.install_path)
        if not exes:
            return GameCapture("failed", f"no .exe file found in {spec.install_path}; the game was not started")
        # PresentMon must outlive each run's game: it is stopped by the app after the last one
        args = capture_game_args(str(self.csv_path), exes, spec.max_record_seconds(), until_exit=False)
        command = [spec.presentmon_path] + args
        halt, running, outcome = threading.Event(), threading.Event(), {}

        def record():
            outcome["out"] = self.elevated_runner(spec.presentmon_path, args, str(self.run_dir),
                                                  spec.max_record_seconds() + START_MARGIN_SECONDS, halt,
                                                  on_started=running.set)
        thread = threading.Thread(target=record, name="presentmon", daemon=True)
        self.on_progress("Starting PresentMon (accept the UAC prompt)...")
        thread.start()

        def end_presentmon():
            halt.set()
            thread.join(30)

        deadline = self.clock() + UAC_WAIT_SECONDS
        while not running.is_set():
            problem = None
            if not thread.is_alive():
                out = outcome.get("out")
                if out is not None and out.status == "uac_declined":
                    return GameCapture("cancelled", "UAC prompt declined; the game was not started", command=command)
                problem = "PresentMon did not start: " + (getattr(out, "error", None)
                                                         or f"exit code {getattr(out, 'exit_code', None)}")
            elif self.stop_event.is_set():
                end_presentmon()
                return GameCapture("cancelled", "stopped by user before the game was started", command=command)
            elif self.clock() >= deadline:
                problem = f"the UAC prompt was not answered within {UAC_WAIT_SECONDS:g} s; the game was not started"
            if problem:
                end_presentmon()
                return GameCapture("failed", problem, command=command)
            self.sleep(0.5)

        sampler = None
        if self.sampler_factory is not None:
            try:
                sampler = self.sampler_factory().start()
            except Exception as e:
                self.on_progress(f"telemetry not started: {e}")
        t_start = self.clock()              # the telemetry's time 0
        passes = []
        for k in range(spec.count):
            if self.stop_event.is_set() or not thread.is_alive():
                break
            label = ("the warm-up run (not counted)" if k < spec.warmup_runs else
                     f"run {k - spec.warmup_runs + 1} of {spec.repetitions}")
            done = self._one_pass(k, label)
            passes.append(done)
            if done["stopped"] or done["error"]:
                break
            if k + 1 < spec.count:
                self.sleep(PASS_GAP_SECONDS)
        end_presentmon()
        tel = sampler.stop() if sampler is not None else None
        notes = [f"run {i}: {p['notes']}" for i, p in enumerate(passes) if p["notes"]]
        tail = ("; " + "; ".join(notes)) if notes else ""
        stopped = any(p["stopped"] for p in passes) or self.stop_event.is_set()
        if not any(p["t_mark"] is not None for p in passes):
            why = next((p["error"] for p in passes if p["error"]), "stopped by user" if stopped else "no run")
            return GameCapture("cancelled" if stopped else "failed", why + tail, command=command)
        text = self._read_csv()
        if text is None:
            return GameCapture("cancelled" if stopped else "failed", "PresentMon wrote no capture file"
                               + presentmon_text(outcome.get("out"), None) + tail, command=command)
        summary = summarize_processes(text)
        remote = self.remote_session()
        out, process = [], None
        for k in range(spec.count):
            is_warmup = k < spec.warmup_runs
            run_index = k if is_warmup else k - spec.warmup_runs + 1
            done = passes[k] if k < len(passes) else None
            frames, samples, recorded = None, [], 0.0
            a = b = 0.0
            if done is None or done["t_mark"] is None:
                why = ("stopped by user" if stopped else
                       (done or {}).get("error") or "not run: an earlier run did not complete")
                result = WorkloadResult(status="cancelled" if stopped else "invalid", error=why, command=command)
            else:
                pick = pick_game_process([r for r in summary if r.get("process_id") in done["started"]],
                                         {os.path.basename(i.exe) for i in done["started"].values()} or set(exes))
                parsed = parse_presentmon_csv(text, process_id=pick[1]) if pick else None
                frametimes = parsed.frametimes_ms if parsed is not None and not parsed.error else []
                recorded = game_seconds(frametimes)
                process = process or pick
                # the game's first frame was `recorded` s before it was asked to close
                a = (done["t_mark"] - done["t_close"]) + recorded
                b = a + float(spec.benchmark_seconds)
                frames = split_frames(frametimes, [(a, b)])[0] if pick else None
                start = done["t_mark"] - t_start
                samples = slice_samples(tel.samples if tel else [], 0.0, [(start, start + b - a)])[0]
                result = (self._window_result(frames, a, b, recorded, False, remote, command, done["shot"])
                          if pick else WorkloadResult(status="invalid", error="PresentMon recorded no frames from "
                                                      "the game in this run", command=command))
                if result.status == "completed":
                    measured_text = (f"Measured: the game's whole benchmark ({spec.benchmark_seconds} s from its "
                                     f"start), the game started anew, {spec.warmup_runs} warm-up run(s) before "
                                     "were not counted")
                    result.note = "; ".join(x for x in [result.note, measured_text] if x)
                self._add_own(result, done["own"])
            out.append(WindowOutcome(is_warmup, run_index, result, samples, frames=list(frames) if frames else []))
        measured = f"{len([p for p in passes if p['t_mark'] is not None])} of {spec.count} runs reached the benchmark"
        return GameCapture("recorded", measured + tail, process=process, windows=out, command=command,
                           telemetry_unavailable=dict(tel.unavailable) if tel else {}, display_mode=self.display_mode)

    @staticmethod
    def _add_own(result: WorkloadResult, own: Optional[dict]) -> None:
        """The game's own numbers next to the app's (owner, 2026-10-23): its minimum and maximum are per second."""
        if not own or result.status != "completed":
            return
        for key, label in OWN_LABELS:
            if own.get(key) is not None:
                result.metrics[f"game_{key}_fps"] = (float(own[key]), "FPS")
        shown = ", ".join(f"{label} {own[key]:g}" for key, label in OWN_LABELS if own.get(key) is not None)
        if shown:
            result.note = "; ".join(x for x in [result.note, f"Game's own result: {shown} FPS"] if x)

    def _one_pass(self, k: int, label: str) -> dict:
        """One launch: through the menus to the benchmark, its whole length, its results screen kept, closed."""
        spec = self.spec
        done = {"started": {}, "t_mark": None, "t_close": None, "stopped": False, "error": None, "shot": None,
                "own": None, "notes": ""}
        self.shots_dir = self.run_dir / f"run_{k}"
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        self.result_shot, self.key_notes, self._started = None, [], {}
        self._before = self.folder_procs()
        since = time.time()
        self.on_progress(f"{spec.game_name}, {label}: launching it through Steam...")
        extra = {"args": list(spec.launch_args)} if spec.launch_args else {}
        launched = self.launcher(spec.app_id, spec.install_path, spec.discovered_appids, snapshot=self.snapshot,
                                 timeout=LAUNCH_TIMEOUT_SECONDS, poll=1.0, sleep=self.sleep,
                                 clock=lambda: float("inf") if self.stop_event.is_set() else self.clock(), **extra)
        if self.stop_event.is_set():
            done["stopped"] = True
        elif launched.get("status") != "launched":
            done["error"] = f"the game did not start: {launched.get('message')}"
        steps, shown = None, None
        t_launch = self.clock()
        try:
            while not done["stopped"] and not done["error"]:
                if self.stop_event.is_set():
                    done["stopped"] = True
                    break
                self._note_started()
                now = self.clock()
                if steps is None:
                    try:
                        front = self._game_in_front()
                    except Exception:
                        front = True
                    if front or now - t_launch >= FRONT_WAIT_SECONDS:
                        steps = self._steps().start()
                    else:
                        self.sleep(self.poll_seconds)
                        continue
                if done["t_mark"] is None:
                    if steps.mark_time is not None:
                        done["t_mark"] = steps.mark_time
                    elif steps.done.is_set():
                        done["error"] = "the app did not reach the game's benchmark"
                        break
                    else:
                        if int(now - t_launch) != shown:
                            shown = int(now - t_launch)
                            self.on_progress(f"{spec.game_name}, {label}: going through its menus to the benchmark "
                                             f"({shown} s); keep the game in front.")
                        self.sleep(self.poll_seconds)
                        continue
                elapsed = now - done["t_mark"]
                if elapsed >= spec.benchmark_seconds:
                    break
                if int(elapsed) != shown:
                    shown = int(elapsed)
                    self.on_progress(f"{spec.game_name}, {label}: benchmark {elapsed:.0f} of "
                                     f"{spec.benchmark_seconds} s, measured whole. It is closed at the end.")
                self.sleep(self.poll_seconds)
        finally:
            if steps is not None:
                steps.stop.set()
                steps.join(5)
        if done["t_mark"] is not None and not done["stopped"]:
            try:
                self.display_mode = self.screen_mode()
            except Exception:
                pass
            self._keep_result_screen()
            try:
                done["own"] = (self.own_result or _own_result)(spec.app_id, since)
            except Exception:
                logger.exception("Could not read the game's own result")
        done["shot"] = self.result_shot or getattr(steps, "started_shot", None)
        done["t_close"] = self.clock()
        closed = self._close_game()
        done["started"] = dict(self._started)
        notes = list(self.key_notes) + ([steps.summary()] if steps is not None else [])
        done["notes"] = "; ".join(n for n in notes + [closed.lstrip("; ")] if n)
        return done

    # ---- Linux: MangoHud (app/frames/mangohud_run.py) ------------------------------------------------
    def _run_mangohud(self) -> GameCapture:
        from app.frames import mangohud_run as mh
        spec = self.spec
        hooks = self.mangohud_hooks or mh.Hooks()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if spec.steps or spec.start_keys or spec.route:     # an X11 / XWayland desktop (app/simulation/x11_desktop.py)
            if self._desk() is None:
                from app.simulation.x11_desktop import X11Desktop
                return GameCapture("failed", "menu steps, keys and routes cannot run here: "
                                   + (X11Desktop.why or "no desktop") + "; nothing was started")
        if not hooks.mangohud_installed():
            return GameCapture("failed", "MangoHud is not installed (Frame capture screen: Install MangoHud); "
                               "nothing was started")
        command = hooks.command(spec.app_id, spec.launch_args)
        if command is None:
            return GameCapture("failed", "the steam command was not found (the Flatpak Steam is not supported "
                               "yet); nothing was started")
        shared = hooks.folder()                     # one place for every game: the app's Steam stays usable
        shared_logs = shared / mh.LOGS_FOLDER
        config = shared / mh.CONFIG_NAME
        try:
            shared_logs.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return GameCapture("failed", f"the MangoHud folder could not be made ({e}); nothing was started")
        state = hooks.steam_state(config)
        if state == "other":                        # opened without the app's MangoHud setting
            self.on_progress("Steam is open without MangoHud: asking it to exit (steam -shutdown), then starting "
                             "it again with MangoHud...")
            why = hooks.shutdown()
            if why:
                return GameCapture("failed", f"Steam is open without MangoHud and did not exit ({why}); close it "
                                   "yourself (Steam menu > Exit) and run again; nothing was started", command=command)
        # with menu steps the benchmark starts later than the game's first frame: MangoHud logs long enough for the
        # menus too, and the app closes the game itself once the measured time after the start mark is over
        duration = int(spec.wait_seconds()) + (steps_bound_seconds(spec.steps) if spec.steps else 0)
        config.write_text(mh.config_text(shared_logs, duration), encoding="utf-8")    # read when the game starts
        (self.run_dir / mh.CONFIG_NAME).write_text(mh.config_text(shared_logs, duration), encoding="utf-8")
        logs = self.run_dir / mh.LOGS_FOLDER
        since = time.time()
        self._before = self.folder_procs()
        self.on_progress(f"Asking the Steam with MangoHud to launch {spec.game_name}..." if state == "ours" else
                         f"Starting Steam with MangoHud and {spec.game_name}...")
        try:
            hooks.start(command, mh.environment(config))
        except Exception as e:
            return GameCapture("failed", f"Steam could not be started: {e}", command=command)
        deadline = self.clock() + mh.LAUNCH_TIMEOUT_SECONDS
        while not self._started:
            if self.stop_event.is_set():
                return GameCapture("cancelled", "stopped by user while the game was starting" + self._close_game(),
                                   command=command)
            if self.clock() >= deadline:
                return GameCapture("failed", f"no game process appeared within {mh.LAUNCH_TIMEOUT_SECONDS:g} s "
                                   "(Steam may be updating, signing in or showing a dialog)", command=command)
            self.sleep(self.poll_seconds)
            self._note_started()
        sampler = None
        if self.sampler_factory is not None:
            try:
                sampler = self.sampler_factory().start()
            except Exception as e:
                self.on_progress(f"telemetry not started: {e}")
        t0, t0_wall = self.clock(), time.time()
        limit = duration + mh.LAUNCH_TIMEOUT_SECONDS     # MangoHud starts logging only when the game draws
        stopped, shown = False, None
        pending = sorted(spec.start_keys)
        player = None
        if spec.route:                  # timed from the game's first time in front, like keys (as on Windows)
            from app.frames.route import RoutePlayer
            player = RoutePlayer(spec.route, self._desk, self._game_in_front, spec.wait_seconds())
        t_front = t0 if not pending and not spec.steps and player is None else None
        steps = None                    # menu step player, started when the game is in front (as on Windows)
        t_mark = None                   # clock() when the steps saw the benchmark start
        try:
            while True:
                if self.stop_event.is_set():
                    stopped = True
                    break
                if mh.logging_done(shared_logs, since - 2):
                    break               # log_duration reached: MangoHud wrote its summary
                elapsed = self.clock() - t0
                if elapsed >= limit:
                    break
                self._note_started()
                if t_front is None:
                    try:
                        front = self._game_in_front()
                    except Exception:
                        front = True    # cannot tell: count from now
                    if front or elapsed >= FRONT_WAIT_SECONDS:
                        t_front = self.clock()
                        if not front:
                            self.key_notes.append(f"the game was not in front after {elapsed:.0f} s; keys counted "
                                                  "from then")
                    else:
                        self.sleep(self.poll_seconds)
                        continue
                if spec.steps and steps is None:
                    steps = self._steps().start()
                if steps is not None:
                    if t_mark is None and steps.mark_time is not None:
                        t_mark = steps.mark_time
                    if t_mark is None:
                        if steps.done.is_set():
                            break       # the menus did not lead to the benchmark: failed below
                        if int(elapsed) != shown:
                            shown = int(elapsed)
                            self.on_progress(f"{spec.game_name}: going through its menus to the benchmark "
                                             f"({elapsed:.0f} s); keep the game in front.")
                        self.sleep(self.poll_seconds)
                        continue
                    if self.clock() - t_mark >= spec.wait_seconds():
                        break           # the measured time after the start mark is over: the app closes the game
                if pending:
                    try:
                        self._press_due(pending, self.clock() - t_front)
                    except Exception as e:          # keys only help; the capture goes on without them
                        self.key_notes.append(f"keys stopped: {e}")
                        pending.clear()
                if player is not None:
                    player.tick(self.clock() - t_front)
                if int(elapsed) != shown:
                    shown = int(elapsed)
                    self.on_progress(f"{spec.game_name}: {elapsed:.0f} s (MangoHud logs {duration} s from the "
                                     f"game's first frames). The first {spec.settle_seconds} s are not measured. It "
                                     "is closed at the end.")
                self.sleep(min(self.poll_seconds, ROUTE_POLL_SECONDS) if player is not None else self.poll_seconds)
        finally:
            if steps is not None:
                steps.stop.set()
                steps.join(5)
            if player is not None:
                try:
                    player.release_all()
                except Exception as e:
                    player.problem = player.problem or f"keys could not be released: {e}"
        closed = self._close_game()
        if self.key_notes:
            closed += "; start keys: " + ", ".join(self.key_notes)
        if player is not None:
            closed += "; " + player.summary()
        if steps is not None:
            closed += "; " + steps.summary()
        tel = sampler.stop() if sampler is not None else None
        prefix = "stopped by user; " if stopped else ""
        mh.move_new_logs(shared_logs, logs, since - 2)    # this run's logs belong to its run folder
        if steps is not None and t_mark is None and not stopped:
            return GameCapture("failed", "the app did not reach the game's benchmark" + closed, command=command)
        best = mh.best_log(logs, since - 2)
        if best is None:
            return GameCapture("cancelled" if stopped else "failed",
                               prefix + "MangoHud wrote no frame log (it works with Vulkan games: Proton games and "
                               "native Vulkan ones; a native OpenGL game writes none)" + closed, command=command)
        path, parsed = best
        frametimes = parsed.frametimes_ms
        recorded = game_seconds(frametimes)
        windows = spec.windows()
        frame_windows = split_frames(frametimes, windows)
        try:                            # the log's last frame was written when the file last changed
            offset = first_frame_offset(path.stat().st_mtime - t0_wall, recorded)
        except OSError:
            offset = 0.0
        if t_mark is not None:          # windows count from the benchmark's start, not the game's first frame
            mark_shift = max(0.0, (t_mark - t0) - offset)
            windows = [(a + mark_shift, b + mark_shift) for a, b in windows]
            frame_windows = split_frames(frametimes, windows)
        sample_windows = slice_samples(tel.samples if tel else [], offset, windows)
        out = self._outcomes(frame_windows, windows, recorded, sample_windows, stopped, steps, command)
        name = mh.program_name(path)
        return GameCapture("recorded", f"{recorded:.0f} s of {name} recorded by MangoHud ({path.name})" + closed,
                           process=(name, None), windows=out, command=command,
                           telemetry_unavailable=dict(tel.unavailable) if tel else {})


class RecordedWindow(Workload):
    """Hands one already recorded window to AutomationRunner, so it is stored like any other run."""

    benchmark_type = "game"

    def __init__(self, workload_id: str, display_name: str, outcomes: List[WindowOutcome],
                 version: Optional[str] = None, unavailable: Optional[dict] = None):
        self.id = workload_id
        self.display_name = display_name
        self._outcomes = list(outcomes)
        self._version = version
        self._unavailable = unavailable or {}

    def version(self) -> Optional[str]:
        return self._version

    def run(self, ctx: RunContext) -> WorkloadResult:
        outcome = self._outcomes.pop(0)
        result = outcome.result
        series = {}
        if outcome.samples:
            from app.telemetry.sampler import TelemetryResult, chart_series, summarize, write_csv
            tel = TelemetryResult(samples=outcome.samples, unavailable=dict(self._unavailable))
            write_csv(tel, Path(ctx.output_dir) / "telemetry.csv")
            if result.status == "completed":
                result.metrics = {**summarize(tel), **result.metrics}
                series.update(chart_series(tel))
        if result.status == "completed" and outcome.frames:
            series["fps"] = ("FPS", fps_over_time(outcome.frames))
        if series:
            result.series = {**series, **(result.series or {})}
        return result


def fps_over_time(frametimes_ms: List[float], step: float = 1.0) -> List[Tuple[float, float]]:
    """(end of each second, frames shown in it per second) from a window's frame times; a last part second is
    left out."""
    out, t, start, count = [], 0.0, 0.0, 0
    for ms in frametimes_ms:
        t += ms / 1000.0
        count += 1
        if t - start >= step - 1e-9:
            out.append((round(t, 3), round(count / (t - start), 2)))
            start, count = t, 0
    return out


def plan_for(capture: GameCapture, spec: GameBenchmarkSpec, version: Optional[str] = None):
    """AutomationRunner plan that stores every recorded window (warmups first, as the runner orders them)."""
    from app.automation.runner import PlanItem
    name = capture.process[0] if capture.process else spec.game_name
    workload = RecordedWindow(workload_id_for(name), f"Game benchmark: {spec.game_name}",
                              capture.windows, version or spec.version, capture.telemetry_unavailable)
    return [PlanItem(workload, repetitions=spec.repetitions, warmup_runs=spec.warmup_runs)]
