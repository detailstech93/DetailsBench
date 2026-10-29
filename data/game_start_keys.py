"""Keys the app presses inside a game after launching it, to get from its menus into the scene it measures
(a gameplay capture or a built-in benchmark without a launch argument).

    GAME_START_KEYS = {app_id: {"keys": [[seconds after the launch, "chord"], ...],
                                "verified": bool, "note": "what the keys do"}}

Chords use app/simulation/desktop.py names ("enter", "esc", "down", "alt+t"). A key is pressed only when the
foreground window belongs to a process the launch started inside the game's folder; otherwise it is skipped
and the run says so (keys never go to another program). Menus change between game versions, so an entry
stays "verified": False until it was watched on a real run with the owner; the times include loading, so
they are generous. Nothing is listed yet: each game's path is found together with the owner, screenshot by
screenshot, before it is written here.
"""

from typing import Dict, List, Optional, Tuple

GAME_START_KEYS: Dict[int, dict] = {}

# Checked menu steps to a game's built-in benchmark (app/frames/menu_steps.py): every key waits for a word read
# on the screen first. Paths come from LTT Labs' MarkBench harnesses (github.com/LTTLabsOSS/markbench-tests,
# read 2026-10-18; facts only, no code taken) and stay "verified": False until watched on a real run here.
GAME_MENU_STEPS: Dict[int, dict] = {
    1174180: {      # Red Dead Redemption 2: main menu -> Settings (Z) -> Graphics -> hold X "Run Benchmark Test"
        "steps": [
            # the main menu drops a key pressed as soon as it shows (2026-10-19): wait, and press Z again if needed
            {"see": "settings", "within": 300, "dismiss": {"strange": "enter"}, "settle": 3, "press": "z", "wait": 3},
            # the settings menu starts on the item used last (Display, Camera, ...): click Graphics where it is read
            {"see": "graphics", "within": 30, "again": {"press": "z", "every": 5}, "settle": 1, "click": "graphics",
             "wait": 3},
            # the mouse highlights Graphics; if the click did not open it, Enter (Select) does
            # holding X 1.5 s started it once and not the next time (2026-10-19): hold longer, and again while the
            # Graphics page (its "Run Benchmark Test") is still there
            {"see": "benchmark", "within": 30, "again": {"press": "enter", "every": 4}, "hold": "x", "seconds": 2.5,
             "wait": 1},
            {"press": "enter"},
            # "Stop Benchmarking" in the corner: it runs. Loading took over 3 min on the first try here
            {"see": "stop", "within": 300, "mark": True,
             "again": {"hold": "x", "seconds": 2.5, "press": "enter", "every": 10, "while": "benchmark test"}},
        ],
        # it writes its results file only when it quits (2026-10-18): if closing its window asks to quit, Enter
        # answers it (not seen yet here; optional, so a game that just closes goes on)
        "quit_steps": [{"see": ["quit", "exit"], "within": 15, "optional": True, "settle": 1, "press": "enter"}],
        "benchmark_seconds": 270,       # about 4.5 min from "Stop Benchmarking" to the results (MarkBench)
        "verified": False,
        "note": "Settings, Graphics, hold X on Run Benchmark Test, confirm; measuring starts when "
                "'Stop Benchmarking' shows. The whole benchmark takes about 4.5 min.",
    },
    750920: {       # Shadow of the Tomb Raider: main menu -> Options -> Display and Graphics -> Run Benchmark
        "launch_args": ["-nolauncher"],         # skips the game's own launcher window (MarkBench)
        "steps": [
            # a popup about the graphics adapter or an unavailable feature may come first: Enter closes it
            {"see": "options", "within": 240, "dismiss": {"adapter": "enter", "unavailable": "enter"},
             "settle": 2, "click": "options", "wait": 3},
            # menus remember the last item: click the word where it is read, then Enter if the click was missed
            {"see": "graphics", "within": 30, "again": {"press": "enter", "every": 5, "while": "options"},
             "settle": 1, "click": "graphics", "wait": 3},
            # R runs the benchmark from the Display and Graphics page. 2026-10-29: an instant R was not seen (five
            # times) and MarkBench's up, right first changed a setting on the page: R alone, held 0.15 s
            {"see": "benchmark", "within": 30, "settle": 1, "press": "r", "tap": 0.15, "wait": 3},
            # the benchmark shows its FPS while it runs
            {"see": "fps", "within": 180, "mark": True, "tap": 0.15,
             "again": {"press": "r", "every": 10, "while": "run benchmark"}},
        ],
        # closing its window asks whether to quit (owner, 2026-10-29: left unanswered, it was closed by force)
        "quit_steps": [{"see": "yes", "within": 15, "settle": 1, "click": "yes"}],
        "benchmark_seconds": 150,       # about 150 s, then its results screen (MarkBench)
        "verified": False,
        "note": "Options, Display and Graphics, R for the benchmark; measuring starts when its FPS shows. "
                "The benchmark takes about 2.5 min.",
    },
    2369390: {      # Far Cry 6: main menu -> Options -> Video -> (E) Quality -> (E E) Advanced -> F5 Benchmark
        # Steam starts it through Ubisoft Connect (signed in by the owner); MarkBench's deprecated harness,
        # read 2026-10-19: Space skips the logos and the intro, Esc closes a "later" popup
        # keys pressed in an instant were not seen on 2026-10-23 (Space in the intro, F5): F5 is held 0.15 s.
        # Space held 0.15 s, then 2 s, did not skip the intro (owner): now pressed twice (owner's idea; the first
        # press may only show a "skip" prompt), each held 0.15 s
        "steps": [
            # Space skips the intro (owner, 2026-10-23); its first words come at about 30 s
            {"see": ["government", "original", "starring"], "within": 120, "optional": True,
             "press": "space, space", "tap": 0.15, "wait": 2},
            {"see": "options", "within": 300,
             "dismiss": {"government": "space, space", "original": "space, space", "starring": "space, space",
                         "later": "esc"},
             "tap": 0.15, "settle": 2, "click": "options", "wait": 3},
            {"see": "video", "within": 30, "settle": 1, "click": "video", "wait": 3},
            # the Video page has the Video Adapter setting; E moves one tab to the right
            {"see": "adapter", "within": 30, "settle": 1, "press": "e", "wait": 2},
            {"see": "filtering", "within": 30, "press": "e, e", "wait": 2},
            # the Advanced page (Enable Framerate Lock): F5 starts the benchmark
            {"see": "lock", "within": 30, "settle": 1, "press": "f5", "tap": 0.15, "wait": 3},
            # "Toggle UI" shows in the lower right while it runs
            {"see": "toggle", "within": 90, "mark": True, "tap": 0.15,
             "again": {"press": "f5", "every": 10, "while": "lock"}},
        ],
        "benchmark_seconds": 60,        # MarkBench waits 60 s after "toggle", then for its results screen
        # FarCry6.exe ends after about 5 s and Ubisoft Connect starts it again (2026-10-23): the first exit is not
        # the game closing
        "relaunches": True,
        "verified": False,
        "note": "Options, Video, two tabs right, F5 for the benchmark; measuring starts when 'Toggle UI' shows. "
                "The benchmark takes about a minute.",
    },
    397540: {       # Borderlands 3: main menu -> Options -> Visuals -> Benchmark (third tab) -> Start Benchmark
        # no launch argument starts it (AnandTech); the path is PCGamingWiki's "List of games with built-in
        # benchmarks" (read 2026-10-23). Not in MarkBench. The tab keys (E) are a guess until watched here.
        "steps": [
            {"see": "options", "within": 300, "dismiss": {"press any": "enter"}, "settle": 2, "click": "options",
             "wait": 3},
            {"see": "visuals", "within": 30, "again": {"press": "enter", "every": 5, "while": "options"},
             "settle": 1, "click": "visuals", "wait": 3},
            # the Visuals page opens on its first tab: E moves one tab to the right until the Benchmark tab shows
            {"see": "start benchmark", "within": 40, "again": {"press": "e", "every": 4}, "settle": 1,
             "click": "start", "wait": 3},
            # the benchmark shows its frame rate while it runs (Notebookcheck)
            {"see": "fps", "within": 180, "dismiss": {"are you sure": "enter"}, "mark": True,
             "again": {"press": "enter", "every": 10, "while": "start benchmark"}},
        ],
        "benchmark_seconds": 110,       # "nearly two minutes" (Notebookcheck), then its results screen
        "verified": False,
        "note": "Options, Visuals, the Benchmark tab, Start Benchmark; measuring starts when its FPS shows. "
                "The benchmark takes about 2 min.",
    },
}


def menu_steps_for(app_id) -> Optional[list]:
    try:
        entry = GAME_MENU_STEPS.get(int(app_id))
    except (TypeError, ValueError):
        return None
    return [dict(s) for s in entry["steps"]] if entry else None


def _menu_entry(app_id) -> dict:
    try:
        return GAME_MENU_STEPS.get(int(app_id)) or {}
    except (TypeError, ValueError):
        return {}


def quit_steps_for(app_id) -> Optional[list]:
    """Menu steps played after the game's window is asked to close (a "quit?" question to answer), or None."""
    steps = _menu_entry(app_id).get("quit_steps")
    return [dict(s) for s in steps] if steps else None


def menu_launch_args(app_id) -> List[str]:
    """Launch flags the menu steps expect (for example a skipped launcher window)."""
    return [str(a) for a in _menu_entry(app_id).get("launch_args", [])]


def benchmark_seconds_for(app_id) -> Optional[int]:
    """About how long the game's built-in benchmark runs after the step marked as its start, or None."""
    value = _menu_entry(app_id).get("benchmark_seconds")
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def relaunches_for(app_id) -> bool:
    """The game's first process ends and its launcher starts it again: its exit does not end the capture."""
    return bool(_menu_entry(app_id).get("relaunches"))


def start_keys_for(app_id) -> List[Tuple[float, str]]:
    try:
        entry = GAME_START_KEYS.get(int(app_id))
    except (TypeError, ValueError):
        return []
    if not entry:
        return []
    return [(float(at), str(chord)) for at, chord in entry.get("keys", [])]
