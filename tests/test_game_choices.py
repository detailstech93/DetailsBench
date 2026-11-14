"""Games: which upscaler / frame generation the card can run, and the recommended one (owner, 2026-10-20)."""

from app.games import presets as p


def test_main_gpu_prefers_the_discrete_card():
    assert p.main_gpu(["Intel(R) UHD Graphics 770", "NVIDIA GeForce RTX 4070"]) == "NVIDIA GeForce RTX 4070"
    assert p.main_gpu(["AMD Radeon(TM) Graphics", "AMD Radeon RX 7900 XTX"]) == "AMD Radeon RX 7900 XTX"
    assert p.main_gpu(["Intel(R) Arc(TM) A770 Graphics"]) == "Intel(R) Arc(TM) A770 Graphics"
    assert p.main_gpu([]) == ""


def test_what_each_card_is_offered():
    rtx50, rtx40, rtx30, gtx = ("NVIDIA GeForce RTX 5090", "NVIDIA GeForce RTX 4070 Laptop GPU",
                                "NVIDIA GeForce RTX 3080", "NVIDIA GeForce GTX 1660 SUPER")
    amd, arc = "AMD Radeon RX 7900 XTX", "Intel(R) Arc(TM) B580 Graphics"
    assert p.upscalers_for(rtx40) == ["dlss", "fsr", "xess"] and p.recommended_upscaler(rtx40) == "dlss"
    assert p.upscalers_for(gtx) == ["fsr", "xess"] and p.recommended_upscaler(gtx) == "fsr"
    assert p.upscalers_for(amd) == ["fsr", "xess"] and p.recommended_upscaler(amd) == "fsr"   # no DLSS on AMD
    assert p.upscalers_for(arc) == ["fsr", "xess"] and p.recommended_upscaler(arc) == "xess"
    assert p.frame_gens_for(rtx50, "dlss") == ["x2", "x3", "x4"] and p.frame_gens_for(rtx40, "dlss") == ["x2"]
    assert p.frame_gens_for(rtx30, "dlss") == [] and p.frame_gens_for(rtx30, "fsr") == ["x2"]
    assert p.frame_gens_for(amd, "xess") == [] and p.frame_gens_for(arc, "xess") == ["x2"]
    assert p.choice_labels(["dlss", "fsr"], "dlss") == [("dlss", "DLSS (recommended)"), ("fsr", "FSR")]


def test_modes_become_a_preset():
    raw = p.game_preset("raw", "dlss", "x2", "high", "2560x1440", "on")
    assert (raw.upscaler, raw.frame_gen, raw.ray_tracing, raw.quality) == ("off", "off", "on", "high")
    up = p.game_preset("upscaling", "fsr", "x2", "ultra", "3840x2160", "off")
    assert (up.upscaler, up.frame_gen) == ("fsr", "off") and p.mode_of(up) == "upscaling"
    fg = p.game_preset("upscaling_fg", "dlss", "x4", "low", "1920x1080", "off")
    assert (fg.upscaler, fg.frame_gen) == ("dlss", "x4") and p.mode_of(fg) == "upscaling_fg"
    assert p.mode_of(raw) == "raw"
    # raw is without ray tracing unless it is chosen (owner, 2026-11-14), from the dialog and from the phone page
    assert p.game_preset("raw", "off", "off", "ultra", p.KEEP, p.KEEP).ray_tracing == "off"
    assert p.game_preset("upscaling", "dlss", "off", "ultra", p.KEEP, p.KEEP).ray_tracing == p.KEEP
    assert p.from_page({"mode": "raw", "quality": "ultra"}).ray_tracing == "off"
    assert p.from_page({"mode": "raw", "ray_tracing": "on"}).ray_tracing == "on"


def test_a_forced_preset_sets_the_games_own_full_resolution_scale():
    # BL3 kept the owner's 175% and drew 3360x1890 for a 1080p run (owner's laptop, 2026-11-14)
    bl3 = {"preset": ["Low", "Ultra"], "resolution_scale": ["Scale_175", "Scale_100"]}
    assert p.plan_preset("397540", p.GamePreset(quality="ultra"), bl3)[0] == {"preset": "Ultra",
                                                                              "resolution_scale": "Scale_100"}
    assert p.plan_preset("397540", p.GamePreset(), bl3)[0] == {}                 # nothing forced: nothing touched
    assert p.plan_preset("397540", p.GamePreset(quality="ultra"),
                         {"preset": ["Ultra"], "resolution_scale": ["Scale_175"]})[0] == {"preset": "Ultra"}
    fc6 = {"preset": ["Ultra"], "resolution_scale": ["1"]}
    assert p.plan_preset("2369390", p.GamePreset(quality="ultra"), fc6)[0]["resolution_scale"] == "1"


def test_a_game_without_the_asked_upscaler_is_left_out():
    # owner, 2026-11-14: upscaling asked and a game that cannot be set to it: left out of the run, not run without
    from app.ui.run_tests import Prepared, RunChoice, lacking_upscaler
    fc6 = {"preset": ["Ultra"], "upscaler": ["Off", "FSR 1.0"]}
    games = [("2369390", "Far Cry 6"), ("397540", "Borderlands 3")]
    offered = {"2369390": (fc6, None), "397540": ({"preset": ["Ultra"]}, None)}

    def run(**preset):
        return lacking_upscaler(Prepared([], [], RunChoice(3, 1, 0, p.GamePreset(**preset)), games, offered))
    dropped = run(upscaler="dlss", quality="ultra")
    assert set(dropped) == {"2369390", "397540"} and "no DLSS" in dropped["2369390"]
    assert set(run(upscaler="fsr")) == {"397540"}           # Far Cry 6 has FSR; Borderlands 3 has no upscaler
    assert run(upscaler="off", quality="ultra") == {} and run(quality="ultra") == {}
