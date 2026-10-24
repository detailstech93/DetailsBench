"""The PC's parts saved with each session (app/environment/hardware.py), from made-up system output."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.environment import hardware
from app.environment.profile import EnvironmentProfile
from app.manifest import build_manifest

NV = "NVIDIA GeForce RTX 5090 Laptop GPU, 617.42, 0x2C1810DE, 0x810D1B61, 24463, 175.00, 95.00, 175.00\n"

WIN = {
    "cs": {"Manufacturer": "CWL", "Model": "CWL", "SystemFamily": "X", "PCSystemType": 2},
    "board": {"Manufacturer": "CWL", "Product": "CWL"},
    "chassis": 3, "bios": "1.05",
    "cpu": {"Name": "Intel(R) Core(TM) Ultra 9 275HX", "Manufacturer": "GenuineIntel", "NumberOfCores": 24,
            "NumberOfLogicalProcessors": 24, "MaxClockSpeed": 2700},
    "gpu": [{"Name": "Intel(R) Graphics", "DriverVersion": "32.0.101.9150",
             "PNPDeviceID": "PCI\\VEN_8086&DEV_7D67&SUBSYS_81021B61&REV_04\\3&11583659&0&10", "AdapterRAM": 134217728},
            {"Name": "NVIDIA GeForce RTX 5090 Laptop GPU", "DriverVersion": "32.0.15.6742",
             "PNPDeviceID": "PCI\\VEN_10DE&DEV_2C18&SUBSYS_810D1B61&REV_A1\\4&1", "AdapterRAM": 4293918720}],
    "ram": [{"Capacity": 51539607552, "Speed": 5600, "ConfiguredClockSpeed": 5600, "Manufacturer": "0x0FC4",
             "PartNumber": "TDS5DDDG08", "DeviceLocator": "Controller0-ChannelA-DIMM1"}],
    "disks": [{"DeviceId": "0", "FriendlyName": "FORESEE XP2300F002T", "MediaType": 4, "BusType": 17,
               "Size": 2048408248320, "FirmwareVersion": "X0828BT"},
              {"DeviceId": "1", "FriendlyName": "WD Blue", "MediaType": 3, "BusType": 11, "Size": 1000204886016}],
    "os": {"Caption": "Microsoft Windows 11 Pro", "Version": "10.0.26300", "BuildNumber": "26300"},
    "guid": "1234-abcd", "data_disk": 0,
}


@pytest.fixture
def no_nvidia(monkeypatch):
    import app.telemetry.sources as sources
    monkeypatch.setattr(sources, "_nvidia_smi_path", lambda: None)


def test_nvidia_smi_lines():
    gpu = hardware.parse_nvidia_smi(NV + "garbage\n")[0]
    assert gpu["vendor_id"] == "10de" and gpu["device_id"] == "2c18" and gpu["subsys_id"] == "810d1b61"
    assert (gpu["driver"], gpu["vram_mb"], gpu["power_limit_w"], gpu["power_default_w"]) == ("617.42", 24463, 175, 95)
    assert hardware.parse_nvidia_smi("A, 1, [N/A], [N/A], [N/A], [N/A], [N/A], [N/A]")[0]["device_id"] is None


def test_windows_parts():
    hw = hardware.parse_windows(WIN)
    d = hw["device"]
    assert d["kind"] == "laptop"                      # PCSystemType Mobile wins over chassis Desktop (3)
    assert d["placeholder"]                           # "CWL" "CWL" names no model: the owner names it
    assert hw["cpu"]["cores"] == 24 and hw["cpu"]["sockets"] == 1
    intel, nv = hw["gpus"]
    assert intel["vram_mb"] == 128 and nv["vram_mb"] is None        # AdapterRAM near 4 GB is wrong: not kept
    assert (nv["vendor_id"], nv["device_id"], nv["subsys_id"]) == ("10de", "2c18", "810d1b61")
    assert hw["ram"]["total_gb"] == 48 and hw["ram"]["modules"][0]["speed_mts"] == 5600
    assert [(x["kind"], x["bus"], x["holds_app_data"]) for x in hw["drives"]] == [("nvme", "NVMe", True),
                                                                                  ("hdd", "SATA", False)]
    assert hw["os"]["build"] == "26300"
    assert hw["unit_id"] == hardware.unit_id("1234-ABCD") and "1234" not in hw["unit_id"]


def test_nvidia_fills_the_same_card():
    hw = hardware.parse_windows(WIN)
    gpus = hardware.merge_nvidia(hw["gpus"], hardware.parse_nvidia_smi(NV))
    assert len(gpus) == 2 and gpus[1]["driver"] == "617.42" and gpus[1]["vram_mb"] == 24463
    assert gpus[1]["source"] == "Win32_VideoController + nvidia-smi"
    assert gpus[0]["driver"] == "32.0.101.9150"


@pytest.mark.parametrize("maker,model,flag", [("To Be Filled By O.E.M.", "X", True), ("ASUS", "System Product Name", True),
                                              ("Dell Inc.", "XPS 15 9530", False), (None, "X", True)])
def test_placeholder_names(maker, model, flag):
    assert hardware._device(maker, model)["placeholder"] is flag


def test_kinds():
    assert hardware.kind_of([10]) == "laptop" and hardware.kind_of([3]) == "desktop"
    assert hardware.kind_of([35]) == "mini PC" and hardware.kind_of([], arm_board=True) == "board"
    assert hardware.kind_of([]) is None


def _tree(root: Path, files: dict):
    for path, text in files.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def test_linux_parts(tmp_path, no_nvidia):
    _tree(tmp_path, {
        "sys/class/dmi/id/sys_vendor": "LENOVO\n", "sys/class/dmi/id/product_name": "83DG\n",
        "sys/class/dmi/id/product_family": "Legion Pro 7\n", "sys/class/dmi/id/chassis_type": "10\n",
        "proc/cpuinfo": "processor\t: 0\nvendor_id\t: AuthenticAMD\nmodel name\t: AMD Ryzen 9\nphysical id\t: 0\n"
                        "core id\t: 0\n\nprocessor\t: 1\nphysical id\t: 0\ncore id\t: 0\n",
        "proc/meminfo": "MemTotal:       32768000 kB\n",
        "sys/class/drm/card1/device/vendor": "0x1002\n", "sys/class/drm/card1/device/device": "0x744c\n",
        "sys/class/drm/card1/device/uevent": "DRIVER=amdgpu\nPCI_ID=1002:744C\n",
        "sys/class/drm/card1/device/mem_info_vram_total": str(24 * 2 ** 30),
        "sys/class/drm/card1-DP-1/status": "connected",
        "sys/block/nvme0n1/size": str(2 * 10 ** 12 // 512), "sys/block/nvme0n1/queue/rotational": "0",
        "sys/block/nvme0n1/device/model": "Samsung SSD  990 PRO 2TB\n",
        "sys/block/nvme0n1/device/firmware_rev": "4B2QJXD7\n",
        "sys/block/loop0/size": "8",
        "etc/os-release": 'NAME="Fedora Linux"\nVERSION_ID=43\nPRETTY_NAME="Fedora Linux 43 (Workstation Edition)"\n',
        "etc/machine-id": "0123456789abcdef\n"})
    def run(cmd, **kwargs):
        assert cmd == ["lspci", "-mm", "-d", "1002:744c"] and kwargs["timeout"]
        return SimpleNamespace(returncode=0, stderr="", stdout='03:00.0 "VGA compatible controller" '
                               '"Advanced Micro Devices, Inc. [AMD/ATI]" "Navi 31 [Radeon RX 7900 XTX]" -rc8\n')
    hw = hardware.capture(tmp_path, platform_name="linux", linux_root=tmp_path, run=run)
    assert hw["gpus"][0]["name"] == "Advanced Micro Devices, Inc. [AMD/ATI] Navi 31 [Radeon RX 7900 XTX]"
    assert hw["device"]["maker"] == "LENOVO" and hw["device"]["kind"] == "laptop" and not hw["device"]["placeholder"]
    assert hw["cpu"]["vendor"] == "AuthenticAMD" and hw["cpu"]["threads"] == 2 and hw["cpu"]["cores"] == 1
    assert hw["ram"]["total_gb"] == 31.2 and hw["ram"]["modules"] == []
    assert hw["gpus"] == [dict(hw["gpus"][0], vendor_id="1002", device_id="744c", driver_name="amdgpu",
                               vram_mb=24576)]
    assert [(x["id"], x["kind"], x["size_gb"], x["firmware"]) for x in hw["drives"]] == [("nvme0n1", "nvme", 2000,
                                                                                         "4B2QJXD7")]
    assert hw["drives"][0]["model"] == "Samsung SSD 990 PRO 2TB"
    assert hw["os"]["name"] == "Fedora Linux 43 (Workstation Edition)" and hw["unit_id"] and hw["notes"] == []


def test_arm_board_from_the_device_tree(tmp_path, no_nvidia):
    _tree(tmp_path, {"proc/device-tree/model": "Raspberry Pi 5 Model B Rev 1.0\x00",
                     "proc/cpuinfo": "processor\t: 0\nCPU implementer\t: 0x41\nModel\t\t: Raspberry Pi 5 Model B\n"})
    hw = hardware.capture(tmp_path, platform_name="linux", linux_root=tmp_path)
    assert hw["device"]["model"] == "Raspberry Pi 5 Model B Rev 1.0" and hw["device"]["maker"] == "Raspberry"
    assert hw["device"]["kind"] == "board" and hw["cpu"]["vendor"] == "ARM"


def test_capture_never_raises(tmp_path, no_nvidia):
    def broken(*a, **k):
        raise OSError("no powershell")
    hw = hardware.capture(tmp_path, platform_name="win32", run=broken)
    assert hw["schema"] == hardware.SCHEMA and "no powershell" in hw["notes"][0] and "device" not in hw


def test_windows_capture_reads_the_drive_letter(tmp_path, no_nvidia):
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"], seen["timeout"] = cmd, kwargs.get("timeout")
        return SimpleNamespace(returncode=0, stdout=json.dumps(WIN), stderr="")
    hw = hardware.capture(tmp_path, platform_name="win32", run=run)
    assert isinstance(seen["cmd"], list) and seen["timeout"]
    assert f"-DriveLetter '{str(tmp_path.resolve())[0].upper()}'" in seen["cmd"][-1]
    assert hardware.app_data_drive(hw)["model"] == "FORESEE XP2300F002T"


def test_saved_with_the_session():
    profile = EnvironmentProfile()
    profile.hardware = {"schema": 1, "drives": []}
    again = EnvironmentProfile.from_dict(json.loads(json.dumps(profile.to_dict())))
    assert again.hardware == profile.hardware and "hardware" not in again.field_names()
    manifest = build_manifest({"id": 1}, environment=profile.to_dict(),
                              benchmarks_used=[{"name": "diskspd", "drive": {"model": "X"}}, {"name": "7zip"}])
    assert manifest["environment"]["hardware"]["schema"] == 1
    assert manifest["benchmarks"][0]["drive"] == {"model": "X"} and "drive" not in manifest["benchmarks"][1]
