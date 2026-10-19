"""The PC's conditions before a run (power, power plan, background load): all read through fakes."""

from types import SimpleNamespace

from app.environment import conditions as c
from app.manifest import build_manifest
from app.report import _conditions_section

BALANCED = "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)\n"
SAVER = "Power Scheme GUID: a1841308-3541-4fab-bc81-f71556f20b4a  (Power saver)\n"


def test_battery_power_plan_and_load_items():
    assert c.battery_item(lambda: None) is None                                   # desktop: nothing to say
    assert c.battery_item(lambda: SimpleNamespace(power_plugged=True, percent=80))["ok"]
    on_battery = c.battery_item(lambda: SimpleNamespace(power_plugged=False, percent=55.4))
    assert not on_battery["ok"] and on_battery["value"] == "on battery (55%)"
    assert c.windows_plan_item(lambda: BALANCED) == c._item("Power plan", "Balanced", True)
    saver = c.windows_plan_item(lambda: SAVER)
    assert not saver["ok"] and "Balanced or higher" in saver["advice"]
    assert c.windows_plan_item(lambda: "garbage") is None
    files = {"/sys/firmware/acpi/platform_profile": "low-power",
             "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor": "powersave"}
    linux = c.linux_plan_item(lambda p: files[p])
    assert not linux["ok"] and linux["value"] == "platform profile low-power, CPU governor powersave"
    assert c.linux_plan_item(lambda p: (_ for _ in ()).throw(OSError())) is None
    assert c.load_item(lambda: (4.0, []))["ok"]
    busy = c.load_item(lambda: (37.0, ["MsMpEng.exe", "OneDrive.exe"]))
    assert not busy["ok"] and busy["value"] == "37% CPU in use (busiest: MsMpEng.exe, OneDrive.exe)"
    assert c.load_item(lambda: 1 / 0) is None                                    # unreadable: left out


def test_check_text_manifest_and_report():
    items = c.check("win32", battery=lambda: None, plan=lambda: SAVER, sample=lambda: (3.0, []))
    assert [i["name"] for i in items] == ["Power plan", "Background load"]
    text = c.text(items)
    assert text.startswith("PC right now (scores may come out lower") and "CHECK  Power plan: Power saver ->" in text
    assert c.text([items[1]]).startswith("PC right now:\n  OK  Background load")
    assert c.check("linux", battery=lambda: None, plan=lambda p: "balanced", sample=lambda: (3.0, []))[0]["ok"]
    manifest = build_manifest({"id": 1}, conditions={"read_at": "2026-10-19 12:00:00", "items": items})
    html = _conditions_section({"manifest": manifest})
    assert "<h2>PC before the run</h2>" in html and "Power saver" in html and "may have made" in html
    assert "Check: Power saver lowers" in html
    assert build_manifest({"id": 1})["conditions"] is None and _conditions_section({"manifest": {}}) == ""
