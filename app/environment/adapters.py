"""PlatformAdapter: the only place that branches on the operating system.

select_adapter() picks WindowsAdapter or LinuxAdapter once; build_profile()
runs every detection step isolated (try/except + timing), so one failing
step is recorded in diagnostics and never aborts the profile.
Nothing here changes the system: reads only.
"""

import logging
import os
import platform
import sys
import sysconfig
import time
from typing import Callable, Dict, List, Optional, Tuple

from app.environment.arch import normalize_arch
from app.environment.profile import (
    EnvironmentProfile, ProfileField, detected, unavailable, unverified,
)

logger = logging.getLogger(__name__)

# IMAGE_FILE_MACHINE_* values returned by IsWow64Process2
_IMAGE_FILE_MACHINE = {0x8664: "x64", 0x014C: "x86", 0xAA64: "arm64"}

UIPI_NOTE = ("A non-elevated process is expected to be unable to automate the UI of an "
             "elevated window (UIPI); unverified.")

Step = Tuple[str, Callable[[], Dict[str, ProfileField]]]


def process_arch_from_platform_tag(tag: str) -> str:
    """Architecture of the running Python from sysconfig.get_platform()."""
    tag = tag.lower()
    if tag in ("win32",) or tag.endswith("-i686") or tag.endswith("-x86"):
        return "x86"
    return normalize_arch(tag.rsplit("-", 1)[-1])


def emulation_field(native: str, source: str) -> ProfileField:
    """Would x64 code run emulated? Only decidable when the native arch is known."""
    if native == "arm64":
        return detected(True, source)
    if native in ("x64", "x86"):
        return detected(False, source)
    return unavailable(source)


class PlatformAdapter:
    os_family = "other"

    def steps(self) -> List[Step]:
        return []

    def build_profile(self) -> EnvironmentProfile:
        profile = EnvironmentProfile()
        profile.os_family = detected(self.os_family, "platform.system")
        for name, step in self.steps():
            start = time.perf_counter()
            try:
                for field_name, value in step().items():
                    setattr(profile, field_name, value)
            except Exception as exc:
                logger.warning("environment step %s failed: %s", name, exc)
                profile.diagnostics.append(f"step {name} failed: {exc}")
            profile.step_seconds[name] = round(time.perf_counter() - start, 3)
        return profile

    # Shared steps (reuse app/system_info, do not duplicate it)
    def _hardware(self) -> Dict[str, ProfileField]:
        from app.system_info import get_cpu_brand, get_gpu_names, get_ram_size
        cpu = get_cpu_brand()
        ram = get_ram_size()
        gpus = get_gpu_names()
        return {
            "cpu": detected(cpu, "system_info.get_cpu_brand") if cpu else unavailable("system_info"),
            "ram_gb": detected(ram[0], f"system_info.get_ram_size ({ram[1]})") if ram else unavailable("system_info"),
            "gpu": detected(gpus, "system_info.get_gpu_names") if gpus else unavailable("system_info"),
        }


class WindowsAdapter(PlatformAdapter):
    os_family = "windows"

    def __init__(self, system_root: Optional[str] = None):
        self.system_root = system_root or os.environ.get("SystemRoot")

    def steps(self) -> List[Step]:
        return [
            ("os", self._os),
            ("architecture", self._architecture),
            ("elevation", self._elevation),
            ("hardware", self._hardware),
            ("graphics", self._graphics),
            ("not_linux", self._not_linux),
        ]

    def _os(self) -> Dict[str, ProfileField]:
        import winreg
        build = sys.getwindowsversion().build
        values = {}
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            for name in ("DisplayVersion", "UBR", "EditionID"):
                try:
                    values[name] = winreg.QueryValueEx(key, name)[0]
                except OSError:
                    values[name] = None
        return {
            "os_name": detected(windows_name_from_build(build, values["EditionID"]),
                                "sys.getwindowsversion build (registry ProductName is unreliable)"),
            "os_version": (detected(values["DisplayVersion"], "registry DisplayVersion")
                           if values["DisplayVersion"] else unavailable("registry DisplayVersion")),
            "os_build": detected(f"{build}.{values['UBR']}" if values["UBR"] is not None else str(build),
                                 "sys.getwindowsversion + registry UBR"),
            "is_wsl": detected(False, "running on Windows"),
            "is_virtual_machine": unavailable("no reliable non-invasive method"),
        }

    def _architecture(self) -> Dict[str, ProfileField]:
        import ctypes
        from ctypes import wintypes
        process = process_arch_from_platform_tag(sysconfig.get_platform())
        result = {"process_arch": detected(process, "sysconfig.get_platform")}
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = getattr(kernel32, "IsWow64Process2", None)
        if fn is not None:
            fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.USHORT),
                           ctypes.POINTER(wintypes.USHORT)]
            fn.restype = wintypes.BOOL
            proc_m, native_m = wintypes.USHORT(), wintypes.USHORT()
            if fn(kernel32.GetCurrentProcess(), ctypes.byref(proc_m), ctypes.byref(native_m)):
                native = _IMAGE_FILE_MACHINE.get(native_m.value, "unknown")
                result["native_arch"] = detected(native, "IsWow64Process2")
                result["x64_emulated"] = emulation_field(native, "IsWow64Process2")
                return result
        # Fallback: environment variables (WOW64 sets PROCESSOR_ARCHITEW6432)
        raw = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE")
        native = normalize_arch(raw)
        result["native_arch"] = (unverified(native, "PROCESSOR_ARCHITECTURE env var")
                                 if native != "unknown" else unavailable("PROCESSOR_ARCHITECTURE"))
        result["x64_emulated"] = emulation_field(native, "PROCESSOR_ARCHITECTURE env var")
        return result

    def _elevation(self) -> Dict[str, ProfileField]:
        import ctypes
        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
        return {"app_is_elevated": detected(elevated, "shell32.IsUserAnAdmin; " + UIPI_NOTE)}

    def _graphics(self) -> Dict[str, ProfileField]:
        """Runtime presence only (System32 DLLs); driver/feature support is not probed."""
        if not self.system_root:
            raise RuntimeError("SystemRoot environment variable missing")
        sys32 = os.path.join(self.system_root, "System32")
        checks = {
            "directx": ("d3d12.dll", "Direct3D 12 runtime present"),
            "vulkan": ("vulkan-1.dll", "Vulkan loader present (GPU driver support not checked)"),
            "opengl": ("opengl32.dll", "OpenGL runtime present (driver version not checked)"),
        }
        out = {}
        for key, (dll, text) in checks.items():
            source = f"file exists: System32\\{dll}"
            if os.path.isfile(os.path.join(sys32, dll)):
                # Loader/runtime presence does not prove the GPU driver supports the API
                out[key] = detected(text, source) if key == "directx" else unverified(text, source)
            else:
                out[key] = unavailable(source)
        return out

    def _not_linux(self) -> Dict[str, ProfileField]:
        return {"display_server": unavailable("not Linux")}


def windows_name_from_build(build: int, edition: Optional[str] = None) -> str:
    """Windows 11 reports itself as "Windows 10" in the registry; use the build."""
    name = "Windows 11" if build >= 22000 else "Windows 10" if build >= 10240 else "Windows"
    return f"{name} {edition}" if edition else name


def select_adapter(system: Optional[str] = None) -> PlatformAdapter:
    system = (system or platform.system()).lower()
    if system == "windows":
        return WindowsAdapter()
    if system == "linux":
        from app.environment.linux_adapter import LinuxAdapter
        return LinuxAdapter()
    return PlatformAdapter()
