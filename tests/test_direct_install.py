"""One-click install of a vendor zip with a pinned size and SHA-256 (app/install/direct.py), with fakes:
nothing is downloaded from the network."""

import hashlib
import io
import zipfile

import sys

import pytest
from PySide6.QtWidgets import QApplication

from app.install import direct
from app.install.plans import Host, plan_for
from data.installers import INSTALLERS


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def plan_with(data, **kw):
    plan = {"product": "Tool CLI", "version": "1.0", "url": "https://download.example.org/tool-1.0.zip",
            "hosts": ["download.example.org"], "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "folder": "tool-1.0", "exe": "tool.exe"}
    plan.update(kw)
    return plan


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(data, seen=None):
    def opener(req, timeout=None):
        if seen is not None:
            seen.append(req.full_url)
        return Response(data)
    return opener


def test_blender_launcher_is_pinned_on_blenders_server():
    win = INSTALLERS["blender_benchmark"]["windows"]
    plan = plan_for("blender_benchmark", host=Host("windows", "x64"))
    assert plan["kind"] == "zip" and plan["test_id"] == "blender_benchmark" and direct.problem(plan) is None
    assert plan["url"].startswith("https://download.blender.org/release/BlenderBenchmark2.0/launcher/")
    assert plan["size"] == 7755878 and plan["exe"] == "benchmark-launcher-cli.exe" and win["arch"] == ["x64"]
    assert plan_for("blender_benchmark", host=Host("windows", "arm64")) is None
    assert plan_for("blender_benchmark", host=Host("linux", "x64", "debian")) is None
    text = direct.confirm_text("Blender Benchmark", plan)
    assert plan["url"] in text and plan["sha256"] in text and "7.4 MB" in text and "nothing is run" in text


def test_download_is_checked_then_extracted_into_the_tools_folder(tmp_path):
    data = zip_bytes({"tool.exe": b"MZ", "README.txt": b"hi"})
    seen = []
    out = direct.install("Tool", plan_with(data), tmp_path, opener=opener_for(data, seen))
    assert out.ok and "SHA-256 verified" in out.message and seen == ["https://download.example.org/tool-1.0.zip"]
    folder = tmp_path / "tools" / "tool-1.0"
    assert (folder / "tool.exe").read_bytes() == b"MZ" and sorted(p.name for p in (tmp_path / "tools").iterdir()) == [
        "tool-1.0"]                                                   # the zip itself is not kept
    again = direct.install("Tool", plan_with(data), tmp_path, opener=lambda *a, **k: pytest.fail("no download"))
    assert again.ok and "already" in again.message


def test_a_changed_file_or_a_foreign_address_is_refused_and_nothing_kept(tmp_path):
    data = zip_bytes({"tool.exe": b"MZ"})
    out = direct.install("Tool", plan_with(data, sha256="0" * 64), tmp_path, opener=opener_for(data))
    assert not out.ok and "SHA-256" in out.message and list((tmp_path / "tools").iterdir()) == []
    out = direct.install("Tool", plan_with(data, url="https://evil.example.com/tool.zip"), tmp_path,
                         opener=lambda *a, **k: pytest.fail("no download"))
    assert not out.ok and "not the vendor's" in out.message
    assert direct.problem(plan_with(data, folder="..")) and direct.problem(plan_with(data, url="http://download.example.org/x"))
    no_exe = zip_bytes({"other.exe": b"MZ"})
    out = direct.install("Tool", plan_with(no_exe), tmp_path, opener=opener_for(no_exe))
    assert not out.ok and "tool.exe is not in the zip" in out.message and list((tmp_path / "tools").iterdir()) == []
    evil = zip_bytes({"../escape.exe": b"MZ", "tool.exe": b"MZ"})
    out = direct.install("Tool", plan_with(evil), tmp_path, opener=opener_for(evil))
    assert not out.ok and "unsafe path" in out.message and not (tmp_path / "escape.exe").exists()


def test_the_install_button_asks_then_installs_and_scans_again(qapp, monkeypatch, tmp_path):
    from test_install import entry, flow_for
    data = zip_bytes({"tool.exe": b"MZ"})
    plan = dict(plan_with(data), kind="zip", test_id="blender_benchmark")
    monkeypatch.setattr("app.ui.install_flow.install_plan", lambda e, host=None: plan)
    flow, win, asked, _ = flow_for(qapp, [True], None, None)
    real = direct.install
    monkeypatch.setattr(flow.direct, "install", lambda name, p: real(name, p, tmp_path, opener=opener_for(data)))
    assert flow.start(entry("blender_benchmark"))
    assert "Download and install" in asked[0] and win.scans == 1 and (tmp_path / "tools" / "tool-1.0" / "tool.exe").is_file()
    flow, win, asked, _ = flow_for(qapp, [False], None, None)
    assert not flow.start(entry("blender_benchmark")) and win.scans == 0


def test_the_scan_also_looks_in_the_tools_folder(monkeypatch, tmp_path):
    from app import benchmark_manager
    from app.benchmark_manager import BenchmarkManager
    from data.benchmark_definitions import DEFINITIONS_BY_ID
    folder = tmp_path / "benchmark-launcher-cli-3.3.0"
    folder.mkdir()
    (folder / "benchmark-launcher-cli.exe").write_bytes(b"")
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.setattr(benchmark_manager, "app_tools_roots", lambda: [str(tmp_path)])
    found, _ = BenchmarkManager()._scan_filesystem(DEFINITIONS_BY_ID["blender_benchmark"], [])
    assert found == [str(folder / "benchmark-launcher-cli.exe")]
