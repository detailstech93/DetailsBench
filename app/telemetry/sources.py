"""Telemetry sources (Phase 7). Each one says honestly when it is not available.

- nvidia-smi: NVIDIA GPU temperature, SM and memory clocks, power and load (no admin rights;
  ships with the NVIDIA driver on Windows and Linux).
- LibreHardwareMonitor: CPU temperature, package power and core clocks, read from ITS OWN local
  web server (http://localhost:8085/data.json). The app never installs LibreHardwareMonitor or a
  kernel driver: CPU sensors on Windows need one (WinRing0 is flagged as vulnerable,
  CVE-2020-14979; LibreHardwareMonitor 0.9.5+ uses PawnIO). The user runs it and enables
  Options > Remote Web Server. Only a loopback address is ever read.
- psutil: CPU load and memory use everywhere; on Linux also CPU temperature (hwmon) and the
  live CPU clock. On Windows psutil.cpu_freq() is the static base clock, so it is not used.
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlparse

from app.telemetry.lhm import parse_lhm_data, pick
from app.telemetry.nvidia_smi import parse_query_output, query_args

Reading = Dict[str, Tuple[float, str]]
LHM_URL = "http://127.0.0.1:8085/data.json"
_CPU_HW_PREFIXES = ("/amdcpu", "/intelcpu", "/cpu")
_LINUX_CPU_CHIPS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal")


def _nvidia_smi_path() -> Optional[str]:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if sys.platform == "win32":
        candidate = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
        return candidate if os.path.isfile(candidate) else None
    return None


class NvidiaSmiSource:
    name = "nvidia-smi"

    def __init__(self, executable: Optional[str] = None, runner: Callable = subprocess.run):
        self.executable = executable if executable is not None else _nvidia_smi_path()
        self.runner = runner

    def _query(self):
        res = self.runner([self.executable] + query_args(), capture_output=True, text=True, timeout=10,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return parse_query_output(res.stdout if res.returncode == 0 else (res.stdout or res.stderr or ""))

    def available(self) -> Tuple[bool, str]:
        if not self.executable:
            return False, "nvidia-smi not found (no NVIDIA driver)"
        try:
            parsed = self._query()
        except Exception as e:
            return False, f"nvidia-smi failed: {e}"
        return (False, parsed.error) if parsed.error else (True, f"{len(parsed.gpus)} NVIDIA GPU(s)")

    def read(self) -> Reading:
        parsed = self._query()
        if parsed.error:
            raise RuntimeError(parsed.error)
        out: Reading = {}
        for i, gpu in enumerate(parsed.gpus):
            n = gpu.index if gpu.index is not None else i
            for key, value, unit in (("temperature", gpu.temperature_c, "C"), ("sm_clock", gpu.sm_clock_mhz, "MHz"),
                                     ("mem_clock", gpu.mem_clock_mhz, "MHz"), ("power", gpu.power_w, "W"),
                                     ("load", gpu.utilization_pct, "%")):
                if value is not None:
                    out[f"gpu{n}_{key}"] = (value, unit)
        return out


def is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")


def lhm_cpu_reading(sensors) -> Reading:
    """Hottest CPU temperature sensor, CPU package power, average of the per-core clocks."""
    out: Reading = {}
    temps = [s.value for p in _CPU_HW_PREFIXES for s in pick(sensors, "Temperature", p)]
    if temps:
        out["cpu_temperature_hottest"] = (max(temps), "C")
    power = [s.value for p in _CPU_HW_PREFIXES for s in pick(sensors, "Power", p, "package")]
    if power:
        out["cpu_package_power"] = (power[0], "W")
    clocks = [s.value for p in _CPU_HW_PREFIXES for s in pick(sensors, "Clock", p, "core")
              if "bus" not in s.name.lower() and s.value > 0]
    if clocks:
        out["cpu_core_clock_avg"] = (sum(clocks) / len(clocks), "MHz")
    return out


class LhmSource:
    name = "LibreHardwareMonitor"

    def __init__(self, url: str = LHM_URL, opener: Callable = urllib.request.urlopen):
        self.url = url
        self.opener = opener

    def _sensors(self):
        if not is_loopback_url(self.url):
            raise ValueError(f"only a local LibreHardwareMonitor address is read, not {self.url}")
        with self.opener(self.url, timeout=3) as resp:
            parsed = parse_lhm_data(json.loads(resp.read().decode("utf-8")))
        if not parsed.sensors:
            raise RuntimeError(parsed.error or "no sensors")
        return parsed.sensors

    def available(self) -> Tuple[bool, str]:
        try:
            reading = lhm_cpu_reading(self._sensors())
        except Exception as e:
            return False, (f"LibreHardwareMonitor web server not reachable at {self.url} ({e}); run "
                           "LibreHardwareMonitor and enable Options > Remote Web Server")
        if not reading:
            return False, "LibreHardwareMonitor shows no CPU sensors (its driver may be missing)"
        return True, ", ".join(sorted(reading))

    def read(self) -> Reading:
        return lhm_cpu_reading(self._sensors())


class PsutilSource:
    name = "psutil"

    def __init__(self, psutil_module=None, platform_name: str = sys.platform):
        if psutil_module is None:
            import psutil as psutil_module
        self.ps = psutil_module
        self.platform_name = platform_name

    def available(self) -> Tuple[bool, str]:
        self.ps.cpu_percent(interval=None)       # the first call only starts the measurement
        return True, "CPU load and memory" + (", CPU temperature and clock" if self._linux() else "")

    def _linux(self) -> bool:
        return self.platform_name.startswith("linux")

    def read(self) -> Reading:
        out: Reading = {"cpu_load": (float(self.ps.cpu_percent(interval=None)), "%"),
                        "ram_used": (float(self.ps.virtual_memory().percent), "%")}
        if self._linux():
            temps = []
            for chip, entries in (getattr(self.ps, "sensors_temperatures", lambda: {})() or {}).items():
                if chip in _LINUX_CPU_CHIPS:
                    temps += [e.current for e in entries if e.current is not None and e.current > 0]
            if temps:
                out["cpu_temperature_hottest"] = (float(max(temps)), "C")
            freq = self.ps.cpu_freq()
            if freq is not None and freq.current and freq.current > 0:
                out["cpu_clock_avg"] = (float(freq.current), "MHz")
        return out


def default_sources(platform_name: str = sys.platform) -> list:
    sources = [PsutilSource(platform_name=platform_name), NvidiaSmiSource()]
    if platform_name == "win32":
        sources.append(LhmSource())
    return sources
