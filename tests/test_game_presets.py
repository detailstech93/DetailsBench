from app.games.presets import (GamePreset, NO_SETTING, NOT_SEEN, describe, plan_preset, resolutions_offered,
                               summary_lines)

CYBERPUNK = {"resolution": ["2560x1440"], "preset": ["Custom", "Low", "Ultra"], "upscaler": ["Off", "DLSS", "FSR 3"],
             "ray_tracing": [True, False], "texture_quality": ["Low", "Medium", "High"]}
RDR2 = {"resolution": ["2560x1080"], "preset": ["Low", "Medium", "High", "Ultra"]}


def test_cyberpunk_choices_become_its_settings():
    requested, skipped = plan_preset(1091500, GamePreset("2560x1440", "fsr", "on", "ultra"), CYBERPUNK)
    assert requested == {"resolution": "2560x1440", "upscaler": "FSR 3", "ray_tracing": True, "preset": "Ultra"}
    assert skipped == {}
    requested, _ = plan_preset(1091500, GamePreset(upscaler="off", ray_tracing="off"), CYBERPUNK)
    assert requested == {"upscaler": "Off", "ray_tracing": False}


def test_values_never_seen_or_missing_settings_are_not_guessed():
    requested, skipped = plan_preset(1091500, GamePreset("3840x2160", "xess", "keep", "high"), CYBERPUNK)
    assert requested == {}
    assert skipped == {"resolution": NOT_SEEN, "upscaler": NOT_SEEN, "quality": NOT_SEEN}
    requested, skipped = plan_preset(1174180, GamePreset("keep", "dlss", "on", "high"), RDR2)
    assert requested == {"preset": "High"}          # the game's whole preset (every quality setting at High)
    assert skipped == {"upscaler": NO_SETTING, "ray_tracing": NO_SETTING}
    assert plan_preset(999, GamePreset(quality="low"), {}) == ({}, {"quality": NO_SETTING})


def test_keep_everything_changes_nothing():
    assert plan_preset(1091500, GamePreset(), CYBERPUNK) == ({}, {})
    assert describe(GamePreset()) == "the game's own settings"
    assert describe(GamePreset("1920x1080", "dlss", "on", "ultra")) == \
        "Resolution 1920x1080, Upscaler DLSS, Ray tracing On, Quality Ultra"


def test_summary_lines_say_what_is_set_and_why_not():
    lines = summary_lines(1091500, "Cyberpunk 2077", GamePreset("keep", "dlss", "on", "high"), CYBERPUNK)
    assert lines[0] == "Cyberpunk 2077: Upscaler DLSS, Ray tracing On."
    assert lines[1].startswith("    not set: Quality High (the game was never seen")
    assert summary_lines(1, "X", GamePreset(quality="low"), None, "start the game once first") == \
        ["X: settings cannot be set (start the game once first); the game's own are used."]
    assert summary_lines(1, "X", GamePreset(), None) == ["X: the game's own settings."]


def test_preset_round_trip_and_resolution_list():
    p = GamePreset("2560x1080", "dlss", "off", "medium")
    assert GamePreset.from_dict(p.to_dict()) == p
    assert GamePreset.from_dict({"quality": "insane", "resolution": "big"}) == GamePreset()
    assert resolutions_offered([RDR2, CYBERPUNK]) == ["keep", "1280x720", "1920x1080", "2560x1080", "2560x1440",
                                                      "3840x2160"]


def test_frame_generation_on_and_off():
    offered = dict(CYBERPUNK, frame_gen=["Off", "DLSS"], dlss_frame_gen=[True, False], multi_frame_gen=["x2", "x3", "x4"])
    assert plan_preset(1091500, GamePreset(frame_gen="x2"), offered) == (
        {"frame_gen": "DLSS", "dlss_frame_gen": True, "multi_frame_gen": "x2"}, {})
    assert plan_preset(1091500, GamePreset(frame_gen="x4"), offered)[0]["multi_frame_gen"] == "x4"
    assert plan_preset(1091500, GamePreset(frame_gen="x3"), dict(offered, multi_frame_gen=["x2"])) ==         ({}, {"frame_gen": NOT_SEEN})                                    # a factor the file does not list
    assert plan_preset(1091500, GamePreset(frame_gen="off"), offered) == (
        {"frame_gen": "Off", "dlss_frame_gen": False}, {})               # the multi frame count is left alone
    assert plan_preset(1091500, GamePreset(frame_gen="x2"), dict(CYBERPUNK, frame_gen=["Off"])) == \
        ({}, {"frame_gen": NOT_SEEN})                                    # DLSS frame generation never seen
    assert plan_preset(1174180, GamePreset(frame_gen="x2"), RDR2) == ({}, {"frame_gen": NO_SETTING})
    assert describe(GamePreset(frame_gen="x4")) == "Frame generation On, 4x (multi frame)"
    assert GamePreset.from_dict(GamePreset(frame_gen="x3").to_dict()).frame_gen == "x3"
    assert GamePreset.from_dict({"frame_gen": "on"}).frame_gen == "x2"           # saved by the earlier version
