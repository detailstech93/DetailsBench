"""Earlier runs of the same tests on the same PC in the report (REAL temporary database)."""

import time

from app.database import Database
from app.export import build_session_export
from app.history import earlier_comparisons, pc_key
from app.report import _earlier_section

ENV = {"cpu": {"value": "Test CPU 9000"}, "gpu": {"value": ["GPU B", "GPU A"]}, "ram_gb": {"value": 63.8},
       "os_family": {"value": "windows"}, "native_arch": {"value": "x64"}}


def manifest(env=ENV, version_runtime=None):
    return {"environment": env, "comparability": {"runtimes": [], "any_emulated": False}, "app": {}}


def session(db, scores, env=ENV, name="tool_a", version="1.0", is_test=False):
    sid = db.create_session(f"s{len(db.search_sessions(include_test=True))}", is_test=is_test)
    for i, score in enumerate(scores, 1):
        db.create_result(sid, name, "cpu", score, "pts", benchmark_version=version, run_index=i, is_test=is_test)
    db.save_session_manifest(sid, manifest(env))
    time.sleep(1.05)                    # created_at has one-second steps: keep the order clear
    return sid


def test_pc_key():
    assert pc_key(manifest()) == ("Test CPU 9000", ("GPU A", "GPU B"), 64, "windows")
    assert pc_key({"environment": {"cpu": {"value": None}}}) is None and pc_key(None) is None


def test_same_pc_earlier_sessions_are_compared_and_others_left_out(tmp_path):
    db = Database(str(tmp_path / "h.db"))
    first = session(db, [100.0, 102.0])
    session(db, [50.0], env=dict(ENV, cpu={"value": "Other CPU"}))          # another PC: never used
    session(db, [999.0], is_test=True)                                    # test data: not for a real session
    second = session(db, [104.0, 106.0])
    now = session(db, [110.0, 110.0])
    export = build_session_export(db, now)
    rows = export["earlier"]
    assert [r["benchmark"] for r in rows] == ["tool_a"] and rows[0]["unit"] == "pts"
    assert [e["session_id"] for e in rows[0]["earlier"]] == [second, first]
    assert round(rows[0]["compare"]["delta_percent"], 2) == 4.76 and rows[0]["compare"]["status"] == "ok"
    html = _earlier_section(export)
    assert "<h2>Compared with earlier runs on this PC</h2>" in html and "+4.8%" in html
    assert "105.00 pts" in html and "101.00" in html and "50.00" not in html
    first_export = build_session_export(db, first)
    assert first_export["earlier"] == [] and _earlier_section(first_export) == ""


def test_a_new_major_version_is_not_comparable(tmp_path):
    db = Database(str(tmp_path / "v.db"))
    session(db, [100.0, 100.0], version="5.1")
    now = session(db, [200.0, 200.0], version="6.0")
    rows = earlier_comparisons(db, next(s for s in db.search_sessions() if s["id"] == now),
                               db.get_session_manifest(now), db.get_runs(now))
    assert rows[0]["compare"]["status"] == "not_comparable"
    assert "not comparable" in _earlier_section({"earlier": rows}) and "Major benchmark versions differ" in \
        _earlier_section({"earlier": rows})


def test_game_scores_are_compared_only_with_the_same_settings():
    from app.history import effective_game_settings, game_settings_issue

    def game(before, requested=None, seconds=60, refused=None):
        return {"run_settings": {"steam_app_id": "1091500", "capture_seconds": seconds, "game_settings": {
            "before": before, "requested": requested or {}, "refused": refused or {}}}}
    base = {"preset": "High", "dlss_mode": "Quality", "ray_tracing": False}
    forced = game(base, {"preset": "Ultra"})
    assert effective_game_settings(forced) == {"preset": "Ultra", "dlss_mode": "Quality", "ray_tracing": False}
    assert effective_game_settings(game(base, {"preset": "Ultra"}, refused={"preset": "no"}))["preset"] == "High"
    assert game_settings_issue("game_x", forced, game(base, {"preset": "Ultra"})) is None
    # DLSS and ray tracing changed by the owner (the app forced only the preset): still caught and named
    other = game(dict(base, dlss_mode="Off", ray_tracing=True), {"preset": "Ultra"})
    issue = game_settings_issue("game_x", forced, other)
    assert issue["level"] == "block"
    assert issue["message"] == "Different game settings: dlss_mode Quality -> Off, ray_tracing False -> True."
    assert "measured time 60 s -> 90 s" in game_settings_issue("game_x", forced, game(base, {"preset": "Ultra"},
                                                                                       90))["message"]
    partly = game_settings_issue("game_x", forced, game({"preset": "High", "dlss_mode": "Quality"},
                                                        {"preset": "Ultra"}))
    assert partly["level"] == "warn" and partly["message"] == "Known for one run only: ray_tracing."
    declared = {"run_settings": {"steam_app_id": "1", "game_settings": {"declared": {"preset": "High"}}}}
    assert effective_game_settings(declared) == {"preset": "High"}
    assert game_settings_issue("cyberpunk2077_builtin_benchmark", {}, forced)["code"] == "game_settings_unknown"
    assert game_settings_issue("tool_a", {}, {}) is None                              # not a game
