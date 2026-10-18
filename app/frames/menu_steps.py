"""Checked steps through a game's menus to its built-in benchmark: each key is pressed only after the word
the step expects was read on the screen (app/frames/screen_text.py), and only into the game.

    {"see": "settings", "within": 300, "press": "z", "dismiss": {"strange": "enter"}}
        wait up to `within` s until a word is on screen, then press. While waiting, a "dismiss" word (a popup)
        gets its own key. "settle": 2 waits that long after the word is seen before pressing (a menu that has
        just appeared may drop keys).
    {"see": "graphics", "within": 30, "again": {"press": "z", "every": 5}}
        while the word is not there yet, press the previous step's key again every 5 s (it was dropped).
    {"see": "graphics", "within": 30, "click": "graphics"}
        click the middle of that word where it was read (a menu that remembers its last item is not reached
        the same way by arrow keys each time).
    {"press": "up, up, left, left, down, enter"}      chords one after another (PAUSE_SECONDS apart)
    {"hold": "x", "seconds": 1.5}                    hold a key (some menus need a long press)
    {"wait": 3}                                      let the menu open
    {"see": "stop", "within": 120, "mark": True}     the benchmark is running: measuring starts here

A "see" with "optional": True goes on when the word does not come. Anything else that goes wrong (the word
never comes, the game is not in front, the screen cannot be read) stops the steps: no key is guessed, the
run fails with what was last seen, and that screenshot stays in the run folder. Runs in its own thread so
the capture loop keeps its timing.
"""

import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from app.frames.screen_text import find_word, read_screen, word_center

PAUSE_SECONDS = 0.5
LOOK_EVERY_SECONDS = 1.0
CLICK_SETTLE_SECONDS = 0.3          # the menu highlights the item under the mouse before the click
FRONT_GRACE_SECONDS = 10.0          # the game may lose the front briefly while it changes video mode
LOG_NAME = "screen_log.txt"          # what every read saw


def step_problems(steps) -> List[str]:
    """What is wrong in a steps list (empty when usable)."""
    from app.simulation.desktop import parse_chord
    out = []
    if not isinstance(steps, list) or not steps:
        return ["no steps"]
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            out.append(f"step {i} is not a dict")
            continue
        chords = [c.strip() for c in str(s.get("press", "")).split(",") if c.strip()]
        chords += [str(s["hold"])] if "hold" in s else []
        chords += [str(k) for k in (s.get("dismiss") or {}).values()]
        again = s.get("again")
        if again is not None:
            if not (isinstance(again, dict) and again.get("press") and isinstance(again.get("every"), (int, float))
                    and again["every"] > 0 and "see" in s):
                out.append(f"step {i}: 'again' needs 'see', 'press' and 'every' seconds")
            else:
                chords.append(str(again["press"]))
        out += [f"step {i}: unknown key {c!r}" for c in chords if parse_chord(c) is None]
        if not any(k in s for k in ("see", "press", "hold", "wait")):
            out.append(f"step {i} does nothing")
        if "click" in s and "see" not in s:
            out.append(f"step {i}: 'click' needs 'see'")
        if "see" in s and not (isinstance(s.get("within"), (int, float)) and s["within"] > 0):
            out.append(f"step {i}: 'see' needs 'within' seconds")
    if not any(s.get("mark") for s in steps if isinstance(s, dict)):
        out.append("no step marks the start of the benchmark")
    return out


class StepPlayer:
    def __init__(self, steps: List[dict], desktop, in_front: Callable[[], bool], folder: Path,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 reader: Callable = read_screen, stop: Optional[threading.Event] = None):
        self.steps = steps
        self.desktop = desktop
        self.in_front = in_front
        self.folder = Path(folder)
        self.clock = clock
        self.sleep = sleep
        self.reader = reader
        self.stop = stop or threading.Event()
        self.notes: List[str] = []
        self.problem: Optional[str] = None
        self.mark_time: Optional[float] = None       # clock() when the mark step's word was seen
        self.done = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._t0 = None
        self._lines: List[str] = []            # the last screen read

    # thread
    def start(self) -> "StepPlayer":
        self._t0 = self.clock()
        self._thread = threading.Thread(target=self.run, name="menu-steps", daemon=True)
        self._thread.start()
        return self

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def run(self) -> None:
        if self._t0 is None:
            self._t0 = self.clock()
        try:
            if self.desktop is None:
                raise _Stop("keys and screen reading work on Windows only for now")
            for i, step in enumerate(self.steps, 1):
                if self.stop.is_set():
                    raise _Stop("stopped")
                self._step(i, step)
        except _Stop as e:
            self.problem = str(e)
        except Exception as e:                  # a bug or a Windows error: stop, never guess
            self.problem = f"steps stopped: {e}"
        finally:
            self.done.set()

    # steps
    def _at(self) -> str:
        return f"{self.clock() - self._t0:.0f} s"

    def _step(self, i: int, step: dict) -> None:
        if "see" in step:
            found = self._see(i, step)
            if found is None:
                return                          # optional and not seen
            if step.get("mark"):
                self.mark_time = self.clock()
                self.notes.append(f"benchmark started ({found!r} seen at {self._at()})")
                self._keep_shot("benchmark_started.png")
            if step.get("settle"):
                self.sleep(float(step["settle"]))
            if "click" in step:
                self._click(i, str(step["click"]))
        for chord in [c.strip() for c in str(step.get("press", "")).split(",") if c.strip()]:
            self._press(chord)
            self.sleep(PAUSE_SECONDS)
        if "hold" in step:
            self._hold(str(step["hold"]), float(step.get("seconds", 1.0)))
        if "wait" in step:
            self.sleep(float(step["wait"]))

    def _see(self, i: int, step: dict) -> Optional[str]:
        words = step["see"]
        dismiss = step.get("dismiss") or {}
        again = step.get("again")
        deadline = self.clock() + float(step["within"])
        next_again = self.clock() + float(again["every"]) if again else None
        last: List[str] = []
        error = None
        while True:
            if self.stop.is_set():
                raise _Stop("stopped")
            lines, error = self.reader(self.desktop, self.folder / "screen.png")
            self._log(i, lines, error)
            if not error:
                last = self._lines = lines
                found = find_word(lines, words)
                if found:
                    self.notes.append(f"saw {found!r} at {self._at()}")
                    return found
                popup = find_word(lines, list(dismiss))
                if popup:
                    self._press(str(dismiss[popup]))
                    self.notes.append(f"closed a popup ({popup!r})")
                elif again and self.clock() >= next_again:
                    self._press(str(again["press"]))
                    self.notes[-1] += " again"
                    next_again = self.clock() + float(again["every"])
            if self.clock() >= deadline:
                break
            self.sleep(LOOK_EVERY_SECONDS)
        wanted = words if isinstance(words, str) else " / ".join(words)
        if step.get("optional"):
            self.notes.append(f"{wanted!r} not seen (optional)")
            return None
        shot = self._keep_shot(f"step_{i}_not_found.png")
        seen = ", ".join(last[:8]) if last else ("nothing: " + error if error else
                                                 "no words at all (a black screenshot? use borderless window)")
        raise _Stop(f"step {i}: {wanted!r} did not appear within {step['within']:g} s; the screen showed: "
                    f"{seen}" + (f" (screenshot {shot})" if shot else ""))

    def _check_front(self, what: str) -> None:
        deadline = self.clock() + FRONT_GRACE_SECONDS
        while not self.in_front():
            if self.clock() >= deadline or self.stop.is_set():
                raise _Stop(f"{what} not sent: the game was not in front")
            self.sleep(0.5)

    def _press(self, chord: str) -> None:
        from app.simulation.desktop import parse_chord
        self._check_front(chord)
        self.desktop.press(parse_chord(chord))
        self.notes.append(f"pressed {chord}")

    def _click(self, i: int, word: str) -> None:
        point = word_center(self._lines, word)
        if point is None:
            raise _Stop(f"step {i}: {word!r} was read but not where it is on the screen; nothing clicked")
        self._check_front(f"click on {word!r}")
        self.desktop.mouse_to(*point)
        self.sleep(CLICK_SETTLE_SECONDS)
        self._check_front(f"click on {word!r}")
        self.desktop.click()
        self.notes.append(f"clicked {word!r} at {point[0]},{point[1]}")

    def _hold(self, chord: str, seconds: float) -> None:
        from app.simulation.desktop import parse_chord
        codes = parse_chord(chord)
        self._check_front(f"holding {chord}")
        self.desktop.key_down(codes)
        try:
            self.sleep(seconds)
        finally:
            self.desktop.key_up(codes)
        self.notes.append(f"held {chord} {seconds:g} s")

    def _log(self, i: int, lines: List[str], error: Optional[str]) -> None:
        """What each read saw, one line per read (screen_log.txt in the run folder): shows afterwards where a
        game stayed, without keeping a full screenshot every second."""
        said = error or (" | ".join(lines)[:300] if lines else "(no words)")
        try:
            with open(self.folder / LOG_NAME, "a", encoding="utf-8") as f:
                f.write(f"{self._at()}\tstep {i}\t{said}\n")
        except OSError:
            pass

    def _keep_shot(self, name: str) -> Optional[str]:
        src = self.folder / "screen.png"
        try:
            if src.is_file():
                dest = self.folder / name
                dest.write_bytes(src.read_bytes())
                return str(dest)
        except OSError:
            pass
        return None

    def summary(self) -> str:
        return "menu steps: " + (", ".join(self.notes) or "none") + (f"; {self.problem}" if self.problem else "")


class _Stop(Exception):
    pass
