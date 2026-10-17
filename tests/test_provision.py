"""PresentMon provisioning with a fake network and a fake signature check (no download, no PowerShell)."""

import hashlib
import io
import json

import pytest

from app.frames import provision
from app.frames.provision import (
    authenticode_problem, download_presentmon, download_verified, find_local, plan_download, tools_dir,
)

PAYLOAD = b"MZ" + b"\x00" * 5000
SHA = hashlib.sha256(PAYLOAD).hexdigest()
URL = "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe"


def release(tag="v2.6.0", assets=None):
    return {"tag_name": tag, "draft": False, "prerelease": False, "assets": assets if assets is not None else [
        {"name": "PresentMon-2.6.0-x64.exe", "size": len(PAYLOAD), "browser_download_url": URL,
         "digest": "sha256:" + SHA},
        {"name": "PresentMon-2.6.0.msi", "size": 9, "browser_download_url": URL + ".msi", "digest": None}]}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def opener_for(body, calls=None):
    def opener(req, timeout=None):
        if calls is not None:
            calls.append(req.full_url)
        return Resp(body)
    return opener




def test_plan_x64_and_arm64_emulated():
    x64 = plan_download("x64", fetch=lambda: [release()])
    assert (x64.name, x64.version, x64.emulated, x64.error) == ("PresentMon-2.6.0-x64.exe", "2.6.0", False, None)
    arm = plan_download("arm64", fetch=lambda: [release()])
    assert arm.asset_arch == "x64" and arm.emulated is True


def test_plan_network_error_is_reported():
    def boom():
        raise OSError("offline")
    assert "offline" in plan_download("x64", fetch=boom).error


def test_download_checks_size_and_hash(tmp_path):
    dest = tmp_path / "d" / "PresentMon-2.6.0-x64.exe"
    assert download_verified(URL, dest, len(PAYLOAD), SHA, opener_for(PAYLOAD)) is None
    assert dest.read_bytes() == PAYLOAD and not (dest.parent / (dest.name + ".partial")).exists()
    assert download_verified(URL, dest, len(PAYLOAD), SHA, opener_for(PAYLOAD)).startswith("already exists")


@pytest.mark.parametrize("body,size,sha,reason", [
    (PAYLOAD + b"x", len(PAYLOAD), SHA, "more data"),
    (PAYLOAD[:-1], len(PAYLOAD), SHA, "differs from the published size"),
    (b"MZ" + b"\x01" * 5000, len(PAYLOAD), SHA, "SHA-256 differs"),
])
def test_bad_download_leaves_nothing(tmp_path, body, size, sha, reason):
    dest = tmp_path / "PresentMon-2.6.0-x64.exe"
    problem = download_verified(URL, dest, size, sha, opener_for(body))
    assert reason in problem
    assert list(tmp_path.iterdir()) == []


def test_download_presentmon_keeps_only_signed_file(tmp_path):
    choice = plan_download("x64", fetch=lambda: [release()])
    bad = download_presentmon(choice, tmp_path, opener_for(PAYLOAD), signature_check=lambda p: "signature status NotSigned")
    assert "NotSigned" in bad.error and bad.path is None
    assert not (tools_dir(tmp_path) / "2.6.0" / "PresentMon-2.6.0-x64.exe").exists()
    good = download_presentmon(choice, tmp_path, opener_for(PAYLOAD), signature_check=lambda p: None)
    assert good.source == "download" and good.path.endswith("PresentMon-2.6.0-x64.exe")
    calls = []
    again = download_presentmon(choice, tmp_path, opener_for(PAYLOAD, calls), signature_check=lambda p: None)
    assert again.source == "downloaded" and calls == []          # reused, not downloaded twice


def test_arm64_download_notes_emulation(tmp_path):
    choice = plan_download("arm64", fetch=lambda: [release()])
    found = download_presentmon(choice, tmp_path, opener_for(PAYLOAD), signature_check=lambda p: None)
    assert found.emulated and any("not verified on ARM hardware" in n for n in found.notes)


def make_exe(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(b"MZ")
    return p


def test_find_local_order(tmp_path):
    pf = tmp_path / "pf"
    intel = make_exe(pf / "Intel" / "PresentMon" / "PresentMonConsoleApplication", "PresentMon-2.6.0-x64.exe")
    data = tmp_path / "data"
    make_exe(tools_dir(data) / "2.5.1", "PresentMon-2.5.1-x64.exe")
    env = {"ProgramFiles": str(pf)}
    found = find_local("x64", data, env=env, signature_check=lambda p: None)
    assert (found.source, found.path, found.version) == ("intel install", str(intel), "2.6.0")
    chosen = make_exe(tmp_path / "mine", "PresentMon.exe")
    assert find_local("x64", data, str(chosen), env=env, signature_check=lambda p: None).source == "settings"
    # an unsigned Intel copy is skipped; the signed downloaded one is used
    found = find_local("x64", data, env=env, signature_check=lambda p: "bad" if "Intel" in p else None)
    assert (found.source, found.version) == ("downloaded", "2.5.1")
    assert find_local("x64", data, env=env, signature_check=lambda p: "bad").error


def test_find_local_arm64_prefers_native_file(tmp_path):
    folder = tmp_path / "pf" / "Intel" / "PresentMon" / "PresentMonConsoleApplication"
    make_exe(folder, "PresentMon-2.6.0-x64.exe")
    make_exe(folder, "PresentMon-2.6.0-arm64.exe")
    found = find_local("arm64", tmp_path / "d", env={"ProgramFiles": str(tmp_path / "pf")},
                       signature_check=lambda p: None)
    assert found.path.endswith("arm64.exe") and found.emulated is False
    assert find_local("x86", tmp_path / "d", env={"ProgramFiles": str(tmp_path / "pf")},
                      signature_check=lambda p: None).error


def test_authenticode_parsing(tmp_path, monkeypatch):
    monkeypatch.setattr(provision.sys, "platform", "win32")

    def runner_for(out):
        return lambda *a, **k: type("R", (), {"stdout": out})()
    ok = json.dumps({"status": "Valid", "subject": "CN=Intel Corporation, O=Intel Corporation, C=US"})
    assert authenticode_problem("x.exe", runner_for(ok)) is None
    other = json.dumps({"status": "Valid", "subject": "CN=Intel Corporation Fake Ltd, O=X"})
    assert "not Intel" in authenticode_problem("x.exe", runner_for(other))
    assert "NotSigned" in authenticode_problem("x.exe", runner_for(json.dumps({"status": "NotSigned"})))
    assert "failed" in authenticode_problem("x.exe", runner_for("not json"))
