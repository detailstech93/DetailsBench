from app.frames.game_benchmark import GameBenchmark
from app.frames.route import RoutePlayer, describe_route, route_problems
from app.simulation.desktop import parse_chord
from data.game_routes import GAME_ROUTES, route_for
from tests.test_game_benchmark import Fakes, spec

W = parse_chord("w")
ROUTE = {"loop_from": 10, "loop_seconds": 10,
         "loop": [[0, "hold", "w", 4], [5, "mouse", 100, 0, 2], [8, "press", "space"]]}


class Desk:
    """Records what the route sends; `front` says whether the game is in front."""

    def __init__(self):
        self.log, self.front, self.down = [], True, set()

    def key_down(self, codes):
        self.log.append(("down", tuple(codes)))
        self.down.update(codes)

    def key_up(self, codes):
        self.log.append(("up", tuple(codes)))
        self.down.difference_update(codes)

    def press(self, codes):
        self.log.append(("press", tuple(codes)))

    def mouse_move(self, dx, dy):
        self.log.append(("mouse", dx, dy))


def play(route, until, desk, front=lambda t: True, step=0.5):
    t = [0.0]
    player = RoutePlayer(route, lambda: desk, lambda: front(t[0]), until)
    while t[0] < until:
        player.tick(t[0])
        t[0] += step
    player.release_all()
    return player


def test_the_loop_repeats_holds_turns_and_presses():
    desk = Desk()
    player = play(ROUTE, 30, desk)
    kinds = [e[0] for e in desk.log]
    assert kinds.count("down") == 2 and kinds.count("up") == 2 and kinds.count("press") == 2   # passes at 10, 20
    assert sum(e[1] for e in desk.log if e[0] == "mouse") == 200          # 100 per pass, spread over 2 s
    assert len([e for e in desk.log if e[0] == "mouse"]) > 2
    assert not desk.down and player.summary() == "route: 6 steps sent"
    first_up = desk.log.index(("up", tuple(W)))
    assert desk.log[:first_up] == [("down", tuple(W))]                    # nothing else before the release


def test_keys_are_released_when_the_game_leaves_the_front_and_steps_skipped():
    desk = Desk()
    player = play(ROUTE, 30, desk, front=lambda t: not 12 <= t < 22)     # another window in front at 12-22 s
    assert desk.log[:2] == [("down", tuple(W)), ("up", tuple(W))]         # released at 12 s, not at 14 s
    assert player.skipped == 3                                            # mouse 15, space 18, hold 20
    assert not desk.down and "3 skipped (the game was not in front)" in player.summary()
    assert "held keys were released" in player.summary()


def test_a_failing_desktop_stops_only_the_route():
    class Broken(Desk):
        def mouse_move(self, dx, dy):
            raise OSError("SendInput failed")
    desk = Broken()
    player = play(ROUTE, 30, desk)
    assert "route stopped: SendInput failed" in player.summary() and not desk.down


def test_route_problems_and_description():
    assert route_problems(ROUTE) == []
    assert describe_route(ROUTE) == "from 10 s after the launch, every 10 s: hold w 4 s, mouse +100,+0, press space"
    bad = {"loop_from": -1, "loop_seconds": 5,
           "loop": [[0, "hold", "nokey", 2], [1, "hold", "w", 9], [2, "mouse", 99999, 0, 1], [3, "jump"]]}
    text = " | ".join(route_problems(bad))
    assert "loop_from" in text and "unknown key 'nokey'" in text and "ends after the pass" in text
    assert "mouse move out of range" in text and "expected [s, 'press', chord]" in text
    assert route_problems({"loop_from": 0, "loop_seconds": 0, "loop": []})[:2] == \
        ["the loop has no steps", "loop_seconds must be a positive number"]


def test_every_route_in_the_data_can_be_played():
    assert route_for("x") is None and route_for(1) is None
    for app_id, route in GAME_ROUTES.items():
        assert isinstance(app_id, int) and route_problems(route) == [], app_id
        assert isinstance(route["verified"], bool) and route["start"] and route["note"]


class FrontDesk(Desk):
    def __init__(self, fakes):
        super().__init__()
        self.fakes = fakes

    def foreground_pid(self):
        return 7 if self.fakes.now[0] < 50 else 999


def test_a_capture_plays_the_route_and_always_releases(tmp_path):
    fakes = Fakes(tmp_path)
    desk = FrontDesk(fakes)
    s = spec(install_path=str(fakes.install), route=ROUTE)
    bench = GameBenchmark(s, tmp_path / "run", None, lambda m: None, elevated_runner=fakes.elevated,
                          launcher=fakes.launcher, snapshot=fakes.snapshot, clock=fakes.clock, sleep=fakes.sleep,
                          folder_procs=fakes.folder_procs, closer=fakes.closer,
                          exe_lister=lambda folder: ["Game.exe"], desktop=desk)
    cap = bench.run()
    assert cap.status == "recorded" and "; route: " in cap.message and "skipped" in cap.message
    assert ("down", tuple(W)) in desk.log and not desk.down
