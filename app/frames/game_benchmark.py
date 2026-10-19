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
        front = FRONT_WAIT_SECONDS if self.route or self.start_keys or self.steps else 0
        return int(UAC_WAIT_SECONDS + LAUNCH_TIMEOUT_SECONDS + front + self.steps_seconds() + self.wait_seconds() + 120)

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


@dataclass
class GameCapture:
    status: str                                   # "recorded" (windows below), "cancelled", "failed"
    message: str = ""
    process: Optional[Tuple[str, Optional[int]]] = None
    windows: List[WindowOutcome] = field(default_factory=list)
    command: Optional[list] = None
    telemetry_unavailable: dict = field(default_factory=dict)


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
                 linux: Optional[bool] = None, mangohud_hooks=None):
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

    def _desk(self):
        if self._desktop is None:
            from app.simulation.desktop import default_desktop
            self._desktop = default_desktop()
        return self._desktop

    def _steps(self):
        if self.step_player is not None:
            return self.step_player(self.spec.steps)
        from app.frames.menu_steps import StepPlayer
        return StepPlayer(self.spec.steps, self._desk(), self._game_in_front, self.run_dir)

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
        capture = self._run_mangohud() if self.linux else self._run()
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
        args = capture_game_args(str(self.csv_path), exes, spec.max_record_seconds())
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
            if not self._started:
                halt.set()              # no game process: nothing would end PresentMon
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
                           telemetry_unavailable=dict(tel.unavailable) if tel else {})

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
            if frames is None:
                if stopped:
                    result = WorkloadResult(status="cancelled", error="stopped by user", **common)
                else:
                    result = WorkloadResult(status="invalid", error=(
                        f"the recording ended at {recorded:.0f} s of game time; this window needs {a:g}-{b:g} s"),
                        **common)
            else:
                stats = compute_frame_stats(frames, spec.min_frames)
                if stats.error:
                    result = WorkloadResult(status="invalid", error=f"capture not usable: {stats.error}", **common)
                elif remote:            # a number, but not this PC's: never stored as a score
                    result = WorkloadResult(status="invalid", error=f"{REMOTE_TEXT} (it showed {stats.avg_fps:.1f} "
                                            "FPS; not stored)", **common)
                else:
                    metrics = stats_to_metrics(stats)
                    metrics["window_start_s"] = (float(a), "s")
                    result = WorkloadResult(status="completed", score=stats.avg_fps, unit="fps",
                                            metrics=metrics, duration_seconds=float(b - a), **common)
                    if getattr(steps, "started_shot", None):
                        result.note = f"Proof screenshot: {steps.started_shot}"
            out.append(WindowOutcome(is_warmup, run_index, result, sample_windows[k]))
        return out

    # ---- Linux: MangoHud (app/frames/mangohud_run.py) ------------------------------------------------
    def _run_mangohud(self) -> GameCapture:
        from app.frames import mangohud_run as mh
        spec = self.spec
        hooks = self.mangohud_hooks or mh.Hooks()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if spec.steps or spec.start_keys or spec.route:
            return GameCapture("failed", "this game's benchmark needs menu steps, keys or a route, which work on "
                               "Windows only so far; nothing was started")
        if not hooks.mangohud_installed():
            return GameCapture("failed", "MangoHud is not installed (Frame capture screen: Install MangoHud); "
                               "nothing was started")
        command = hooks.command(spec.app_id, spec.launch_args)
        if command is None:
            return GameCapture("failed", "the steam command was not found (the Flatpak Steam is not supported "
                               "yet); nothing was started")
        if hooks.steam_running():
            return GameCapture("failed", "Steam is open: close it (Steam menu > Exit) so the app can start it with "
                               "MangoHud for this run; nothing was started", command=command)
        logs = self.run_dir / mh.LOGS_FOLDER
        logs.mkdir(exist_ok=True)
        config = self.run_dir / mh.CONFIG_NAME
        duration = int(spec.wait_seconds())
        config.write_text(mh.config_text(logs, duration), encoding="utf-8")
        since = time.time()
        self._before = self.folder_procs()
        self.on_progress(f"Starting Steam with MangoHud and {spec.game_name}...")
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
        while True:
            if self.stop_event.is_set():
                stopped = True
                break
            if mh.logging_done(logs):
                break                   # log_duration reached: MangoHud wrote its summary
            elapsed = self.clock() - t0
            if elapsed >= limit:
                break
            self._note_started()
            if int(elapsed) != shown:
                shown = int(elapsed)
                self.on_progress(f"{spec.game_name}: {elapsed:.0f} s (MangoHud logs {duration} s from the game's "
                                 f"first frames). The first {spec.settle_seconds} s are not measured. It is closed "
                                 "at the end.")
            self.sleep(self.poll_seconds)
        closed = self._close_game()
        tel = sampler.stop() if sampler is not None else None
        prefix = "stopped by user; " if stopped else ""
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
        sample_windows = slice_samples(tel.samples if tel else [], offset, windows)
        out = self._outcomes(frame_windows, windows, recorded, sample_windows, stopped, None, command)
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
        if outcome.samples:
            from app.telemetry.sampler import TelemetryResult, summarize, write_csv
            tel = TelemetryResult(samples=outcome.samples, unavailable=dict(self._unavailable))
            write_csv(tel, Path(ctx.output_dir) / "telemetry.csv")
            if result.status == "completed":
                result.metrics = {**summarize(tel), **result.metrics}
        return result


def plan_for(capture: GameCapture, spec: GameBenchmarkSpec, version: Optional[str] = None):
    """AutomationRunner plan that stores every recorded window (warmups first, as the runner orders them)."""
    from app.automation.runner import PlanItem
    name = capture.process[0] if capture.process else spec.game_name
    workload = RecordedWindow(workload_id_for(name), f"Game benchmark: {spec.game_name}",
                              capture.windows, version or spec.version, capture.telemetry_unavailable)
    return [PlanItem(workload, repetitions=spec.repetitions, warmup_runs=spec.warmup_runs)]
