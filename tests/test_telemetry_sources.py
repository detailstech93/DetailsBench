"""Telemetry sources with fakes only: no nvidia-smi run, no network, no real sensors."""

import io
import json
from types import SimpleNamespace

from app.telemetry.sources import (
    LhmSource, NvidiaSmiSource, PsutilSource, default_sources, is_loopback_url, lhm_cpu_reading,
)
from app.telemetry.lhm import parse_lhm_data

REAL_LINE = "0, NVIDIA GeForce RTX 5090 Laptop GPU, 43, 240, 405, 10.50, 0\n"


def smi_runner(stdout, code=0, calls=None):
    def run(argv, **kw):
        if calls is not None:
            calls.append(argv)
        return SimpleNamespace(returncode=code, stdout=stdout, stderr="")
    return run


def test_nvidia_smi_reading():
    calls = []
    src = NvidiaSmiSource("nvidia-smi.exe", smi_runner(REAL_LINE, calls=calls))
    assert src.available() == (True, "1 NVIDIA GPU(s)")
    assert src.read() == {"gpu0_temperature": (43.0, "C"), "gpu0_sm_clock": (240.0, "MHz"),
                          "gpu0_mem_clock": (405.0, "MHz"), "gpu0_power": (10.5, "W"), "gpu0_load": (0.0, "%")}
    assert calls[0][0] == "nvidia-smi.exe" and calls[0][1].startswith("--query-gpu=")


def test_nvidia_smi_missing_values_are_absent_not_zero():
    src = NvidiaSmiSource("x", smi_runner("0, GPU, 50, [N/A], 405, [N/A], 3\n"))
    reading = src.read()
    assert "gpu0_power" not in reading and "gpu0_sm_clock" not in reading and reading["gpu0_temperature"] == (50.0, "C")


def test_nvidia_smi_unavailable_reasons():
    assert NvidiaSmiSource("", smi_runner("")).available() == (False, "nvidia-smi not found (no NVIDIA driver)")
    failed = "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.\n"
    ok, reason = NvidiaSmiSource("x", smi_runner(failed, code=9)).available()
    assert not ok and "NVIDIA-SMI has failed" in reason

    def boom(argv, **kw):
        raise OSError("not a valid application")
    assert NvidiaSmiSource("x", boom).available() == (False, "nvidia-smi failed: not a valid application")


def sensor(sid, typ, name, value):
    return {"id": 0, "Text": name, "SensorId": sid, "Type": typ, "Value": "", "RawValue": value}


TREE = {"id": 0, "Text": "Sensor", "Children": [{"id": 1, "Text": "PC", "Children": [
    {"id": 2, "Text": "AMD Ryzen 9", "HardwareId": "/amdcpu/0", "Children": [
        {"id": 3, "Text": "Temperatures", "Children": [
            sensor("/amdcpu/0/temperature/2", "Temperature", "Core (Tctl/Tdie)", 71.5),
            sensor("/amdcpu/0/temperature/3", "Temperature", "CCD1 (Tdie)", 66.0)]},
        {"id": 4, "Text": "Powers", "Children": [sensor("/amdcpu/0/power/0", "Power", "Package", 88.0)]},
        {"id": 5, "Text": "Clocks", "Children": [
            sensor("/amdcpu/0/clock/0", "Clock", "Bus Speed", 100.0),
            sensor("/amdcpu/0/clock/1", "Clock", "Core #1", 5000.0),
            sensor("/amdcpu/0/clock/2", "Clock", "Core #2", 4800.0)]}]},
    {"id": 6, "Text": "RTX", "HardwareId": "/gpu-nvidia/0", "Children": [
        sensor("/gpu-nvidia/0/temperature/0", "Temperature", "GPU Core", 90.0)]}]}]}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def lhm_opener(data, seen=None):
    def opener(url, timeout=None):
        if seen is not None:
            seen.append(getattr(url, "full_url", url))
        if isinstance(data, Exception):
            raise data
        return Resp(json.dumps(data).encode("utf-8"))
    return opener


def test_lhm_cpu_reading_ignores_gpu_and_bus_clock():
    reading = lhm_cpu_reading(parse_lhm_data(TREE).sensors)
    assert reading == {"cpu_temperature_hottest": (71.5, "C"), "cpu_package_power": (88.0, "W"),
                       "cpu_core_clock_avg": (4900.0, "MHz")}


def test_lhm_source_reads_local_server():
    seen = []
    src = LhmSource(opener=lhm_opener(TREE, seen))
    ok, reason = src.available()
    assert ok and "cpu_package_power" in reason
    assert src.read()["cpu_temperature_hottest"] == (71.5, "C")
    assert seen[0] == "http://127.0.0.1:8085/data.json"


def test_lhm_not_running_is_explained():
    ok, reason = LhmSource(opener=lhm_opener(ConnectionRefusedError("refused"))).available()
    assert not ok and "not reachable" in reason and "Remote Web Server" in reason
    gpu_only = {"id": 0, "Text": "S", "Children": [TREE["Children"][0]["Children"][1]]}
    assert LhmSource(opener=lhm_opener(gpu_only)).available() == (
        False, "LibreHardwareMonitor shows no CPU sensors (its driver may be missing)")


def _sensor(sid, text, kind, raw):
    return {"id": 0, "Text": text, "SensorId": sid, "Type": kind, "RawValue": raw, "Children": []}


# Shape and values as LibreHardwareMonitor v0.9.6 served them on 2026-10-17 (Core Ultra 9 275HX):
# RawValue is text "<number> <unit>", Intel adds "Distance to TjMax" temperatures, a load can be "NaN %".
REAL_TREE = {"id": 0, "Text": "Sensor", "Children": [{"id": 1, "Text": "HOST", "Children": [
    {"id": 3, "Text": "Intel Core Ultra 9 275HX", "HardwareId": "/intelcpu/0", "Children": [
        _sensor("/intelcpu/0/power/0", "CPU Package", "Power", "17.3 W"),
        _sensor("/intelcpu/0/power/1", "CPU Cores", "Power", "8.0 W"),
        _sensor("/intelcpu/0/clock/0", "Bus Speed", "Clock", "99.1 MHz"),
        _sensor("/intelcpu/0/clock/1", "P-Core #1", "Clock", "4063.0 MHz"),
        _sensor("/intelcpu/0/clock/3", "E-Core #1", "Clock", "4558.5 MHz"),
        _sensor("/intelcpu/0/temperature/0", "Core Max", "Temperature", "52.0 \u00b0C"),
        _sensor("/intelcpu/0/temperature/26", "CPU Package", "Temperature", "51.0 \u00b0C"),
        _sensor("/intelcpu/0/temperature/27", "P-Core #1 Distance to TjMax", "Temperature", "57.0 \u00b0C"),
        _sensor("/intelcpu/0/load/20", "CPU Core #19", "Load", "NaN %"),
    ]},
]}]}


def test_lhm_real_v096_format():
    parsed = parse_lhm_data(REAL_TREE)
    assert parsed.error is None and parsed.skipped == 1                     # only the "NaN %" load
    reading = lhm_cpu_reading(parsed.sensors)
    assert reading == {"cpu_temperature_hottest": (52.0, "C"),              # not the 57 "Distance to TjMax"
                       "cpu_package_power": (17.3, "W"),
                       "cpu_core_clock_avg": ((4063.0 + 4558.5) / 2, "MHz")}


def test_lhm_raw_value_in_other_windows_cultures():
    tree = {"id": 0, "Text": "S", "Children": [{"id": 1, "Text": "CPU", "HardwareId": "/amdcpu/0", "Children": [
        _sensor("/amdcpu/0/power/0", "Package", "Power", "17,3 W"),
        _sensor("/amdcpu/0/temperature/0", "Core (Tctl/Tdie)", "Temperature",
                "\u0666\u0665\u066b\u0665 \u00b0C"),
        _sensor("/amdcpu/0/temperature/1", "CCD1", "Temperature", "1.234.5 \u00b0C"),
    ]}]}
    parsed = parse_lhm_data(tree)
    assert parsed.skipped == 1
    assert lhm_cpu_reading(parsed.sensors) == {"cpu_temperature_hottest": (65.5, "C"), "cpu_package_power": (17.3, "W")}


def test_lhm_answering_without_sensors_is_not_called_unreachable():
    ok, reason = LhmSource(opener=lhm_opener({"id": 0, "Text": "Sensor", "Children": []})).available()
    assert not ok and "answered" in reason and "no sensors in data.json" in reason and "not reachable" not in reason


def test_lhm_refuses_non_local_address():
    seen = []
    ok, reason = LhmSource("http://192.168.1.5:8085/data.json", opener=lhm_opener(TREE, seen)).available()
    assert not ok and "only a local" in reason and seen == []
    assert is_loopback_url("http://localhost:8085/data.json") and not is_loopback_url("https://127.0.0.1/x")


class Times(tuple):
    """Like psutil's scputimes: a tuple of (user, system, idle) with an .idle field."""
    @property
    def idle(self):
        return self[2]


def fake_psutil(temps=None, freq=3600.0):
    ticks = iter([Times((0.0, 0.0, 0.0))] + [Times((37.5 * i, 0.0, 62.5 * i)) for i in range(1, 100)])
    return SimpleNamespace(
        cpu_times=lambda: next(ticks),
        cpu_percent=lambda interval=None: 0.0,          # must not be used (shared window, see sources.py)
        virtual_memory=lambda: SimpleNamespace(percent=52.0),
        sensors_temperatures=lambda: temps or {},
        cpu_freq=lambda: SimpleNamespace(current=freq))


def test_psutil_windows_has_no_clock_or_temperature():
    now = [0.0]
    src = PsutilSource(fake_psutil(), platform_name="win32", clock=lambda: now[0])
    assert src.available() == (True, "CPU load and memory")
    now[0] = 1.0
    assert src.read() == {"cpu_load": (37.5, "%"), "ram_used": (52.0, "%")}


def test_psutil_load_needs_a_measurement_window():
    now = [0.0]
    src = PsutilSource(fake_psutil(), platform_name="win32", clock=lambda: now[0])
    src.available()
    now[0] = 0.05                    # too soon after priming: no load value rather than a noisy 0
    assert "cpu_load" not in src.read()
    now[0] = 0.6
    assert src.read()["cpu_load"] == (37.5, "%")
    assert "cpu_load" not in PsutilSource(fake_psutil(), platform_name="win32").read()   # never primed


def test_psutil_load_ignores_idle_only_or_frozen_counters():
    flat = SimpleNamespace(cpu_times=lambda: Times((1.0, 1.0, 1.0)),
                           virtual_memory=lambda: SimpleNamespace(percent=10.0))
    now = [0.0]
    src = PsutilSource(flat, platform_name="win32", clock=lambda: now[0])
    src.available()
    now[0] = 2.0
    assert src.read() == {"ram_used": (10.0, "%")}          # no time passed in the counters: no value


def test_psutil_linux_temperature_and_clock():
    temps = {"k10temp": [SimpleNamespace(current=63.0), SimpleNamespace(current=70.25)],
             "nvme": [SimpleNamespace(current=99.0)]}
    reading = PsutilSource(fake_psutil(temps), platform_name="linux").read()
    assert reading["cpu_temperature_hottest"] == (70.25, "C") and reading["cpu_clock_avg"] == (3600.0, "MHz")
    bare = PsutilSource(fake_psutil({}, freq=0.0), platform_name="linux").read()
    assert "cpu_temperature_hottest" not in bare and "cpu_clock_avg" not in bare


def test_default_sources_per_platform():
    assert [s.name for s in default_sources("win32")] == ["psutil", "nvidia-smi", "LibreHardwareMonitor"]
    assert [s.name for s in default_sources("linux")] == ["psutil", "nvidia-smi", "RAPL"]
