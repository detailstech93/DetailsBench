"""A route: keys held and mouse moves played inside a game during a gameplay capture, so a game without a
built-in benchmark is measured on the same path every time (data/game_routes.py holds the routes).

    route = {"loop_from": 45,                  # s after the launch: the loop starts (the save is loaded by then)
             "loop_seconds": 20,               # one pass of the loop; it repeats until the capture ends
             "loop": [[0, "hold", "w", 8],     # [s into the pass, "hold", chord, seconds held]
                      [8, "mouse", 900, 0, 1], # [s, "mouse", dx, dy, over seconds]: relative, turns the camera
                      [9.5, "press", "space"]]}  # [s, "press", chord]: down and up at once

Walking forward, turning around and walking back keeps the character on one stretch of the map, so every
measured window sees much the same scene. Every step is sent only while the game's own window is in front;
when another window is in front the held keys are released at once and the steps that fall due are skipped
(counted in the run's message). Held keys are always released at the end, on stop and on error.
"""

from typing import Callable, List, Optional, Tuple

from app.simulation.desktop import parse_chord

KINDS = {"press": 3, "hold": 4, "mouse": 5}
MAX_MOUSE_STEP = 10000          # counts in one step: a typo must not spin the camera wildly


def route_problems(route: dict) -> List[str]:
    """What is wrong with a route (empty when it can be played)."""
    problems = []
    loop = route.get("loop") or []
    period = route.get("loop_seconds")
    if not loop:
        problems.append("the loop has no steps")
    if not isinstance(period, (int, float)) or period <= 0:
        problems.append("loop_seconds must be a positive number")
    if not isinstance(route.get("loop_from"), (int, float)) or route["loop_from"] < 0:
        problems.append("loop_from must be a number of seconds")
    for step in loop:
        kind = step[1] if len(step) > 1 else None
        if kind not in KINDS or len(step) != KINDS[kind]:
            problems.append(f"step {step!r}: expected [s, 'press', chord], [s, 'hold', chord, s] or "
                            "[s, 'mouse', dx, dy, s]")
            continue
        at = step[0]
        if not isinstance(at, (int, float)) or at < 0:
            problems.append(f"step {step!r}: bad time")
        if kind in ("press", "hold") and parse_chord(str(step[2])) is None:
            problems.append(f"step {step!r}: unknown key {step[2]!r}")
        if kind == "hold" and not (isinstance(step[3], (int, float)) and step[3] > 0):
            problems.append(f"step {step!r}: hold time must be positive")
        if kind == "mouse" and (abs(step[2]) > MAX_MOUSE_STEP or abs(step[3]) > MAX_MOUSE_STEP or step[4] < 0):
            problems.append(f"step {step!r}: mouse move out of range")
        end = at + (step[3] if kind == "hold" else step[4] if kind == "mouse" else 0)
        if isinstance(period, (int, float)) and end > period:
            problems.append(f"step {step!r}: ends after the pass ({period} s)")
    return problems


def describe_route(route: dict) -> str:
    """One line for the confirm dialog."""
    parts = []
    for step in route.get("loop", []):
        if step[1] == "hold":
            parts.append(f"hold {step[2]} {step[3]:g} s")
        elif step[1] == "press":
            parts.append(f"press {step[2]}")
        else:
            parts.append(f"mouse {step[2]:+d},{step[3]:+d}")
    return (f"from {route['loop_from']:g} s after the launch, every {route['loop_seconds']:g} s: "
            + ", ".join(parts))


class RoutePlayer:
    def __init__(self, route: dict, desktop_fn: Callable[[], object], in_front: Callable[[], bool], until: float):
        self.desktop_fn, self.in_front = desktop_fn, in_front
        self.events: List[Tuple[float, list]] = []
        t, period = float(route["loop_from"]), float(route["loop_seconds"])
        while t < until:
            for step in route["loop"]:
                if t + step[0] < until:
                    self.events.append((t + step[0], step))
            t += period
        self.events.sort(key=lambda e: e[0])
        self.holds: List[Tuple[float, List[int]]] = []                 # (release at, codes)
        self.moves: List[list] = []                                     # [start, end, dx, dy, sent x, sent y]
        self.sent = self.skipped = 0
        self.paused = False
        self.problem: Optional[str] = None

    def _desk(self):
        return self.desktop_fn()

    def release_all(self):
        """Key up for every held key (harmless wherever the focus is; a key left down would keep walking)."""
        holds, self.holds, self.moves = self.holds, [], []
        for _, codes in holds:
            self._desk().key_up(codes)

    def tick(self, elapsed: float):
        if self.problem:
            return
        try:
            self._tick(elapsed)
        except Exception as e:          # the route only helps; the capture goes on without it
            self.problem = f"route stopped: {e}"
            try:
                self.release_all()
            except Exception:
                pass

    def _tick(self, elapsed: float):
        due = []
        while self.events and self.events[0][0] <= elapsed:
            due.append(self.events.pop(0))
        if not (due or self.holds or self.moves):
            return
        if not self.in_front():
            self.paused = True
            self.release_all()
            self.skipped += len(due)
            return
        desk = self._desk()
        for end, codes in [h for h in self.holds if h[0] <= elapsed]:
            desk.key_up(codes)
        self.holds = [h for h in self.holds if h[0] > elapsed]
        for at, step in due:
            kind = step[1]
            if kind == "press":
                desk.press(parse_chord(step[2]))
            elif kind == "hold":
                codes = parse_chord(step[2])
                desk.key_down(codes)
                self.holds.append((at + step[3], codes))
            else:
                self.moves.append([at, at + step[4], step[2], step[3], 0, 0])
            self.sent += 1
        for m in self.moves:
            start, end, dx, dy = m[:4]
            f = 1.0 if end <= start else min(1.0, max(0.0, (elapsed - start) / (end - start)))
            x, y = round(dx * f) - m[4], round(dy * f) - m[5]
            if x or y:
                desk.mouse_move(x, y)
                m[4], m[5] = m[4] + x, m[5] + y
        self.moves = [m for m in self.moves if (m[4], m[5]) != (m[2], m[3])]

    def summary(self) -> str:
        text = f"route: {self.sent} steps sent"
        if self.skipped:
            text += f", {self.skipped} skipped (the game was not in front)"
        if self.paused:
            text += "; held keys were released while the game was not in front"
        if self.problem:
            text += f"; {self.problem}"
        return text
