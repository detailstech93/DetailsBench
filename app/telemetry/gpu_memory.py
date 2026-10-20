"""Graphics memory of every GPU (NVIDIA, AMD, Intel, integrated), for the AI tests (owner, 2026-10-20).

- Windows: the GPU Adapter Memory performance counters (Dedicated Usage, Shared Usage, in bytes, one instance per
  adapter, named luid_0x<high>_0x<low>_phys_<n>) through PDH, the same numbers Task Manager shows, for any vendor's
  WDDM 2 driver and without admin rights. The adapter's name and memory sizes come from DXGI (EnumAdapters1 +
  GetDesc1, matched on the adapter LUID). The counter path is added with PdhAddEnglishCounterW, so a Windows in
  another language finds it too.
- Linux: amdgpu's sysfs files (mem_info_vram_used / _total, mem_info_gtt_used / _total, in bytes). Other Linux
  drivers (Intel i915/xe, nouveau) give no such file: there an integrated GPU's memory is the PC's RAM, which is
  always recorded.
- macOS: one memory for the CPU and the GPU (unified), which is the RAM reading.

Keys (one set per GPU, the GPU with the most dedicated memory first): gpumem{n}_dedicated (GiB), gpumem{n}_dedicated_pct
(% of its dedicated memory), gpumem{n}_shared (GiB of the PC's RAM the GPU uses: an integrated GPU works in it, and a
graphics card spills into it when its own memory is full). Software adapters (Microsoft Basic Render Driver) are left
out.
"""

import ctypes
import os
import sys
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

Reading = Dict[str, Tuple[float, str]]
GIB = 2 ** 30
MICROSOFT_VENDOR = 0x1414          # Microsoft Basic Render Driver / WARP
DXGI_ADAPTER_FLAG_SOFTWARE = 2


@dataclass
class Adapter:
    luid: Tuple[int, int]          # (high, low)
    name: str
    vendor_id: int
    dedicated_total: int           # bytes
    shared_total: int              # bytes


def gib(value: float) -> float:
    return round(value / GIB, 2)


def instance_luid(instance: str) -> Optional[Tuple[int, int]]:
    """'luid_0x00000000_0x0000D1E6_phys_0' -> (0, 0xD1E6)."""
    parts = instance.lower().split("_")
    if len(parts) < 3 or parts[0] != "luid":
        return None
    try:
        return int(parts[1], 16), int(parts[2], 16)
    except ValueError:
        return None


def windows_reading(adapters: List[Adapter], dedicated: Dict[str, float], shared: Dict[str, float]) -> Reading:
    """Adapters (from DXGI) and the counters' {instance: bytes} -> readings, the biggest dedicated memory first."""
    def total(values, luid):
        found = [v for inst, v in values.items() if instance_luid(inst) == luid]
        return sum(found) if found else None

    real = [a for a in adapters if a.vendor_id != MICROSOFT_VENDOR]
    real.sort(key=lambda a: -a.dedicated_total)
    out: Reading = {}
    for n, a in enumerate(real):
        used = total(dedicated, a.luid)
        if used is not None:
            out[f"gpumem{n}_dedicated"] = (gib(used), "GiB")
            if a.dedicated_total > 0:
                out[f"gpumem{n}_dedicated_pct"] = (round(100 * used / a.dedicated_total, 1), "%")
                out[f"gpumem{n}_dedicated_total"] = (gib(a.dedicated_total), "GiB")
        spill = total(shared, a.luid)
        if spill is not None:
            out[f"gpumem{n}_shared"] = (gib(spill), "GiB")
    return out


# ---- Windows: DXGI (adapter names and sizes) -------------------------------------------------------------------
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


class _DESC1(ctypes.Structure):
    _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint), ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                ("LuidLow", ctypes.c_ulong), ("LuidHigh", ctypes.c_long), ("Flags", ctypes.c_uint)]


IID_IDXGIFactory1 = "770aae78-f26f-4dba-a829-253c83d1b387"
DXGI_ERROR_NOT_FOUND = 0x887A0002


def _method(obj, index: int, *argtypes):
    """The COM method at vtable index of obj (an interface pointer), returning an HRESULT."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return lambda *args: proto(vtable[index])(obj, *args)


def dxgi_adapters() -> List[Adapter]:
    """Every adapter DXGI lists (IDXGIFactory1::EnumAdapters1 = vtable 12, IDXGIAdapter1::GetDesc1 = 10,
    IUnknown::Release = 2)."""
    dxgi = ctypes.WinDLL("dxgi")
    iid = _GUID.from_buffer_copy(uuid.UUID(IID_IDXGIFactory1).bytes_le)
    factory = ctypes.c_void_p()
    hr = dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory))
    if hr != 0 or not factory.value:
        raise OSError(f"CreateDXGIFactory1 failed: 0x{hr & 0xFFFFFFFF:08X}")
    out = []
    try:
        for i in range(16):
            adapter = ctypes.c_void_p()
            hr = _method(factory, 12, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(i, ctypes.byref(adapter))
            if hr & 0xFFFFFFFF == DXGI_ERROR_NOT_FOUND or not adapter.value:
                break
            try:
                desc = _DESC1()
                if _method(adapter, 10, ctypes.POINTER(_DESC1))(ctypes.byref(desc)) == 0 \
                        and not desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE:
                    out.append(Adapter((desc.LuidHigh & 0xFFFFFFFF, desc.LuidLow & 0xFFFFFFFF), desc.Description,
                                       desc.VendorId, int(desc.DedicatedVideoMemory), int(desc.SharedSystemMemory)))
            finally:
                _method(adapter, 2)()
    finally:
        _method(factory, 2)()
    return out


# ---- Windows: PDH (memory in use) ------------------------------------------------------------------------------
PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA = 0x800007D2
COUNTERS = {"dedicated": r"\GPU Adapter Memory(*)\Dedicated Usage", "shared": r"\GPU Adapter Memory(*)\Shared Usage"}


class _FmtValue(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_ulong), ("doubleValue", ctypes.c_double)]


class _FmtItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _FmtValue)]


class PdhCounters:
    """One PDH query with the two GPU Adapter Memory counters; read() -> {"dedicated": {instance: bytes}, ...}."""

    def __init__(self):
        self.pdh = ctypes.WinDLL("pdh")
        self.query = ctypes.c_void_p()
        status = self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query))
        if status != 0:
            raise OSError(f"PdhOpenQueryW failed: 0x{status & 0xFFFFFFFF:08X}")
        self.counters = {}
        for key, path in COUNTERS.items():
            handle = ctypes.c_void_p()
            status = self.pdh.PdhAddEnglishCounterW(self.query, ctypes.c_wchar_p(path), None, ctypes.byref(handle))
            if status != 0:
                self.close()
                raise OSError(f"the counter {path} is not on this PC (0x{status & 0xFFFFFFFF:08X})")
            self.counters[key] = handle

    def _values(self, handle) -> Dict[str, float]:
        size, count = ctypes.c_ulong(0), ctypes.c_ulong(0)
        status = self.pdh.PdhGetFormattedCounterArrayW(handle, PDH_FMT_DOUBLE, ctypes.byref(size),
                                                       ctypes.byref(count), None)
        if status & 0xFFFFFFFF != PDH_MORE_DATA or not size.value:
            return {}
        buf = ctypes.create_string_buffer(size.value)
        status = self.pdh.PdhGetFormattedCounterArrayW(handle, PDH_FMT_DOUBLE, ctypes.byref(size),
                                                       ctypes.byref(count), buf)
        if status != 0:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(_FmtItem))
        return {items[i].szName: items[i].FmtValue.doubleValue for i in range(count.value)
                if items[i].FmtValue.CStatus in (0, 1)}       # PDH_CSTATUS_VALID_DATA / NEW_DATA

    def read(self) -> Dict[str, Dict[str, float]]:
        status = self.pdh.PdhCollectQueryData(self.query)
        if status != 0:
            raise OSError(f"PdhCollectQueryData failed: 0x{status & 0xFFFFFFFF:08X}")
        return {key: self._values(handle) for key, handle in self.counters.items()}

    def close(self):
        if self.query:
            self.pdh.PdhCloseQuery(self.query)
            self.query = ctypes.c_void_p()


# ---- Linux: amdgpu sysfs ---------------------------------------------------------------------------------------
DRM_ROOT = "/sys/class/drm"


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, encoding="ascii") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def linux_reading(root: str = DRM_ROOT, listdir: Callable = os.listdir,
                  read_int: Callable[[str], Optional[int]] = _read_int) -> Reading:
    cards = []
    try:
        names = sorted(listdir(root))
    except OSError:
        return {}
    for name in names:
        if not name.startswith("card") or not name[4:].isdigit():          # card0, not card0-DP-1
            continue
        dev = f"{root}/{name}/device"
        vram_total = read_int(f"{dev}/mem_info_vram_total")
        if vram_total is None:
            continue
        cards.append((vram_total, read_int(f"{dev}/mem_info_vram_used"), read_int(f"{dev}/mem_info_gtt_used")))
    cards.sort(key=lambda c: -c[0])
    out: Reading = {}
    for n, (total, used, gtt) in enumerate(cards):
        if used is not None:
            out[f"gpumem{n}_dedicated"] = (gib(used), "GiB")
            if total:
                out[f"gpumem{n}_dedicated_pct"] = (round(100 * used / total, 1), "%")
                out[f"gpumem{n}_dedicated_total"] = (gib(total), "GiB")
        if gtt is not None:
            out[f"gpumem{n}_shared"] = (gib(gtt), "GiB")
    return out


class GpuMemorySource:
    name = "GPU memory"

    def __init__(self, platform_name: str = sys.platform, adapters: Callable[[], List[Adapter]] = None,
                 counters: Callable[[], object] = None, linux: Callable[[], Reading] = linux_reading):
        self.platform_name = platform_name
        self._adapters_fn = adapters or dxgi_adapters
        self._counters_fn = counters or PdhCounters
        self._linux = linux
        self._adapters: List[Adapter] = []
        self._counters = None

    def available(self) -> Tuple[bool, str]:
        if self.platform_name == "win32":
            try:
                self._adapters = [a for a in self._adapters_fn() if a.vendor_id != MICROSOFT_VENDOR]
                if not self._adapters:
                    return False, "no graphics adapter listed by DXGI"
                self._counters = self._counters_fn()
            except Exception as e:
                return False, f"GPU memory counters could not be opened: {e}"
            ordered = sorted(self._adapters, key=lambda a: -a.dedicated_total)
            return True, "; ".join(f"gpumem{n}: {a.name} ({gib(a.dedicated_total)} GiB dedicated)"
                                   for n, a in enumerate(ordered))
        if self.platform_name.startswith("linux"):
            reading = self._linux()
            if not reading:
                return False, ("no amdgpu memory files (other Linux GPU drivers do not give one; an integrated GPU "
                               "uses the RAM, which is recorded)")
            return True, "amdgpu memory"
        return False, "the GPU uses the PC's RAM on this system (unified memory): see the RAM reading"

    def read(self) -> Reading:
        if self.platform_name == "win32":
            values = self._counters.read()
            return windows_reading(self._adapters, values.get("dedicated") or {}, values.get("shared") or {})
        return self._linux()
