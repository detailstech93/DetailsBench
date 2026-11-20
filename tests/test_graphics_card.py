"""app/games/graphics_card.py and the retry in a whole benchmark (Fedora 2026-11-20: RDR2 drew on llvmpipe)."""

from app.frames.game_benchmark import GameBenchmark, GameCapture, GameBenchmarkSpec
from app.games.graphics_card import CardChoice
from app.games.red_dead_redemption2 import APP_ID

XML = '<?xml version="1.0"?>\n<rage__fwuiSystemSettingsCollection>\n  <video>\n    <adapterIndex value="1" />\n' \
      '    <outputIndex value="0" />\n  </video>\n</rage__fwuiSystemSettingsCollection>\n'


def settings(tmp_path):
    env = {"PCBM_DOCUMENTS": str(tmp_path / "docs")}
    path = tmp_path / "docs" / "Rockstar Games" / "Red Dead Redemption 2" / "Settings" / "system.xml"
    path.parent.mkdir(parents=True)
    path.write_text(XML, encoding="utf-8")
    return env, path


def test_rdr2s_card_number_is_tried_remembered_and_put_back(tmp_path):
    env, path = settings(tmp_path)
    card = CardChoice.for_game(APP_ID, tmp_path / "run", tmp_path / "data", env)
    assert card is not None and card.store.path == path and card.current() == "1"
    assert card.next_number() == "0"                                       # the one in the file counts as tried
    card.set("0")
    assert '<adapterIndex value="0" />' in path.read_text(encoding="utf-8") and card.next_number() == "2"
    card.set("2")
    assert card.next_number() is None
    assert card.remembered() is None
    card.remember("0")
    assert CardChoice.for_game(APP_ID, tmp_path / "run2", tmp_path / "data", env).remembered() == "0"
    assert card.restore() == "" and path.read_text(encoding="utf-8") == XML       # as it was before the first write
    assert CardChoice.for_game("397540", tmp_path / "run", tmp_path / "data", env) is None   # no such number
    assert CardChoice.for_game(APP_ID, tmp_path / "run", tmp_path / "data", {"PCBM_DOCUMENTS": "/nowhere"}) is None


def test_a_run_on_the_cpu_is_run_again_on_the_next_card_number_which_is_kept(tmp_path, monkeypatch):
    env, path = settings(tmp_path)
    s = GameBenchmarkSpec(game_name="Red Dead Redemption 2", app_id=str(APP_ID), install_path=str(tmp_path / "g"),
                          discovered_appids=[str(APP_ID)], presentmon_path="/usr/bin/mangohud", warmup_runs=0,
                          repetitions=1, benchmark_seconds=60, steps=[{"see": "stop", "within": 60, "mark": True}])
    bench = GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=True, sleep=lambda x: None)
    monkeypatch.setattr(bench, "_card_choice", lambda: CardChoice(APP_ID, "video/adapterIndex", *_store(env), tmp_path
                                                                     / "run", tmp_path / "data"))
    seen = []

    def one(pass_index=None):
        seen.append(path.read_text(encoding="utf-8").split('adapterIndex value="')[1][0])
        if seen[-1] == "1":
            return GameCapture("failed", "the game drew on the CPU (llvmpipe)", on_cpu=True)
        return GameCapture("recorded", "60 s recorded", windows=[])
    monkeypatch.setattr(bench, "_run_mangohud", one)
    cap = bench.run()
    assert seen == ["1", "0"] and "graphics card number 0 written, run again" in cap.message
    assert path.read_text(encoding="utf-8") == XML                          # put back after the runs
    seen.clear()
    bench.run()                                                              # the next time: number 0 first
    assert seen == ["0"] and path.read_text(encoding="utf-8") == XML


def _store(env):
    from app.games.settings_file import adapter_for
    adapter = adapter_for(APP_ID)
    return adapter, adapter.store(env)


def test_a_remembered_number_that_draws_on_the_cpu_leaves_the_files_own_number_to_try(tmp_path):
    env, path = settings(tmp_path)
    card = CardChoice.for_game(APP_ID, tmp_path / "run", tmp_path / "data", env)
    card.set("0", ran=False)                    # remembered, written before the first run: "1" was never run
    assert card.next_number() == "1"
    card.set("1")
    assert card.next_number() == "2" and card.restore() == "" and path.read_text(encoding="utf-8") == XML


def test_a_number_that_drew_on_the_card_is_kept_even_when_the_menus_failed_after(tmp_path, monkeypatch):
    """Fedora 2026-11-20: number 0 drew on the Radeon, the menus then failed, and the next RDR2 started on 1 again."""
    env, path = settings(tmp_path)
    s = GameBenchmarkSpec(game_name="Red Dead Redemption 2", app_id=str(APP_ID), install_path=str(tmp_path / "g"),
                          discovered_appids=[str(APP_ID)], presentmon_path="/usr/bin/mangohud", warmup_runs=0,
                          repetitions=1, benchmark_seconds=60, steps=[{"see": "stop", "within": 60, "mark": True}])
    bench = GameBenchmark(s, tmp_path / "run", None, lambda m: None, linux=True, sleep=lambda x: None)
    monkeypatch.setattr(bench, "_card_choice", lambda: CardChoice(APP_ID, "video/adapterIndex", *_store(env), tmp_path
                                                                     / "run", tmp_path / "data"))
    seen = []

    def one(pass_index=None):
        seen.append(path.read_text(encoding="utf-8").split('adapterIndex value="')[1][0])
        if seen[-1] == "1":
            bench.drawing_device = "llvmpipe (LLVM 23.1.5)"
            return GameCapture("failed", "the game drew on the CPU (llvmpipe)", on_cpu=True)
        bench.drawing_device = None             # MangoHud wrote its log when the game closed, after the menus failed
        logs = bench.shots_dir / "mangohud_logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "RDR2_2026-11-20_11-20-51.csv").write_text(
            "os,cpu,gpu,ram\nSteam Runtime 4,AMD Ryzen 7 7730U,AMD Radeon Graphics (RADV RENOIR),15732208\n",
            encoding="utf-8")
        return GameCapture("failed", "step 3: 'benchmark' did not appear")
    monkeypatch.setattr(bench, "_run_mangohud", one)
    bench.run()
    seen.clear()
    bench.run()
    assert seen == ["0"]
