"""Tests for the Borderlands 3 settings adapter."""

from app.games.borderlands3 import (
    APP_ID,
    LAUNCH_ARGS,
    SECTION,
    SETTINGS,
    Change,
    IniData,
    apply,
    choices,
    dump,
    get_value,
    merge_indices,
    observed_indices,
    parse,
    plan,
    read_settings,
    set_value,
    verify,
)


def sample_text() -> str:
    return (
        "[ScalabilityGroups]\r\n"
        "sg.ResolutionQuality=100.000000\r\n"
        "sg.TextureQuality=3\r\n"
        "\r\n"
        "[/Script/OakGame.OakGameUserSettings]\r\n"
        "ResolutionScale=Scale_100\r\n"
        "FrameRateLimitMode=Unlimited\r\n"
        "GraphicsQualityMode=Ultra\r\n"
        "TextureStreamingQuality=Ultra\r\n"
        "AntiAliasingModeIndex=2\r\n"
        "LastGPUBenchmarkSteps=1507.529297\r\n"
        "LastGPUBenchmarkSteps=1147.025879\r\n"
        "bUseVSync=False\r\n"
        "bUseDynamicResolution=False\r\n"
        "ResolutionSizeX=2560\r\n"
        "ResolutionSizeY=1440\r\n"
        "LastUserConfirmedResolutionSizeX=2560\r\n"
        "LastUserConfirmedResolutionSizeY=1440\r\n"
        "FullscreenMode=1\r\n"
        "LastConfirmedFullscreenMode=1\r\n"
        "PreferredFullscreenMode=1\r\n"
        "PreferredGraphicsAPI=DX11\r\n"
        "\r\n"
        "[/Script/Engine.GameUserSettings]\r\n"
        "bUseDesiredScreenHeight=False\r\n"
    )


def test_module_constants() -> None:
    assert APP_ID == 397540
    assert LAUNCH_ARGS == []
    assert SECTION == "/Script/OakGame.OakGameUserSettings"
    assert list(SETTINGS) == [
        "resolution",
        "window_mode",
        "vsync",
        "fps_limit",
        "preset",
        "resolution_scale",
        "dynamic_resolution",
        "anti_aliasing",
        "texture_quality",
        "graphics_api",
    ]


def test_parse_dump_round_trip_crlf() -> None:
    text = "a=1\r\nb=2\r\n"
    data = parse(text)
    assert data.newline == "\r\n"
    assert data.final_newline is True
    assert data.lines == ["a=1", "b=2"]
    assert dump(data) == text


def test_parse_dump_round_trip_lf() -> None:
    text = "a=1\nb=2"
    data = parse(text)
    assert data.newline == "\n"
    assert data.final_newline is False
    assert data.lines == ["a=1", "b=2"]
    assert dump(data) == text


def test_parse_dump_round_trip_no_final_newline_crlf() -> None:
    text = "a=1\r\nb=2"
    data = parse(text)
    assert data.newline == "\r\n"
    assert data.final_newline is False
    assert data.lines == ["a=1", "b=2"]
    assert dump(data) == text


def test_get_value_first_duplicate_key() -> None:
    data = parse(
        "[S]\r\n"
        "k=first\r\n"
        "k=second\r\n"
    )
    assert get_value(data, "S", "k") == "first"


def test_get_value_ignores_same_key_in_other_section() -> None:
    data = parse(
        "[Other]\r\n"
        "k=other\r\n"
        "\r\n"
        "[Target]\r\n"
        "k=target\r\n"
    )
    assert get_value(data, "Target", "k") == "target"
    assert get_value(data, "Missing", "k") is None


def test_set_value_keeps_duplicates_and_leading_whitespace_and_input() -> None:
    text = (
        "[S]\r\n"
        "  k=old\r\n"
        "k=dup\r\n"
    )
    data = parse(text)
    original_lines = list(data.lines)

    updated = set_value(data, "S", "k", "new")
    assert updated is not None
    assert updated.lines == ["[S]", "  k=new", "k=dup"]
    assert data.lines == original_lines
    assert dump(updated) == "[S]\r\n  k=new\r\nk=dup\r\n"


def test_read_settings_example_values() -> None:
    data = parse(sample_text())
    settings = read_settings(data)

    assert settings["resolution"] == "2560x1440"
    assert settings["window_mode"] == "Borderless"
    assert settings["vsync"] is False
    assert settings["graphics_api"] == "DX11"
    assert settings["fps_limit"] == "Unlimited"
    assert settings["preset"] == "Ultra"
    assert settings["resolution_scale"] == "Scale_100"
    assert settings["dynamic_resolution"] is False
    assert settings["anti_aliasing"] == "2"
    assert settings["texture_quality"] == "Ultra"


def test_read_settings_unknown_map_text_absent() -> None:
    data = parse(
        "[/Script/OakGame.OakGameUserSettings]\r\n"
        "PreferredGraphicsAPI=DX9\r\n"
    )
    settings = read_settings(data)
    assert "graphics_api" not in settings


def test_choices_for_present_settings() -> None:
    data = parse(sample_text())
    known = {
        "fps_limit": {"Unlimited": 1, "60": 2},
        "resolution": {"2560x1440": 1, "1920x1080": 2},
    }

    result = choices(data, known)

    assert result["vsync"] == [True, False]
    assert result["window_mode"] == ["Fullscreen", "Borderless", "Windowed"]
    assert result["fps_limit"] == ["60", "Unlimited"]
    assert result["resolution"] == ["1920x1080", "2560x1440"]
    assert result["graphics_api"] == ["DX11", "DX12"]


def test_plan_noop_when_desired_matches_current() -> None:
    data = parse(sample_text())
    changes, refusals = plan(data, {"vsync": False, "window_mode": "Borderless"}, {})
    assert changes == []
    assert refusals == {}


def test_plan_bool_change() -> None:
    data = parse(sample_text())
    changes, refusals = plan(data, {"vsync": True}, {})
    assert refusals == {}
    assert changes == [Change("vsync", False, True, None)]


def test_plan_map_refusal_exact_text() -> None:
    data = parse(sample_text())
    changes, refusals = plan(data, {"window_mode": "Tabless"}, {})
    assert changes == []
    assert refusals == {"window_mode": "not one of: Fullscreen, Borderless, Windowed"}


def test_plan_learned_not_seen_exact_refusal() -> None:
    data = parse(sample_text())
    changes, refusals = plan(data, {"fps_limit": "144"}, {})
    assert changes == []
    assert refusals == {
        "fps_limit": (
            "not seen in this game yet; choose it once in the game's menu "
            "so the app can learn it"
        )
    }


def test_plan_learned_seen_accepted() -> None:
    data = parse(sample_text())
    known = {"fps_limit": {"Unlimited": 0, "60": 1}}
    changes, refusals = plan(data, {"fps_limit": "60"}, known)
    assert refusals == {}
    assert changes == [Change("fps_limit", "Unlimited", "60", None)]


def test_apply_resolution_writes_all_four_keys_and_verify_passes() -> None:
    data = parse(sample_text())
    known = {"resolution": {"2560x1440": 0, "1920x1080": 1}}
    changes, refusals = plan(data, {"resolution": "1920x1080"}, known)
    assert refusals == {}

    updated = apply(data, changes)
    assert get_value(updated, SECTION, "ResolutionSizeX") == "1920"
    assert get_value(updated, SECTION, "ResolutionSizeY") == "1080"
    assert get_value(updated, SECTION, "LastUserConfirmedResolutionSizeX") == "1920"
    assert get_value(updated, SECTION, "LastUserConfirmedResolutionSizeY") == "1080"

    assert verify(updated, {"resolution": "1920x1080"}) == {}


def test_plan_bad_resolution_text_refused() -> None:
    data = parse(sample_text())
    changes, refusals = plan(data, {"resolution": "1920-1080"}, {})
    assert changes == []
    assert refusals == {"resolution": "expects WIDTHxHEIGHT"}


def test_plan_unknown_key_refused() -> None:
    data = parse(sample_text())
    changes, refusals = plan(data, {"brightness": 100}, {})
    assert changes == []
    assert refusals == {"brightness": "unknown setting"}


def test_verify_reports_expected_and_none_when_absent() -> None:
    data = parse(
        "[/Script/OakGame.OakGameUserSettings]\r\n"
        "bUseVSync=False\r\n"
    )
    assert verify(data, {"graphics_api": "DX11"}) == {"graphics_api": ("DX11", None)}


def test_observed_indices_and_merge_indices() -> None:
    data = parse(sample_text())
    observed = observed_indices(data)

    assert observed["fps_limit"] == {"Unlimited": 0}
    assert observed["resolution"] == {"2560x1440": 0}

    known = {"fps_limit": {"Unlimited": 9}}
    merged = merge_indices(known, observed)
    assert merged["fps_limit"] == {"Unlimited": 0} and merged["resolution"] == {"2560x1440": 0}
    assert known == {"fps_limit": {"Unlimited": 9}}


def test_apply_bool_and_map_write_all_keys() -> None:
    data = parse(sample_text())
    changes = [
        Change("vsync", False, True, None),
        Change("window_mode", "Borderless", "Windowed", None),
    ]

    updated = apply(data, changes)

    assert get_value(updated, SECTION, "bUseVSync") == "True"
    assert get_value(updated, SECTION, "FullscreenMode") == "2"
    assert get_value(updated, SECTION, "LastConfirmedFullscreenMode") == "2"
    assert get_value(updated, SECTION, "PreferredFullscreenMode") == "2"
    assert verify(updated, {"vsync": True, "window_mode": "Windowed"}) == {}
