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
