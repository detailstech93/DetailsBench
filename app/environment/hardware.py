"""The parts of this PC, saved with every session (hardware results library, phase 1, owner 2026-10-24).

Read only and without admin rights; a part that cannot be read is left None, never guessed:
- Windows: one PowerShell call of CIM classes Microsoft documents (Win32_ComputerSystem, Win32_BaseBoard,
  Win32_SystemEnclosure, Win32_BIOS, Win32_Processor, Win32_VideoController, Win32_PhysicalMemory,
  Win32_OperatingSystem) and Get-PhysicalDisk / Get-Partition (Storage module).
- Linux: the kernel's /sys/class/dmi/id (maker, model, board, chassis type), /proc/device-tree/model (ARM boards),
  /proc/cpuinfo, /proc/meminfo, /sys/class/drm (graphics cards: PCI ids, driver, AMD VRAM), /sys/block (drives),
  /etc/os-release. RAM speed needs dmidecode as root there, so it is left None.
- NVIDIA cards on both: nvidia-smi (driver, VRAM, power limit).

The unit id tells one PC from another of the same model: a hash of the OS's own machine id (Windows MachineGuid,
Linux /etc/machine-id), so the id itself is never saved. A maker or model that is a firmware placeholder
("To Be Filled By O.E.M.", "System Product Name", ...) is kept as read and marked placeholder, for the owner to name.
"""

import hashlib
import json
import logging
import os
import platform
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

SCHEMA = 1
PLACEHOLDERS = {"default string", "to be filled by o.e.m.", "system product name", "system manufacturer",
                "o.e.m.", "oem", "not applicable", "none", "unknown", "type1productconfigid", "x.x",
                "system version", "base board product name", "base board manufacturer", "123456789", "0"}
# SMBIOS chassis types (DMTF DSP0134, 7.4.1)
LAPTOP = {8, 9, 10, 14, 31, 32}
DESKTOP = {3, 4, 5, 6, 7, 15, 16, 24}
KINDS = {13: "all-in-one", 30: "tablet", 35: "mini PC", 36: "mini PC", 17: "server", 23: "server", 28: "server"}
WIN_MEDIA = {3: "hdd", 4: "ssd", 5: "scm"}
WIN_BUS = {1: "SCSI", 3: "ATA", 6: "Fibre", 7: "USB", 8: "RAID", 9: "iSCSI", 10: "SAS", 11: "SATA", 12: "SD",
           13: "MMC", 15: "File", 16: "Storage Spaces", 17: "NVMe", 18: "UFS"}
NV_FIELDS = ("name", "driver_version", "pci.device_id", "pci.sub_device_id", "memory.total", "enforced.power.limit",
             "power.default_limit", "power.max_limit")       # power.limit reads [N/A] on laptops: enforced is set

PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$o = [ordered]@{}
$o.cs = Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,SystemFamily,PCSystemType
$o.board = Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer,Product
$o.chassis = @((Get-CimInstance Win32_SystemEnclosure).ChassisTypes)
$o.bios = (Get-CimInstance Win32_BIOS).SMBIOSBIOSVersion
$o.cpu = Get-CimInstance Win32_Processor | Select-Object Name,Manufacturer,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed
$o.gpu = Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,PNPDeviceID,AdapterRAM
$o.ram = Get-CimInstance Win32_PhysicalMemory | Select-Object Capacity,Speed,ConfiguredClockSpeed,Manufacturer,PartNumber,DeviceLocator
$o.disks = Get-PhysicalDisk | Select-Object DeviceId,FriendlyName,MediaType,BusType,Size,FirmwareVersion
$o.os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber
$o.guid = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography').MachineGuid
$o.data_disk = (Get-Partition -DriveLetter '__LETTER__' | Get-Disk).Number
$o | ConvertTo-Json -Depth 4 -Compress
"""


def _list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(value) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def _int(value) -> Optional[int]:
    try:
        return int(str(value).strip(), 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return None


def _float(value) -> Optional[float]:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def is_placeholder(text: Optional[str]) -> bool:
    return text is None or text.strip().lower() in PLACEHOLDERS


def unit_id(machine_id: Optional[str]) -> Optional[str]:
    """A hash of the OS's machine id: the same PC gets the same id, and the id itself is not saved."""
    machine_id = (machine_id or "").strip().lower()
    if not machine_id:
        return None
    return hashlib.sha256(("detailsbench:" + machine_id).encode()).hexdigest()[:16]


def kind_of(chassis: List[int], mobile: bool = False, arm_board: bool = False) -> Optional[str]:
    """Windows' PCSystemType Mobile wins over the chassis type: a laptop's firmware can say Desktop (3)."""
    if mobile:
        return "laptop"
    for c in chassis:
        if c in KINDS:
            return KINDS[c]
        if c in LAPTOP:
            return "laptop"
        if c in DESKTOP:
            return "desktop"
    if arm_board:
        return "board"                  # a single-board computer (Raspberry Pi, ...)
    return None


def _device(maker, model, family=None, board_maker=None, board=None, bios=None, kind=None, source="") -> dict:
    maker, model = _text(maker), _text(model)
    return {"maker": maker, "model": model, "family": _text(family), "board_maker": _text(board_maker),
            "board": _text(board), "bios": _text(bios), "kind": kind,
            # a model that only repeats the maker ("CWL" "CWL") names no model either
            "placeholder": is_placeholder(maker) or is_placeholder(model) or maker.lower() == model.lower(),
            "source": source}


# ── NVIDIA ──────────────────────────────────────────────────────────────────

def parse_nvidia_smi(text: str) -> List[dict]:
    """Lines of `nvidia-smi --query-gpu=<NV_FIELDS> --format=csv,noheader,nounits`."""
    out = []
    for line in (text or "").splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) != len(NV_FIELDS) or not cells[0]:
            continue
        values = dict(zip(NV_FIELDS, [None if c.startswith("[") or c in ("N/A", "") else c for c in cells]))
        pci = _int(values["pci.device_id"])                     # 0xDDDDVVVV: device << 16 | vendor
        out.append({"name": values["name"], "driver": values["driver_version"],
                    "vendor_id": f"{pci & 0xFFFF:04x}" if pci is not None else None,
                    "device_id": f"{pci >> 16:04x}" if pci is not None else None,
                    "subsys_id": (values["pci.sub_device_id"] or "").lower().replace("0x", "") or None,
                    "vram_mb": _int(_float(values["memory.total"])),
                    "power_limit_w": _float(values["enforced.power.limit"]),
                    "power_default_w": _float(values["power.default_limit"]),
                    "power_max_w": _float(values["power.max_limit"]),
                    "source": "nvidia-smi"})
    return out


def nvidia_gpus(run: Callable = subprocess.run) -> List[dict]:
    from app.telemetry.sources import _nvidia_smi_path
    exe = _nvidia_smi_path()
    if not exe:
        return []
    try:
        res = run([exe, "--query-gpu=" + ",".join(NV_FIELDS), "--format=csv,noheader,nounits"],
                  capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_nvidia_smi(res.stdout) if res.returncode == 0 else []


def merge_nvidia(gpus: List[dict], nvidia: List[dict]) -> List[dict]:
    """nvidia-smi's driver, VRAM and power limit replace what the OS gave for the same card (by PCI ids)."""
    left = list(nvidia)
    for gpu in gpus:
        match = next((n for n in left if n["device_id"] and n["device_id"] == gpu.get("device_id")
                      and n["vendor_id"] == gpu.get("vendor_id")), None)
        if match:
            left.remove(match)
            gpu.update({k: v for k, v in match.items()
                        if v is not None and k != "source" and (k != "name" or not gpu.get("name"))})
            gpu["source"] = gpu.get("source", "") + " + nvidia-smi"
    return gpus + left


# ── Windows ─────────────────────────────────────────────────────────────────

PNP = re.compile(r"VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})(?:&SUBSYS_([0-9A-F]{8}))?", re.I)


def parse_windows(data: dict) -> dict:
    """The JSON of PS_SCRIPT into the saved form."""
    cs = data.get("cs") or {}
    board = data.get("board") or {}
    chassis = [c for c in (_int(x) for x in _list(data.get("chassis"))) if c is not None]
    device = _device(cs.get("Manufacturer"), cs.get("Model"), cs.get("SystemFamily"), board.get("Manufacturer"),
                     board.get("Product"), data.get("bios"), kind_of(chassis, _int(cs.get("PCSystemType")) == 2),
                     "Win32_ComputerSystem, Win32_BaseBoard, Win32_SystemEnclosure, Win32_BIOS")
    cpus = _list(data.get("cpu"))
    c = cpus[0] if cpus else {}
    cpu = {"name": _text(c.get("Name")), "vendor": _text(c.get("Manufacturer")), "sockets": len(cpus) or None,
           "cores": sum(_int(x.get("NumberOfCores")) or 0 for x in cpus) or None,
           "threads": sum(_int(x.get("NumberOfLogicalProcessors")) or 0 for x in cpus) or None,
           "max_mhz": _int(c.get("MaxClockSpeed")), "source": "Win32_Processor"}
    gpus = []
    for g in _list(data.get("gpu")):
        m = PNP.search(g.get("PNPDeviceID") or "")
        ram = _int(g.get("AdapterRAM"))
        gpus.append({"name": _text(g.get("Name")), "driver": _text(g.get("DriverVersion")),
                     "vendor_id": m.group(1).lower() if m else None, "device_id": m.group(2).lower() if m else None,
                     "subsys_id": (m.group(3) or "").lower() or None if m else None,
                     # AdapterRAM is 32-bit: 4 GB and more reads wrong, so only a smaller value is kept
                     "vram_mb": ram // 2 ** 20 if ram and 0 < ram < 2 ** 32 - 2 ** 20 else None,
                     "power_limit_w": None, "power_default_w": None, "power_max_w": None, "source": "Win32_VideoController"})
    modules = [{"size_gb": round((_int(r.get("Capacity")) or 0) / 2 ** 30, 1) or None,
                "speed_mts": _int(r.get("Speed")), "configured_mts": _int(r.get("ConfiguredClockSpeed")),
                "maker": _text(r.get("Manufacturer")), "part": _text(r.get("PartNumber")),
                "slot": _text(r.get("DeviceLocator"))} for r in _list(data.get("ram"))]
    ram = {"total_gb": round(sum(m["size_gb"] or 0 for m in modules), 1) or None, "modules": modules,
           "source": "Win32_PhysicalMemory"}
    data_disk = _text(data.get("data_disk"))
    drives = []
    for d in _list(data.get("disks")):
        media, bus = d.get("MediaType"), d.get("BusType")
        bus = WIN_BUS.get(bus, str(bus)) if isinstance(bus, int) else _text(bus)
        media = WIN_MEDIA.get(media) if isinstance(media, int) else (_text(media) or "").lower() or None
        size = _int(d.get("Size"))
        drives.append({"id": _text(d.get("DeviceId")), "model": _text(d.get("FriendlyName")),
                       "kind": "nvme" if bus == "NVMe" else media, "bus": bus,
                       "size_gb": round(size / 1e9) if size else None, "firmware": _text(d.get("FirmwareVersion")),
                       "holds_app_data": data_disk is not None and _text(d.get("DeviceId")) == data_disk,
                       "source": "Get-PhysicalDisk"})
    osd = data.get("os") or {}
    return {"device": device, "cpu": cpu, "gpus": gpus, "ram": ram, "drives": drives,
            "os": {"name": _text(osd.get("Caption")), "version": _text(osd.get("Version")),
                   "build": _text(osd.get("BuildNumber")), "kernel": None, "source": "Win32_OperatingSystem"},
            "unit_id": unit_id(data.get("guid"))}


def windows_capture(data_path: Path, run: Callable = subprocess.run) -> dict:
    letter = (str(data_path.resolve())[:1] or "C").upper()
    script = PS_SCRIPT.replace("__LETTER__", letter if letter.isalpha() else "C")
    res = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
              capture_output=True, text=True, timeout=60)
    if res.returncode != 0 or not res.stdout.strip():
        raise RuntimeError(f"PowerShell exit code {res.returncode}: {(res.stderr or '').strip()[:200]}")
    return parse_windows(json.loads(res.stdout))


# ── Linux ───────────────────────────────────────────────────────────────────

def _read(path, root: Optional[Path] = None) -> Optional[str]:
    """A small text file, under `root` (tests) when given."""
    try:
        return (root / str(path).lstrip("/") if root is not None else Path(path)).read_text(encoding="utf-8", errors="replace").strip("\x00\n ") or None
    except OSError:
        return None


def _cpuinfo(text: str) -> dict:
    keys, physical = {}, set()
    threads = 0
    core = None
    for line in (text or "").splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "processor":
            threads += 1
        elif key in ("model name", "vendor_id", "Hardware", "Model", "CPU implementer") and key not in keys:
            keys[key] = value
        elif key == "physical id":
            core = value
        elif key == "core id":
            physical.add((core, value))
    return {"name": keys.get("model name") or keys.get("Model") or keys.get("Hardware"),
            "vendor": keys.get("vendor_id") or ({"0x41": "ARM", "0x51": "Qualcomm", "0x61": "Apple"}
                                                .get(keys.get("CPU implementer", ""))),
            "cores": len(physical) or None, "threads": threads or None}


def lspci_name(vendor: Optional[str], device: Optional[str], run: Callable = subprocess.run) -> Optional[str]:
    """The card's name from `lspci -mm -d VVVV:DDDD` (pciutils): its maker and device fields."""
    if not vendor or not device:
        return None
    try:
        res = run(["lspci", "-mm", "-d", f"{vendor}:{device}"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    fields = re.findall(r'"([^"]*)"', (res.stdout or "").splitlines()[0] if res.stdout else "")
    if res.returncode != 0 or len(fields) < 3:
        return None
    return f"{fields[1]} {fields[2]}".strip() or None


def _block_of(path: Path, root: Path) -> Optional[str]:
    """The whole disk (sda, nvme0n1) that holds `path`: its device number, up from a partition or a device-mapper
    volume (LVM, LUKS) to the disk under it."""
    try:
        st = os.stat(path)
        node = root / f"sys/dev/block/{os.major(st.st_dev)}:{os.minor(st.st_dev)}"
    except (OSError, AttributeError):            # os.major is Linux only
        return None
    for _ in range(4):
        try:
            node = node.resolve()
        except OSError:
            return None
        if (node / "partition").exists():
            node = node.parent
            continue
        slaves = sorted((node / "slaves").glob("*")) if (node / "slaves").is_dir() else []
        if slaves:
            node = slaves[0]
            continue
        return node.name if node.exists() else None
    return None


def linux_capture(data_path: Path, root: Path = Path("/"), run: Callable = subprocess.run) -> dict:
    dmi = "sys/class/dmi/id/"
    tree = _read("proc/device-tree/model", root)
    chassis = [c for c in [_int(_read(dmi + "chassis_type", root))] if c is not None]
    device = _device(_read(dmi + "sys_vendor", root), _read(dmi + "product_name", root) or tree,
                     _read(dmi + "product_family", root), _read(dmi + "board_vendor", root),
                     _read(dmi + "board_name", root), _read(dmi + "bios_version", root),
                     kind_of(chassis, arm_board=tree is not None),
                     "/sys/class/dmi/id" if _read(dmi + "sys_vendor", root) else "/proc/device-tree/model")
    if tree and device["maker"] is None:                    # e.g. "Raspberry Pi 5 Model B Rev 1.0"
        device["maker"] = tree.split()[0]
        device["placeholder"] = False
    cpu = dict(_cpuinfo(_read("proc/cpuinfo", root) or ""), sockets=None, max_mhz=None, source="/proc/cpuinfo")
    top = _int(_read("sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", root))
    cpu["max_mhz"] = top // 1000 if top else None
    try:
        import psutil
        cpu["cores"] = cpu["cores"] or psutil.cpu_count(logical=False)
    except Exception:
        pass
    m = re.search(r"MemTotal:\s+(\d+)", _read("proc/meminfo", root) or "")
    ram = {"total_gb": round(int(m.group(1)) / 2 ** 20, 1) if m else None, "modules": [],
           "source": "/proc/meminfo (module speed needs dmidecode as root: not read)"}
    gpus = []
    for card in sorted((root / "sys/class/drm").glob("card[0-9]*")):
        if "-" in card.name:
            continue
        dev = card / "device"
        uevent = _read(dev / "uevent") or ""
        driver = (re.search(r"^DRIVER=(\S+)", uevent, re.M) or [None, None])[1]
        vram = _int(_read(dev / "mem_info_vram_total"))
        sv, sd = _read(dev / "subsystem_vendor"), _read(dev / "subsystem_device")
        gpus.append({"name": None, "driver_name": driver,     # its version: nvidia-smi, or the kernel's for in-kernel
                     "driver": _read(f"sys/module/{driver}/version", root) if driver else None,
                     "vendor_id": (_read(dev / "vendor") or "").replace("0x", "") or None,
                     "device_id": (_read(dev / "device") or "").replace("0x", "") or None,
                     "subsys_id": (sd or "").replace("0x", "") + (sv or "").replace("0x", "") or None,
                     "vram_mb": vram // 2 ** 20 if vram else None, "power_limit_w": None, "power_default_w": None, "power_max_w": None,
                     "source": "/sys/class/drm"})
        gpus[-1]["name"] = lspci_name(gpus[-1]["vendor_id"], gpus[-1]["device_id"], run)
    disk = _block_of(data_path, root)
    drives = []
    for blk in sorted((root / "sys/block").glob("*")):
        if re.match(r"(loop|ram|zram|dm-|sr|md|fd|nbd)", blk.name):
            continue
        size = _int(_read(blk / "size"))
        rot = _read(blk / "queue/rotational")
        drives.append({"id": blk.name, "model": " ".join((_read(blk / "device/model") or "").split()) or None,
                       "kind": "nvme" if blk.name.startswith("nvme") else ("hdd" if rot == "1" else "ssd"
                                                                         if rot == "0" else None),
                       "bus": "NVMe" if blk.name.startswith("nvme") else ("MMC" if blk.name.startswith("mmc")
                                                                          else None),
                       "size_gb": round(size * 512 / 1e9) if size else None,
                       "firmware": _read(blk / "device/firmware_rev") or _read(blk / "device/rev"),
                       "holds_app_data": blk.name == disk, "source": "/sys/block"})
    rel = dict(re.findall(r'^(\w+)="?([^"\n]*)"?', _read("etc/os-release", root) or "", re.M))
    return {"device": device, "cpu": cpu, "gpus": gpus, "ram": ram, "drives": drives,
            "os": {"name": rel.get("PRETTY_NAME") or rel.get("NAME"), "version": rel.get("VERSION_ID"),
                   "build": None, "kernel": platform.release(), "source": "/etc/os-release"},
            "unit_id": unit_id(_read("etc/machine-id", root))}


def app_data_drive(hw: Optional[dict]) -> Optional[dict]:
    """The drive a disk test measures (the one holding the app's data), as saved with its result."""
    for d in (hw or {}).get("drives") or []:
        if d.get("holds_app_data"):
            return {k: d.get(k) for k in ("model", "kind", "bus", "size_gb", "firmware")}
    return None


# ── both ────────────────────────────────────────────────────────────────────

def capture(data_path: Optional[Path] = None, platform_name: str = sys.platform,
            run: Callable = subprocess.run, linux_root: Path = Path("/"), now: Optional[str] = None) -> dict:
    """The parts of this PC; never raises. `data_path` is the folder of the app's data: the disk test runs there,
    so its drive is marked holds_app_data."""
    if data_path is None:
        from app.paths import resolve_database_path
        data_path = Path(resolve_database_path()[0]).resolve().parent
    notes = []
    try:
        if platform_name == "win32":
            out = windows_capture(data_path, run)
        elif platform_name.startswith("linux"):
            out = linux_capture(data_path, linux_root, run)
        else:
            out = {}
    except Exception as e:
        logger.exception("Reading the PC's parts failed")
        out, notes = {}, [f"Not read: {e}"]
    if out:
        out["gpus"] = merge_nvidia(out.get("gpus", []), nvidia_gpus(run))
    return {"schema": SCHEMA, "captured_at": now or datetime.now().isoformat(timespec="seconds"), **out,
            "notes": notes}
