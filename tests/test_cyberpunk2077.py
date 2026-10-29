"""Tests for app/games/cyberpunk2077.py."""

import copy

from app.games.cyberpunk2077 import (
    Change,
    apply,
    find_option,
    merge_indices,
    observed_indices,
    plan,
    read_settings,
    verify,
)


def _example_data():
    return {
        "version": 140,
        "data": [
            {
                "group_name": "/graphics/presets",
                "options": [
                    {"name": "QuickPresets", "type": "string_list", "is_dynamic": True, "value": "Custom", "index": 0},
                    {"name": "ResolutionScaling", "type": "string_list", "is_dynamic": True, "value": "DLSS", "index": 1},
                    {"name": "DLSS", "type": "string_list", "is_dynamic": True, "value": "Balanced", "index": 3},
                    {"name": "FrameGeneration", "type": "string_list", "is_dynamic": True, "value": "DLSS", "index": 1},
                    {"name": "DLSSFrameGen", "type": "bool", "value": True, "default_value": False},
                    {
                        "name": "DLSS_MultiFrameGeneration",
                        "type": "string_list",
                        "is_dynamic": False,
                        "value": "x4",
                        "index": 2,
                        "default_index": 0,
                        "values": ["x2", "x3", "x4"],
                    },
                    {"name": "DynamicResolutionScaling", "type": "bool", "value": False, "default_value": False},
                ],
            },
            {
                "group_name": "/graphics/raytracing",
                "options": [
                    {"name": "RayTracing", "type": "bool", "value": True, "default_value": False},
                    {
                        "name": "RayTracedLighting",
                        "type": "string_list",
                        "is_dynamic": False,
                        "value": "Psycho",
                        "index": 3,
                        "default_index": 0,
                        "values": ["Off", "Medium", "Ultra", "Psycho"],
                    },
                    {"name": "RayTracedPathTracing", "type": "bool", "value": False, "default_value": False},
                ],
            },
            {
                "group_name": "/video/display",
                "options": [
                    {
                        "name": "VSync",
                        "type": "string_list",
                        "is_dynamic": True,
                        "value": "UI-Settings-Video-QualitySetting-Off",
                        "index": 0,
                    },
                    {"name": "MaximumFPS_OnOff", "type": "bool", "value": False, "default_value": False},
                    {
                        "name": "MaximumFPS_Value",
                        "type": "int",
                        "value": 30,
                        "default_value": 30,
                        "min_value": 10,
                        "max_value": 240,
                    },
                    {"name": "WindowMode", "type": "string_list", "is_dynamic": True, "value": "BorderlessWindowed", "index": 1},
                    {"name": "Resolution", "type": "string_list", "is_dynamic": True, "value": "2560x1440", "index": 0},
                ],
            },
        ],
    }


def test_find_option_returns_same_object():
    data = _example_data()
    option = find_option(data, "/video/display", "Resolution")
    assert option is data["data"][2]["options"][4]


def test_read_settings_on_example():
    data = _example_data()
    assert read_settings(data) == {
        "resolution": "2560x1440",
        "window_mode": "BorderlessWindowed",
        "vsync": "UI-Settings-Video-QualitySetting-Off",
        "fps_limit": False,
        "preset": "Custom",
        "upscaler": "DLSS Balanced",
        "dlss_mode": "Balanced",
        "dynamic_resolution": False,
        "frame_gen": "Custom",                   # no FSR frame generation switch in this file
        "dlss_frame_gen": True,
        "multi_frame_gen": "x4",
        "ray_tracing": "Custom",                 # ray tracing on, but not as one of the game's levels
        "rt_lighting": "Psycho",
        "path_tracing": False,
    }


def test_read_settings_missing_option_absent():
    data = {
        "version": 140,
        "data": [
            {
                "group_name": "/graphics/presets",
                "options": [
                    {"name": "QuickPresets", "type": "string_list", "is_dynamic": True, "value": "Custom", "index": 0},
                ],
            }
        ],
    }
    result = read_settings(data)
    assert "preset" in result
    assert "resolution" not in result
    assert "rt_lighting" not in result


def test_observed_indices_lists_dynamic_but_not_static():
    data = _example_data()
    assert observed_indices(data) == {
        "resolution": {"2560x1440": 0},
        "window_mode": {"BorderlessWindowed": 1},
        "vsync": {"UI-Settings-Video-QualitySetting-Off": 0},
        "preset": {"Custom": 0},
        "dlss_mode": {"Balanced": 3},
    }


def test_merge_indices_seen_wins_and_inputs_untouched():
    known = {
        "dlss_mode": {"Balanced": 3, "Quality": 1},
        "resolution": {"2560x1440": 0},
    }
    seen = {
        "dlss_mode": {"Quality": 2, "Performance": 0},
        "window_mode": {"Fullscreen": 2},
    }

    known_before = copy.deepcopy(known)
    seen_before = copy.deepcopy(seen)

    merged = merge_indices(known, seen)

    assert merged == {
        "dlss_mode": {"Balanced": 3, "Quality": 2, "Performance": 0},
        "resolution": {"2560x1440": 0},
        "window_mode": {"Fullscreen": 2},
    }
    assert known == known_before
    assert seen == seen_before


def test_plan_no_differences():
    data = _example_data()
    desired = read_settings(data)
    known = observed_indices(data)

    changes, refused = plan(data, desired, known)

    assert changes == []
    assert refused == {}


def test_plan_bool_change():
    data = {
        "version": 140,
        "data": [
            {
                "group_name": "/graphics/presets",
                "options": [
                    {"name": "DLSSFrameGen", "type": "bool", "value": False, "default_value": False},
                ],
            }
        ],
    }

    changes, refused = plan(data, {"dlss_frame_gen": True}, {})

    assert changes == [Change("dlss_frame_gen", False, True, None)]
    assert refused == {}


def test_plan_bool_refusal_for_yes():
    data = {
        "version": 140,
        "data": [
            {
                "group_name": "/graphics/presets",
                "options": [
                    {"name": "DLSSFrameGen", "type": "bool", "value": False, "default_value": False},
                ],
            }
        ],
    }

    changes, refused = plan(data, {"dlss_frame_gen": "yes"}, {})

    assert changes == []
    assert refused == {"dlss_frame_gen": "expects true or false"}


def test_plan_int_out_of_range_refusal():
    data = {
        "version": 140,
        "data": [
            {
                "group_name": "/video/display",
                "options": [
                    {
                        "name": "MaximumFPS_OnOff",
                        "type": "int",
                        "value": 30,
                        "default_value": 30,
                        "min_value": 10,
                        "max_value": 240,
                    },
                ],
            }
        ],
    }

    changes, refused = plan(data, {"fps_limit": 250}, {})

    assert changes == []
    assert refused == {"fps_limit": "must be between 10 and 240"}


def test_plan_static_list_change_with_index():
    data = {
        "version": 140,
        "data": [
            {
                "group_name": "/graphics/raytracing",
                "options": [
                    {
                        "name": "RayTracedLighting",
                        "type": "string_list",
                        "is_dynamic": False,
                        "value": "Psycho",
                        "index": 3,
                        "default_index": 0,
                        "values": ["Off", "Medium", "Ultra", "Psycho"],
                    },
                ],
            }
        ],
    }

    changes, refused = plan(data, {"rt_lighting": "Ultra"}, {})

    assert changes == [Change("rt_lighting", "Psycho", "Ultra", 2)]
    assert refused == {}


def test_plan_static_list_refusal_text_exact():
    data = {
        "version": 140,
        "data": [
            {
                "group_name": "/graphics/raytracing",
                "options": [
                    {
                        "name": "RayTracedLighting",
                        "type": "string_list",
                        "is_dynamic": False,
                        "value": "Psycho",
                        "index": 3,
                        "default_index": 0,
                        "values": ["Off", "Medium", "Ultra", "Psycho"],
                    },
                ],
            }
        ],
    }

    changes, refused = plan(data, {"rt_lighting": "Nope"}, {})

    assert changes == []
    assert refused == {"rt_lighting": "not one of: Off, Medium, Ultra, Psycho"}


def test_plan_dynamic_list_known_index():
    data = {
        "version": 140,
        "data": [
            {
                "group_name": "/graphics/presets",
                "options": [
                    {"name": "DLSS", "type": "string_list", "is_dynamic": True, "value": "Balanced", "index": 3},
                ],
            }
        ],
    }

    changes, refused = plan(data, {"dlss_mode": "Quality"}, {"dlss_mode": {"Quality": 2}})

    assert changes == [Change("dlss_mode", "Balanced", "Quality", 2)]
    assert refused == {}


def test_plan_dynamic_list_unknown_refusal_text_exact():
    data = {
        "version": 140,
        "data": [
            {
                "group_name": "/graphics/presets",
                "options": [
                    {"name": "DLSS", "type": "string_list", "is_dynamic": True, "value": "Balanced", "index": 3},
                ],
            }
        ],
    }

    changes, refused = plan(data, {"dlss_mode": "Quality"}, {})

    assert changes == []
    assert refused == {
        "dlss_mode": (
            "the game's position for this value is unknown; "
            "choose it once in the game's menu so the app can learn it"
        )
    }


def test_plan_unknown_key():
    data = _example_data()

    changes, refused = plan(data, {"bogus": 1}, {})

    assert changes == []
    assert refused == {"bogus": "unknown setting"}


def test_apply_does_not_mutate_input_and_sets_index():
    data = {
        "version": 140,
        "data": [
            {
                "group_name": "/graphics/raytracing",
                "options": [
                    {
                        "name": "RayTracedLighting",
                        "type": "string_list",
                        "is_dynamic": False,
                        "value": "Psycho",
                        "index": 3,
                        "default_index": 0,
                        "values": ["Off", "Medium", "Ultra", "Psycho"],
                    },
                ],
            }
        ],
    }

    original = copy.deepcopy(data)
    changes = [Change("rt_lighting", "Psycho", "Ultra", 2)]

    out = apply(data, changes)

    assert data == original
    option = find_option(out, "/graphics/raytracing", "RayTracedLighting")
    assert option is not None
    assert option["value"] == "Ultra"
    assert option["index"] == 2


def test_apply_without_index_keeps_old_index():
    data = {
        "version": 140,
        "data": [
            {
                "group_name": "/graphics/raytracing",
                "options": [
                    {
                        "name": "RayTracedLighting",
                        "type": "string_list",
                        "is_dynamic": False,
                        "value": "Psycho",
                        "index": 3,
                        "default_index": 0,
                        "values": ["Off", "Medium", "Ultra", "Psycho"],
                    },
                ],
            }
        ],
    }

    changes = [Change("rt_lighting", "Psycho", "Ultra", None)]
    out = apply(data, changes)

    option = find_option(out, "/graphics/raytracing", "RayTracedLighting")
    assert option is not None
    assert option["value"] == "Ultra"
    assert option["index"] == 3


def test_verify_empty_after_apply():
    data = {
        "version": 140,
        "data": [
            {
                "group_name": "/graphics/raytracing",
                "options": [
                    {
                        "name": "RayTracedLighting",
                        "type": "string_list",
                        "is_dynamic": False,
                        "value": "Psycho",
                        "index": 3,
                        "default_index": 0,
                        "values": ["Off", "Medium", "Ultra", "Psycho"],
                    },
                ],
            }
        ],
    }

    desired = {"rt_lighting": "Ultra"}
    changes, refused = plan(data, desired, {})
    assert refused == {}

    out = apply(data, changes)
    assert verify(out, desired) == {}


def test_verify_reports_missing_option():
    data = {"version": 140, "data": []}
    desired = {"resolution": "1920x1080"}

    assert verify(data, desired) == {"resolution": ("1920x1080", None)}


def test_verify_treats_true_vs_one_as_mismatch():
    data = {
        "version": 140,
        "data": [
            {
                "group_name": "/graphics/presets",
                "options": [
                    {"name": "DLSSFrameGen", "type": "bool", "value": 1, "default_value": False},
                ],
            }
        ],
    }

    desired = {"dlss_frame_gen": True}
    assert verify(data, desired) == {"dlss_frame_gen": (True, 1)}


def test_quick_preset_is_written_setting_by_setting():
    from app.games import cyberpunk2077 as cp
    groups = {}
    for (g, n), v in zip(cp.PRESET_OPTIONS, cp.PRESETS["Ultra"]):
        opt = ({"name": n, "type": "bool", "value": v} if isinstance(v, bool) else
               {"name": n, "type": "int_list", "value": v, "index": 3, "values": [2, 4, 8, 16]} if n == "Anisotropy" else
               {"name": n, "type": "string_list", "value": v, "index": 0, "values": ["Off", "Low", "Medium", "High", "Ultra"]})
        groups.setdefault(g, []).append(opt)
    groups["/graphics/presets"].append({"name": "QuickPresets", "type": "string_list", "value": "Ultra", "index": 4})
    data = {"data": [{"group_name": g, "options": o} for g, o in groups.items()]}
    assert cp.read_settings(data)["preset"] == "Ultra" and cp.choices(data, {})["preset"][:4] == list(cp.PRESETS)
    changes, refused = cp.plan(data, {"preset": "Low"}, {})
    assert refused == {} and [(c.key, c.new) for c in changes] == [("preset", "Low")]
    out = cp.apply(data, changes)
    assert cp.verify(out, {"preset": "Low"}) == {}
    assert cp.find_option(out, "/graphics/advanced", "Anisotropy") == {"name": "Anisotropy", "type": "int_list",
                                                                       "value": 4, "index": 1, "values": [2, 4, 8, 16]}
    assert (cp.find_option(out, "/graphics/presets", "QuickPresets")["value"],
            cp.find_option(out, "/graphics/presets", "QuickPresets")["index"]) == cp.CUSTOM
    assert cp.plan(out, {"preset": "Low"}, {}) == ([], {})                       # already Low: nothing written
    del groups["/graphics/basic"][0]                                             # FilmGrain missing
    assert cp.plan(data, {"preset": "High"}, {})[1] == {"preset": "not in the settings file: FilmGrain"}


def test_upscaler_with_its_mode_and_ray_tracing_levels():
    from app.games import cyberpunk2077 as cp
    dyn = {"type": "string_list", "is_dynamic": True}
    data = {"data": [
        {"group_name": "/graphics/presets", "options": [
            dict(dyn, name="QuickPresets", value="Ray Tracing Ultra", index=8),
            dict(dyn, name="ResolutionScaling", value="DLSS", index=1),
            dict(dyn, name="DLSS", value="Auto", index=0), dict(dyn, name="FSR2", value="Auto", index=0),
            dict(dyn, name="XESS", value="Auto", index=0)]},
        {"group_name": "/graphics/raytracing", "options": [
            {"name": n, "type": "bool", "value": True} for n in ("RayTracing", "RayTracedReflections",
                                                                 "RayTracedSunShadows", "RayTracedLocalShadows",
                                                                 "RayTracedPathTracing")] + [
            {"name": "RayTracedLighting", "type": "string_list", "value": "Ultra", "index": 2,
             "values": ["Off", "Medium", "Ultra", "Psycho"]}]}]}
    assert cp.read_settings(data)["upscaler"] == "Custom" and cp.read_settings(data)["ray_tracing"] == "Custom"
    want = {"upscaler": "Off", "ray_tracing": "Ultra"}            # ray tracing without an upscaler (owner)
    out = cp.apply(data, cp.plan(data, want, {})[0])
    assert cp.verify(out, want) == {}
    assert cp.find_option(out, "/graphics/raytracing", "RayTracedPathTracing")["value"] is False
    assert cp.find_option(out, "/graphics/presets", "QuickPresets")["value"] == "Custom"
    out = cp.apply(out, cp.plan(out, {"upscaler": "FSR Performance", "ray_tracing": "Low"}, {})[0])
    assert [(cp.find_option(out, "/graphics/presets", n)["value"], cp.find_option(out, "/graphics/presets", n)["index"])
            for n in ("ResolutionScaling", "FSR2", "DLSS")] == [("FSR2", 2), ("Performance", 3), ("Auto", 0)]
    assert cp.read_settings(out)["ray_tracing"] == "Low"
    assert cp.plan(out, {"upscaler": "FSR Ultra Performance"}, {})[1]["upscaler"].startswith("not one of: Off, DLSS")


def test_frame_generation_follows_the_upscaler():
    from app.games import cyberpunk2077 as cp
    dyn = {"type": "string_list", "is_dynamic": True}
    data = {"data": [{"group_name": "/graphics/presets", "options": [   # as the game wrote it with FSR's (owner)
        dict(dyn, name="QuickPresets", value="Custom", index=0), dict(dyn, name="FrameGeneration", value="FSR3", index=2),
        {"name": "DLSSFrameGen", "type": "bool", "value": False}, {"name": "FSR3_FrameGeneration", "type": "bool", "value": True},
        dict(dyn, name="DLSS_MultiFrameGeneration", value="", index=-1),
        dict(dyn, name="DLSS_DynamicMultiFrameGeneration_TargetFPS", value="", index=-1)]}]}
    assert cp.read_settings(data)["frame_gen"] == "FSR x2"
    out = cp.apply(data, cp.plan(data, {"frame_gen": "DLSS x3"}, {})[0])
    assert [(o["name"], o["value"], o.get("index")) for o in out["data"][0]["options"][1:]] == [
        ("FrameGeneration", "DLSS", 1), ("DLSSFrameGen", True, None), ("FSR3_FrameGeneration", False, None),
        ("DLSS_MultiFrameGeneration", "x3", 2), ("DLSS_DynamicMultiFrameGeneration_TargetFPS", cp.AUTO, 0)]
    out = cp.apply(out, cp.plan(out, {"frame_gen": "Off"}, {})[0])
    assert cp.read_settings(out)["frame_gen"] == "Off" and cp.verify(out, {"frame_gen": "Off"}) == {}
