"""LibreHardwareMonitor setup with fakes only: no network, no real download, no process started."""

import base64
import hashlib
import io
import os
import sys
import urllib.error
import zipfile

import pytest

from app.telemetry import lhm_provision as lp
from app.telemetry.lhm_files import LhmAsset, parse_config
from app.telemetry.sources import LhmSource, RaplSource, default_sources

URL = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v0.9.6/LibreHardwareMonitor.zip"


def make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def asset_for(data: bytes, **kw) -> LhmAsset:
    return LhmAsset(version="0.9.6", tag="v0.9.6", name="LibreHardwareMonitor.zip", url=URL, size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(), **kw)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def opener_for(data: bytes, calls=None):
    def opener(req, timeout=None):
        if calls is not None:
            calls.append(getattr(req, "full_url", req))
        return Resp(data)
    return opener


# ---- extraction ----------------------------------------------------------------

def test_extract_top_level_and_single_folder(tmp_path):
    z1 = tmp_path / "a.zip"
    z1.write_bytes(make_zip({"LibreHardwareMonitor.exe": b"MZ", "lib/x.dll": b"d"}))
    assert lp.extract_zip(z1, tmp_path / "out1") is None
    assert (tmp_path / "out1" / "LibreHardwareMonitor.exe").read_bytes() == b"MZ"
    z2 = tmp_path / "b.zip"
    z2.write_bytes(make_zip({"LHM/LibreHardwareMonitor.exe": b"MZ", "LHM/x.dll": b"d"}))
    assert lp.extract_zip(z2, tmp_path / "out2") is None
    assert (tmp_path / "out2" / "x.dll").is_file() and not (tmp_path / "out2.partial").exists()


@pytest.mark.parametrize("name", ["../evil.exe", "a/../../evil.exe"])
def test_extract_refuses_paths_leaving_the_folder(tmp_path, name):
    z = tmp_path / "e.zip"
    z.write_bytes(make_zip({"LibreHardwareMonitor.exe": b"MZ", name: b"x"}))
    problem = lp.extract_zip(z, tmp_path / "t" / "out")
    assert "unsafe path" in problem
    assert not (tmp_path / "t" / "out").exists() and not (tmp_path / "t" / "out.partial").exists()
    assert not (tmp_path / "evil.exe").exists() and not (tmp_path / "t" / "evil.exe").exists()


def test_extract_without_exe_keeps_nothing(tmp_path):
    z = tmp_path / "n.zip"
    z.write_bytes(make_zip({"readme.txt": b"x"}))
    assert "not at the top" in lp.extract_zip(z, tmp_path / "out")
    assert not (tmp_path / "out").exists() and not (tmp_path / "out.partial").exists()


# ---- download ------------------------------------------------------------------

def test_download_verifies_extracts_and_finds(tmp_path):
    data = make_zip({"LibreHardwareMonitor.exe": b"MZ"})
    calls = []
    found = lp.download_lhm(asset_for(data), tmp_path, opener=opener_for(data, calls))
    assert found.error is None and found.version == "0.9.6"
    assert found.exe == str(tmp_path / "tools" / "librehardwaremonitor" / "0.9.6" / "LibreHardwareMonitor.exe")
    assert "size and SHA-256 verified" in found.notes and calls == [URL]
    assert lp.find_local(tmp_path) == tmp_path / "tools" / "librehardwaremonitor" / "0.9.6" / "LibreHardwareMonitor.exe"
    assert not list((tmp_path / "tools" / "librehardwaremonitor").glob("*.zip"))      # only the folder is kept


def test_download_with_wrong_checksum_keeps_nothing(tmp_path):
    data = make_zip({"LibreHardwareMonitor.exe": b"MZ"})
    bad = asset_for(data)
    bad.sha256 = "0" * 64
    found = lp.download_lhm(bad, tmp_path, opener=opener_for(data))
    assert "SHA-256" in found.error and lp.find_local(tmp_path) is None
    assert not any(p.is_file() for p in (tmp_path / "tools").rglob("*"))


def test_download_arm_note_and_error_passthrough(tmp_path):
    data = make_zip({"LibreHardwareMonitor.exe": b"MZ"})
    found = lp.download_lhm(asset_for(data), tmp_path, opener=opener_for(data), arch="arm64")
    assert lp.ARM_NOTE in found.notes
    assert lp.download_lhm(LhmAsset(error="nope"), tmp_path).error == "nope"


def test_find_local_picks_newest_version(tmp_path):
    for v in ("0.9.5", "0.9.10", "notaversion"):
        d = lp.tools_dir(tmp_path) / v
        d.mkdir(parents=True)
        (d / "LibreHardwareMonitor.exe").write_bytes(b"MZ")
    assert lp.find_local(tmp_path).parent.name == "0.9.10"
    assert lp.find_local(tmp_path / "none") is None


def test_plan_download_is_windows_only_and_reports_fetch_errors():
    assert "Windows-only" in lp.plan_download("linux").error
    def boom():
        raise OSError("offline")
    assert "offline" in lp.plan_download("win32", fetch=boom).error


# ---- settings and launch -------------------------------------------------------

def test_configure_web_server_keeps_other_settings(tmp_path):
    exe = tmp_path / "LibreHardwareMonitor.exe"
    exe.write_bytes(b"MZ")
    (tmp_path / "LibreHardwareMonitor.config").write_text(
        '<?xml version="1.0" encoding="utf-8"?><configuration><appSettings>'
        '<add key="mainForm.Width" value="470" /><add key="runWebServerMenuItem" value="false" />'
        '</appSettings></configuration>', encoding="utf-8")
    assert lp.configure_web_server(exe, running=lambda: False, new_password=lambda: "s3cret&<x>") is None
    settings = lp.read_settings(exe).settings
    assert settings["mainForm.Width"] == "470" and settings["runWebServerMenuItem"] == "true"
    assert settings["authenticationEnabled"] == "true" and settings["authenticationPassword"] == "s3cret&<x>"
    assert settings["listenerPort"] == "8085"
    assert not (tmp_path / "LibreHardwareMonitor.config.partial").exists()


def test_configure_refuses_while_running_and_on_broken_file(tmp_path):
    exe = tmp_path / "LibreHardwareMonitor.exe"
    assert "close it first" in lp.configure_web_server(exe, running=lambda: True)
    (tmp_path / "LibreHardwareMonitor.config").write_text("<not xml", encoding="utf-8")
    assert "not valid XML" in lp.configure_web_server(exe, running=lambda: False)
    assert (tmp_path / "LibreHardwareMonitor.config").read_text(encoding="utf-8") == "<not xml"   # untouched


def test_default_password_is_random(tmp_path):
    exe = tmp_path / "LibreHardwareMonitor.exe"
    lp.configure_web_server(exe, running=lambda: False)
    first = lp.read_settings(exe).settings["authenticationPassword"]
    lp.configure_web_server(exe, running=lambda: False)
    second = lp.read_settings(exe).settings["authenticationPassword"]
    assert len(first) >= 24 and first != second


def test_is_running_by_name():
    class P:
        def __init__(self, name):
            self.info = {"name": name}
    assert lp.is_running(lambda attrs: [P("explorer.exe"), P("librehardwaremonitor.exe")])
    assert not lp.is_running(lambda attrs: [P("explorer.exe"), P(None)])


class FakeApi:
    def __init__(self, handle=1, err=None):
        self.handle, self.err, self.calls, self.closed = handle, err, [], []

    def shell_execute(self, file, params, cwd, show):
        self.calls.append((file, params, cwd))
        return (self.handle, None) if self.handle else (None, self.err)

    def close(self, handle):
        self.closed.append(handle)


def test_launch_through_uac(tmp_path):
    api = FakeApi()
    assert lp.launch(tmp_path / "LibreHardwareMonitor.exe", api=api) is None
    assert api.calls == [(str(tmp_path / "LibreHardwareMonitor.exe"), "", str(tmp_path))] and api.closed == [1]
    assert lp.launch("x.exe", api=FakeApi(None, 1223)) == "the UAC prompt was declined"
    assert "error 2" in lp.launch("x.exe", api=FakeApi(None, 2))


# ---- telemetry with the password -----------------------------------------------

TREE = {"id": 0, "Text": "Sensor", "Children": [{"id": 1, "Text": "CPU", "HardwareId": "/intelcpu/0", "Children": [
    {"id": 2, "Text": "CPU Package", "SensorId": "/intelcpu/0/power/0", "Type": "Power", "RawValue": "17.3 W"}]}]}


def test_lhm_source_sends_password_and_port_from_settings():
    import json
    seen = []

    def opener(req, timeout=None):
        seen.append((req.full_url, req.get_header("Authorization")))
        return Resp(json.dumps(TREE).encode("utf-8"))

    settings = {"authenticationEnabled": "true", "authenticationUserName": "pcbm",
                "authenticationPassword": "abc", "listenerPort": "8090"}
    src = LhmSource(opener=opener, settings=lambda: settings)
    assert src.read() == {"cpu_package_power": (17.3, "W")}
    assert seen == [("http://127.0.0.1:8090/data.json", "Basic " + base64.b64encode(b"pcbm:abc").decode())]
    seen.clear()
    LhmSource(opener=opener).read()
    assert seen == [("http://127.0.0.1:8085/data.json", None)]


def test_lhm_source_wrong_password_is_explained():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
    ok, reason = LhmSource(opener=opener).available()
    assert not ok and "password" in reason and "not reachable" not in reason


def test_broken_settings_reader_falls_back_to_defaults():
    def boom():
        raise OSError("gone")
    assert LhmSource(settings=boom).url == "http://127.0.0.1:8085/data.json"


# ---- RAPL (Linux), on a fake /sys (":" is not allowed in Windows folder names) ----

class FakeSys:
    def __init__(self, zones):
        self.files = {}
        for zone, (name, energy, max_range) in zones.items():
            self.files[f"/rapl/{zone}/name"] = name + "\n"
            self.files[f"/rapl/{zone}/energy_uj"] = f"{energy}\n"
            self.files[f"/rapl/{zone}/max_energy_range_uj"] = f"{max_range}\n"
        self.denied = set()

    def listdir(self, path):
        if path != "/rapl":
            raise FileNotFoundError(path)
        return sorted({k.split("/")[2] for k in self.files})

    def read_text(self, path):
        if path in self.denied:
            raise PermissionError(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def source(self, clock=lambda: 0.0):
        return RaplSource("/rapl", clock=clock, listdir=self.listdir, read_text=self.read_text)


def test_rapl_power_from_energy_delta():
    fs = FakeSys({"intel-rapl:0": ("package-0", 1_000_000, 100_000_000),
                  "intel-rapl:0:0": ("core", 5, 10), "intel-rapl:1": ("psys", 7, 10)})
    t = [100.0]
    src = fs.source(clock=lambda: t[0])
    assert src.available() == (True, "CPU package power (1 package(s))")
    assert src.read() == {}                                    # first reading starts the window
    t[0] = 102.0
    fs.files["/rapl/intel-rapl:0/energy_uj"] = "31000000\n"   # +30 J over 2 s
    assert src.read() == {"cpu_package_power": (pytest.approx(15.0), "W")}
    t[0] = 104.0
    fs.files["/rapl/intel-rapl:0/energy_uj"] = "1000000\n"    # wrapped (range 100 J): +70 J over 2 s
    assert src.read() == {"cpu_package_power": (pytest.approx(35.0), "W")}


def test_rapl_two_packages_are_summed():
    fs = FakeSys({"intel-rapl:0": ("package-0", 0, 10**12), "intel-rapl:1": ("package-1", 0, 10**12)})
    t = [0.0]
    src = fs.source(clock=lambda: t[0])
    src.read()
    t[0] = 1.0
    fs.files["/rapl/intel-rapl:0/energy_uj"] = "40000000"
    fs.files["/rapl/intel-rapl:1/energy_uj"] = "35000000"
    assert src.read() == {"cpu_package_power": (pytest.approx(75.0), "W")}


def test_rapl_not_available_reasons():
    assert RaplSource("/nowhere", listdir=FakeSys({}).listdir).available() == (
        False, "no RAPL package energy counters under /nowhere")
    fs = FakeSys({"intel-rapl:0": ("package-0", 1, 10)})
    fs.files["/rapl/intel-rapl:0/energy_uj"] = "garbage"
    ok, reason = fs.source().available()
    assert not ok and "could not be read" in reason


def test_rapl_root_only_is_explained():
    fs = FakeSys({"intel-rapl:0": ("package-0", 1, 10)})
    fs.denied.add("/rapl/intel-rapl:0/energy_uj")
    ok, reason = fs.source().available()
    assert not ok and "root only" in reason
