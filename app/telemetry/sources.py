"""Telemetry sources (Phase 7). Each one says honestly when it is not available.

- nvidia-smi: NVIDIA GPU temperature, SM and memory clocks, power and load (no admin rights;
  ships with the NVIDIA driver on Windows and Linux).
- LibreHardwareMonitor: CPU temperature, package power and core clocks, read from ITS OWN local
  web server (http://127.0.0.1:8085/data.json). The app can download it and turn its web server
  on with a random password (app/telemetry/lhm_provision.py); it never installs a kernel driver
  itself (LibreHardwareMonitor 0.9.5+ installs PawnIO with its own prompt; the older WinRing0 is
  flagged as vulnerable, CVE-2020-14979). For the copy this app set up, the port and the password
  are read from its settings file on every request, because that file always holds the password
  the running server expects. Only a loopback address is ever read.
- GPU memory (app/telemetry/gpu_memory.py): dedicated and shared memory in use of every GPU, any vendor,
  integrated ones too (Windows performance counters; amdgpu on Linux).
- psutil: CPU load and memory use everywhere; on Linux also CPU temperature (hwmon) and the
  live CPU clock. On Windows psutil.cpu_freq() is the static base clock, so it is not used.
- RAPL (Linux): CPU package power from the kernel's energy counters
  (/sys/class/powercap/intel-rapl:N, also used for AMD). Since kernel 5.10 only root may read
  them (CVE-2020-8694); then the source says so and the reading is absent, never 0.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlparse

from app.telemetry.gpu_memory import GpuMemorySource
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
            if gpu.vram_used_mib is not None:
                out[f"gpu{n}_vram_used"] = (round(gpu.vram_used_mib / 1024, 2), "GiB")
                if gpu.vram_total_mib:
                    out[f"gpu{n}_vram_used_pct"] = (round(100 * gpu.vram_used_mib / gpu.vram_total_mib, 1), "%")
                    out[f"gpu{n}_vram_total"] = (round(gpu.vram_total_mib / 1024, 2), "GiB")
        return out


def is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")


def lhm_cpu_reading(sensors) -> Reading:
    """Hottest CPU temperature sensor, CPU package power, average of the per-core clocks.

    Intel's "... Distance to TjMax" sensors are typed Temperature but are a headroom, not a
    temperature (57 at idle), so they are left out of the hottest.
    """
    out: Reading = {}
    temps = [s.value for p in _CPU_HW_PREFIXES for s in pick(sensors, "Temperature", p)
             if "distance" not in s.name.lower() and "tjmax" not in s.name.lower()]
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


class LhmDataError(RuntimeError):
    """The web server answered, but not with sensors we can read."""


class LhmAuthError(RuntimeError):
    """The web server wants a password that is not the one in the settings file we know."""


class LhmSource:
    name = "LibreHardwareMonitor"

    def __init__(self, url: Optional[str] = None, opener: Callable = urllib.request.urlopen,
                 settings: Callable[[], dict] = dict):
        """settings() -> the LibreHardwareMonitor.config values of the copy this app set up ({} when none):
        its port is used when no url is given, and its password when authentication is on."""
        self._url = url
        self.opener = opener
        self.settings = settings

    @property
    def url(self) -> str:
        if self._url is not None:
            return self._url
        from app.telemetry.lhm_files import web_port
        return f"http://127.0.0.1:{web_port(self._settings())}/data.json"

    def _settings(self) -> dict:
        try:
            return self.settings() or {}
        except Exception:
            return {}

    def _request(self, url: str):
        from app.telemetry.lhm_files import web_auth
        req = urllib.request.Request(url, headers={"User-Agent": "PC-Benchmark-Manager"})
        auth = web_auth(self._settings())
        if auth is not None:
            token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode("utf-8")).decode("ascii")
            req.add_header("Authorization", f"Basic {token}")
        return req

    def _sensors(self):
        url = self.url
        if not is_loopback_url(url):
            raise ValueError(f"only a local LibreHardwareMonitor address is read, not {url}")
        try:
            with self.opener(self._request(url), timeout=3) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise LhmAuthError("it asks for a password that is not the one in the settings of the copy this "
                                   "app set up (another copy may be running); close it (tray icon > Exit) and the next "
                                   "run with Record telemetry starts this app's copy")
            raise
        try:
            data = json.loads(body.decode("utf-8-sig"))
        except ValueError as e:
            raise LhmDataError(f"data.json is not valid JSON: {e}")
        parsed = parse_lhm_data(data)
        if not parsed.sensors:
            raise LhmDataError(parsed.error or "no sensors")
        return parsed.sensors

    def available(self) -> Tuple[bool, str]:
        try:
            reading = lhm_cpu_reading(self._sensors())
        except LhmDataError as e:
            return False, f"LibreHardwareMonitor answered at {self.url} but its data could not be read: {e}"
        except LhmAuthError as e:
            return False, f"LibreHardwareMonitor at {self.url}: {e}"
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

    MIN_LOAD_WINDOW = 0.5                        # seconds; a shorter window is noise

    def __init__(self, psutil_module=None, platform_name: str = sys.platform, clock: Callable = time.monotonic):
        if psutil_module is None:
            import psutil as psutil_module
        self.ps = psutil_module
        self.platform_name = platform_name
        self.clock = clock
        self._load_since: Optional[float] = None
        self._times = None

    def _cpu_busy_since_last(self) -> Optional[float]:
        """CPU load from OUR OWN cpu_times() deltas. psutil.cpu_percent(None) keeps one window for the
        whole process, so any other caller (e.g. the diagnostic snapshot) would reset it and give a 0."""
        times = self.ps.cpu_times()
        before, self._times = self._times, times
        if before is None:
            return None
        total = sum(times) - sum(before)
        if total <= 0:
            return None
        idle = times.idle - before.idle
        return max(0.0, min(100.0, 100.0 * (1.0 - idle / total)))

    def available(self) -> Tuple[bool, str]:
        self._cpu_busy_since_last()              # the first call only starts the measurement
        self._load_since = self.clock()
        return True, "CPU load and memory" + (", CPU temperature and clock" if self._linux() else "")

    def _linux(self) -> bool:
        return self.platform_name.startswith("linux")

    def read(self) -> Reading:
        mem = self.ps.virtual_memory()
        out: Reading = {"ram_used": (float(mem.percent), "%")}
        used = getattr(mem, "total", 0) - getattr(mem, "available", 0)
        if getattr(mem, "total", 0) and used >= 0:
            out["ram_used_gib"] = (round(used / 2**30, 2), "GiB")
            out["ram_total_gib"] = (round(mem.total / 2**30, 2), "GiB")
        now = self.clock()
        if self._load_since is None or now - self._load_since >= self.MIN_LOAD_WINDOW:
            load = self._cpu_busy_since_last()   # load since the previous call
            if load is not None:
                out["cpu_load"] = (round(load, 1), "%")
            self._load_since = now
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


def new_sampler(interval_seconds: float = 1.0):
    """A fresh, not started sampler over the default sources (one per run)."""
    from app.telemetry.sampler import TelemetrySampler
    return TelemetrySampler(default_sources(), interval_seconds)


def new_memory_sampler(interval_seconds: float = 1.0):
    """RAM and GPU memory only: an AI test records them even with Record telemetry off (owner, 2026-10-20: the
    memory is an important reading of an AI test, on a graphics card, an integrated GPU or the CPU alone)."""
    from app.telemetry.sampler import TelemetrySampler
    return TelemetrySampler([PsutilSource(), GpuMemorySource()], interval_seconds)


RAPL_ROOT = "/sys/class/powercap"


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


class RaplSource:
    """CPU package power (Linux) from the RAPL energy counters: the change in energy_uj over time."""
    name = "RAPL"

    def __init__(self, root: str = RAPL_ROOT, clock: Callable = time.monotonic,
                 listdir: Callable[[str], list] = os.listdir, read_text: Optional[Callable[[str], str]] = None):
        self.root = root
        self.clock = clock
        self.listdir = listdir
        self.read_text = read_text or _read_text
        self._last = None                       # (time, {zone: energy_uj})

    def _zones(self) -> list:
        """intel-rapl:N package zones (not their intel-rapl:N:M sub-zones)."""
        zones = []
        try:
            names = sorted(self.listdir(self.root))
        except OSError:
            return zones
        for entry in names:
            parts = entry.split(":")
            if len(parts) != 2 or parts[0] != "intel-rapl" or not parts[1].isdigit():
                continue
            try:
                if self.read_text(f"{self.root}/{entry}/name").strip().startswith("package"):
                    zones.append(entry)
            except OSError:
                continue
        return zones

    def _counter(self, zone: str, item: str) -> int:
        return int(self.read_text(f"{self.root}/{zone}/{item}").strip())

    def available(self) -> Tuple[bool, str]:
        zones = self._zones()
        if not zones:
            return False, f"no RAPL package energy counters under {self.root}"
        try:
            for zone in zones:
                self._counter(zone, "energy_uj")
        except PermissionError:
            return False, ("RAPL energy counters are readable by root only on this kernel (CVE-2020-8694 fix); "
                           "CPU package power is not recorded")
        except (OSError, ValueError) as e:
            return False, f"RAPL energy counters could not be read: {e}"
        return True, f"CPU package power ({len(zones)} package(s))"

    def read(self) -> Reading:
        now = self.clock()
        energy = {zone: self._counter(zone, "energy_uj") for zone in self._zones()}
        last, self._last = self._last, (now, energy)
        if last is None or now - last[0] <= 0 or set(last[1]) != set(energy):
            return {}                                # the first reading only starts the window
        joules = 0
        for zone, value in energy.items():
            delta = value - last[1][zone]
            if delta < 0:                            # the counter wrapped around
                try:
                    delta += self._counter(zone, "max_energy_range_uj")
                except (OSError, ValueError):
                    return {}
            joules += delta / 1e6
        return {"cpu_package_power": (joules / (now - last[0]), "W")}


THERMAL_ROOT = "/sys/class/thermal"
DEVFREQ_ROOT = "/sys/class/devfreq"


class ThermalZoneSource:
    """Linux boards (ARM SBCs, Qualcomm): CPU and GPU temperature from the kernel's thermal zones and the GPU clock
    from devfreq, no root (owner, 2026-10-22: the Radxa Dragon Q8B's stress report had no GPU chart; psutil's hwmon
    chips did not name its CPU either). Zones "cpu..."/"cluster..." are the CPU, zones with "gpu" the GPU (gpuss-N on
    Qualcomm, gpu_thermal on Rockchip); a devfreq device with "gpu" in its name is the GPU's clock. Listed first, so
    psutil (hwmon CPU chips) and nvidia-smi win where they read the same thing."""
    name = "thermal zones"

    def __init__(self, thermal_root: str = THERMAL_ROOT, devfreq_root: str = DEVFREQ_ROOT,
                 listdir: Callable[[str], list] = os.listdir, read_text: Optional[Callable[[str], str]] = None):
        self.thermal_root, self.devfreq_root = thermal_root, devfreq_root
        self.listdir = listdir
        self.read_text = read_text or _read_text
        self._zones: Optional[list] = None          # [(path, "cpu" | "gpu")], read once
        self._gpu_freq: Optional[str] = None

    def _find(self):
        if self._zones is not None:
            return
        self._zones = []
        try:
            names = sorted(n for n in self.listdir(self.thermal_root) if n.startswith("thermal_zone"))
        except OSError:
            names = []
        for n in names:
            try:
                kind = self.read_text(f"{self.thermal_root}/{n}/type").strip().lower()
            except OSError:
                continue
            part = "gpu" if "gpu" in kind else "cpu" if kind.startswith(("cpu", "cluster")) else None
            if part:
                self._zones.append((f"{self.thermal_root}/{n}/temp", part))
        try:
            gpus = sorted(n for n in self.listdir(self.devfreq_root) if "gpu" in n.lower())
        except OSError:
            gpus = []
        self._gpu_freq = f"{self.devfreq_root}/{gpus[0]}/cur_freq" if gpus else None

    def available(self) -> Tuple[bool, str]:
        self._find()
        parts = sorted({part for _, part in self._zones})
        if not parts and not self._gpu_freq:
            return False, "no CPU or GPU thermal zone and no GPU devfreq clock"
        return True, ", ".join([f"{p.upper()} temperature" for p in parts] + (["GPU clock"] if self._gpu_freq
                                                                             else []))

    def read(self) -> Reading:
        self._find()
        temps = {"cpu": [], "gpu": []}
        for path, part in self._zones:
            try:
                value = int(self.read_text(path).strip()) / 1000.0
            except (OSError, ValueError):
                continue
            if 0 < value < 150:                 # -40.96 and the like: a zone with no sensor behind it
                temps[part].append(value)
        out: Reading = {}
        if temps["cpu"]:
            out["cpu_temperature_hottest"] = (max(temps["cpu"]), "C")
        if temps["gpu"]:
            out["gpu0_temperature"] = (max(temps["gpu"]), "C")
        if self._gpu_freq:
            try:
                out["gpu0_sm_clock"] = (int(self.read_text(self._gpu_freq).strip()) / 1e6, "MHz")
            except (OSError, ValueError):
                pass
        return out


def _provisioned_lhm_settings() -> dict:
    """LibreHardwareMonitor.config of the copy this app set up; {} when there is none."""
    from app.paths import get_data_dir
    from app.telemetry import lhm_provision
    exe = lhm_provision.find_local(get_data_dir())
    return lhm_provision.read_settings(exe).settings if exe else {}


def default_sources(platform_name: str = sys.platform) -> list:
    sources = [PsutilSource(platform_name=platform_name), NvidiaSmiSource(), GpuMemorySource(platform_name)]
    if platform_name == "win32":
        sources.append(LhmSource(settings=_provisioned_lhm_settings))
    elif platform_name.startswith("linux"):
        sources.insert(0, ThermalZoneSource())
        sources.append(RaplSource())
    return sources
