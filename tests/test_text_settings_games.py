from app.games import red_dead_redemption2 as rdr2
from app.games import witcher3 as w3
from app.games.settings_file import ForcedSettings
from app.games.text_settings import NOT_SEEN

RDR2 = ('<?xml version="1.0" encoding="UTF-8"?>\r\n\r\n<rage__fwuiSystemSettingsCollection>\r\n'
        '  <graphics>\r\n    <textureQuality>kSettingLevel_Ultra</textureQuality>\r\n'
        '    <fxaaEnabled value="false" />\r\n    <dlssIndex value="0" />\r\n  </graphics>\r\n'
        '  <advancedGraphics>\r\n    <API>kSettingAPI_Vulkan</API>\r\n    <motionBlur value="true" />\r\n'
        '  </advancedGraphics>\r\n  <video>\r\n    <screenWidth value="2560" />\r\n'
        '    <screenHeight value="1440" />\r\n    <screenWidthWindowed value="2560" />\r\n'
        '    <vSync value="1" />\r\n  </video>\r\n</rage__fwuiSystemSettingsCollection>')
W3 = ('[Viewport]\r\nVSync=true\r\nResolution="2560x1440"\r\nFullScreenMode=1\r\n'
      '[Rendering/RT]\r\nEnableRT=true\r\n[Rendering/RT/PathTracer]\r\nPTEnable=false\r\n'
      '[PostProcess]\r\nDLSSGMode=0\r\n')


def test_rdr2_read_and_plan():
    s = rdr2.read_settings(RDR2)
    assert s == {"resolution": "2560x1440", "vsync": "1", "graphics_api": "Vulkan", "texture_quality": "Ultra",
                 "fxaa": False, "dlss": "0", "motion_blur": True}
    changes, refused = rdr2.plan(RDR2, {"texture_quality": "Low", "graphics_api": "DX12", "vsync": "0",
                                        "fxaa": "on", "ssao": "x", "msaa": "2"}, {})
    assert [c.key for c in changes] == ["texture_quality", "graphics_api"]
    assert refused == {"vsync": NOT_SEEN, "fxaa": "not one of: True, False", "ssao": "unknown setting",
                       "msaa": "not in the settings file"}


def test_rdr2_apply_changes_only_those_characters():
    known = {"resolution": {"1920x1080": 0}}
    want = {"texture_quality": "Low", "fxaa": True, "resolution": "1920x1080"}
    out = rdr2.apply(RDR2, rdr2.plan(RDR2, want, known)[0])
    assert rdr2.verify(out, want) == {}
    assert out == (RDR2.replace("kSettingLevel_Ultra", "kSettingLevel_Low")
                   .replace('fxaaEnabled value="false"', 'fxaaEnabled value="true"')
                   .replace('screenWidth value="2560"', 'screenWidth value="1920"')
                   .replace('screenHeight value="1440"', 'screenHeight value="1080"'))
    assert 'screenWidthWindowed value="2560"' in out


def test_rdr2_resolution_goes_to_exclusive_full_screen():
    known = {"resolution": {"1920x1080": 0}, "window_mode": {"0": 0, "2": 0}}
    borderless = RDR2.replace('<vSync value="1" />', '<vSync value="1" />\r\n    <windowed value="2" />')
    changes, refused = rdr2.plan(borderless, {"resolution": "1920x1080", "texture_quality": "Low"}, known)
    assert [(c.key, c.new) for c in changes] == [("resolution", "1920x1080"), ("texture_quality", "Low"),
                                                ("window_mode", "0")] and refused == {}
    changes, refused = rdr2.plan(borderless, {"resolution": "1920x1080", "window_mode": "2"}, known)
    assert refused["resolution"].startswith("windowed or borderless mode (video/windowed 2)")
    assert [c.key for c in changes] == []                   # window mode 2 is what the file has already


def test_rdr2_any_screen_resolution_at_the_screen_s_refresh_rate(monkeypatch):
    from app.games import screen_modes
    monkeypatch.setattr(screen_modes, "refresh_rates", lambda: {(1920, 1080): 60, (1280, 720): 75})
    data = RDR2.replace('<vSync value="1" />', '<vSync value="1" />\r\n    <windowed value="2" />\r\n'
                        '    <refreshRateNumerator value="32" />')
    assert rdr2.choices(data, {})["resolution"] == ["1280x720", "1920x1080", "2560x1440"]
    changes, refused = rdr2.plan(data, {"resolution": "1280x720"}, {})   # never seen in the game: still written
    assert refused == {} and [(c.key, c.new) for c in changes] == [
        ("resolution", "1280x720"), ("window_mode", "0"), ("refresh_rate", "75")]
    out = rdr2.apply(data, changes)
    assert all(s in out for s in ('screenWidth value="1280"', 'windowed value="0"', 'refreshRateNumerator value="75"'))
    assert rdr2.plan(data, {"resolution": "3840x2160"}, {})[1] == {"resolution": NOT_SEEN}


def test_witcher3_quotes_are_kept_and_bytes_are_exact():
    data = w3.parse(W3)
    assert w3.dump(data) == W3
    assert w3.read_settings(data) == {"resolution": "2560x1440", "window_mode": "1", "vsync": True,
                                      "dlss_frame_gen": "0", "ray_tracing": True, "path_tracing": False}
    known = {"resolution": {"1920x1080": 0}}
    changes, refused = w3.plan(data, {"resolution": "1920x1080", "ray_tracing": False, "window_mode": "2"}, known)
    assert refused == {"window_mode": NOT_SEEN}
    out = w3.dump(w3.apply(data, changes))
    assert out == W3.replace('"2560x1440"', '"1920x1080"').replace("EnableRT=true", "EnableRT=false")


def test_witcher3_forced_flow_restores_the_file(tmp_path):
    f = tmp_path / "docs" / "The Witcher 3" / "dx12user.settings"
    f.parent.mkdir(parents=True)
    f.write_bytes(W3.encode("utf-8"))
    fs = ForcedSettings("292030", {"ray_tracing": False, "vsync": False}, tmp_path / "run", tmp_path / "data",
                        env={"PCBM_DOCUMENTS": str(tmp_path / "docs")})
    rec = fs.apply()
    assert rec.refused == {} and rec.mismatch_before_launch == {}
    assert b"EnableRT=false" in f.read_bytes() and b"VSync=false" in f.read_bytes()
    rec = fs.finish()
    assert rec.verified and rec.restored is True and f.read_bytes() == W3.encode("utf-8")
