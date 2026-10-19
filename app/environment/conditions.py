"""What the PC is doing right before a run: things that make scores lower or noisier (the "noise check" idea from
ReBench, owner 2026-10-18/19). Read only: nothing is changed, the owner decides. Never blocks a run.

- Power: on battery (psutil.sensors_battery; None on a desktop).
- Power plan: Windows "powercfg /getactivescheme" (Microsoft's documented, read-only command; it prints the active
  scheme's GUID and name). Power saver is a warning. Linux: /sys/firmware/acpi/platform_profile (kernel ACPI
  platform profile; "low-power" is a warning) and the CPU frequency governor of cpu0.
- Background CPU load: the whole CPU over one second, and the busiest programs (names only) when it is high.
- Start temperature: the hottest CPU sensor and each NVIDIA GPU, read once from the app's telemetry sources
  (LibreHardwareMonitor / psutil on Linux for the CPU, nvidia-smi for the GPU). A PC still warm from an earlier run
  boosts less at the start, so its first scores come out lower. Missing sensors are left out.
"""

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional

BUSY_PERCENT = 10.0             # whole-CPU load before a run above this is called busy
SAMPLE_SECONDS = 1.0
POWER_SAVER_GUID = "a1841308-3541-4fab-bc81-f71556f20b4a"      # Windows' built-in Power saver scheme
SCHEME = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\s*\(([^)]*)\)")
IDLE_NAMES = {"system idle process", "idle"}
WARM_CPU_C = 60.0               # a CPU above this before a run is called still warm
WARM_GPU_C = 55.0


def _item(name: str, value: str, ok: bool, advice: str = "") -> dict:
    return {"name": name, "value": value, "ok": ok, "advice": advice}


def battery_item(read=None) -> Optional[dict]:
    if read is None:
        import psutil
        read = getattr(psutil, "sensors_battery", lambda: None)
    try:
        b = read()
    except Exception:
        return None
    if b is None:
        return None                 # no battery: a desktop
    if b.power_plugged is False:
        return _item("Power", f"on battery ({b.percent:.0f}%)", False,
                     "plug the charger in: many laptops slow down on battery")
    return _item("Power", "plugged in", True)


def windows_plan_item(run=None) -> Optional[dict]:
    try:
        if run is None:
            out = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True, timeout=10,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        else:
            out = run()
    except Exception:
        return None
    m = SCHEME.search(out or "")
    if not m:
        return None
    guid, name = m.group(1).lower(), m.group(2).strip()
    if guid == POWER_SAVER_GUID:
        return _item("Power plan", name, False, "Power saver lowers the CPU's speed: pick Balanced or higher")
    return _item("Power plan", name, True)


def linux_plan_item(read=None) -> Optional[dict]:
    def default(path):
        return Path(path).read_text(encoding="utf-8").strip()
    read = read or default
    parts, ok = [], True
    try:
        profile = read("/sys/firmware/acpi/platform_profile")
        parts.append(f"platform profile {profile}")
        ok = profile != "low-power"
    except Exception:
        pass
    try:
        parts.append(f"CPU governor {read('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')}")
    except Exception:
        pass
    if not parts:
        return None
    return _item("Power plan", ", ".join(parts), ok,
                 "" if ok else "the low-power profile lowers the CPU's speed: pick balanced or performance")


def load_item(sample=None) -> Optional[dict]:
    """(whole-CPU percent, [busiest program names]) from `sample`, or psutil over SAMPLE_SECONDS."""
    try:
        percent, busiest = (sample or _sample)()
    except Exception:
        return None
    value = f"{percent:.0f}% CPU in use"
    if percent <= BUSY_PERCENT:
        return _item("Background load", value, True)
    return _item("Background load", value + (f" (busiest: {', '.join(busiest)})" if busiest else ""), False,
                 "close programs that work in the background, or wait until they finish")


def _sample(seconds: float = SAMPLE_SECONDS):
    import psutil
    procs = []
    for p in psutil.process_iter(["name"]):
        try:
            p.cpu_percent(None)
            procs.append(p)
        except Exception:
            pass
    whole = psutil.cpu_percent(interval=seconds)
    loads = []
    for p in procs:
        try:
            name = p.info.get("name") or ""
            if name.lower() not in IDLE_NAMES and not name.lower().startswith("python"):
                loads.append((p.cpu_percent(None), name))
        except Exception:
            pass
    busiest = [name for load, name in sorted(loads, reverse=True)[:3] if load >= 5.0]
    return whole, busiest


def _read_temperatures() -> dict:
    from app.telemetry import sources
    out = {}
    for source in sources.default_sources():
        try:
            if source.available()[0]:
                out.update(source.read())
        except Exception:
            pass                    # a sensor that cannot be read is left out
    return out


def temperature_item(read=None) -> Optional[dict]:
    """From a telemetry reading {key: (value, unit)}: cpu_temperature_hottest and gpu<N>_temperature."""
    try:
        reading = (read or _read_temperatures)()
    except Exception:
        return None
    parts, warm = [], []
    cpu = reading.get("cpu_temperature_hottest")
    if cpu:
        parts.append(f"CPU {cpu[0]:.0f} C")
        if cpu[0] > WARM_CPU_C:
            warm.append("CPU")
    for key in sorted(k for k in reading if k.startswith("gpu") and k.endswith("_temperature")):
        value = reading[key][0]
        label = "GPU" if key == "gpu0_temperature" and "gpu1_temperature" not in reading else \
            f"GPU {key[3:-len('_temperature')]}"
        parts.append(f"{label} {value:.0f} C")
        if value > WARM_GPU_C:
            warm.append(label)
    if not parts:
        return None
    if warm:
        return _item("Start temperature", ", ".join(parts), False,
                     f"{' and '.join(warm)} still warm (above {WARM_CPU_C:g} C CPU / {WARM_GPU_C:g} C GPU): wait a few "
                     "minutes or use a longer pause, or the first run may score lower")
    return _item("Start temperature", ", ".join(parts), True)


def check(platform: str = sys.platform, battery=None, plan=None, sample=None, temps=None) -> List[dict]:
    """The items that could be read, in order: power, power plan, background load, start temperature."""
    items = [battery_item(battery),
             windows_plan_item(plan) if platform == "win32" else
             linux_plan_item(plan) if platform.startswith("linux") else None,
             load_item(sample), temperature_item(temps)]
    return [i for i in items if i]


def text(items: List[dict]) -> str:
    """For the run's confirmation: one line each, problems with what to do."""
    if not items:
        return ""
    lines = []
    for i in items:
        lines.append(f"  {'OK' if i['ok'] else 'CHECK'}  {i['name']}: {i['value']}"
                     + (f" -> {i['advice']}" if not i["ok"] and i["advice"] else ""))
    head = "PC right now:" if all(i["ok"] for i in items) else \
        "PC right now (scores may come out lower or uneven; you can still run):"
    return head + "\n" + "\n".join(lines)


def timed_check(**kw) -> dict:
    """The items with the time they were read (stored in the session's manifest)."""
    return {"read_at": time.strftime("%Y-%m-%d %H:%M:%S"), "items": check(**kw)}
