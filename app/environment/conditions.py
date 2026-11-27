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
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional

BUSY_PERCENT = 10.0             # whole-CPU load before a run above this is called busy
SAMPLE_SECONDS = 1.0
IDLE_READS = 5                  # one-second reads of the other programs at rest; their middle is kept
POWER_SAVER_GUID = "a1841308-3541-4fab-bc81-f71556f20b4a"      # Windows' built-in Power saver scheme
SCHEME = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\s*\(([^)]*)\)")
IDLE_NAMES = {"system idle process", "idle"}
# a CPU above this before a run is called still warm: the cooling wait's limits (trust fix 3c, owner 2026-11-12: at
# 60 / 55 it showed on nearly every test, and starts up to 97 C did not lower a score on this PC)
WARM_CPU_C = 90.0
WARM_GPU_C = 80.0
# trust fix 3b (owner, 2026-11-12): before each test, wait while the PC is this hot, at most COOL_MAX_SECONDS. An
# estimate: no start temperature seen on this PC (up to 97 C) lowered a score; it is a safety net
HOT_CPU_C = WARM_CPU_C
HOT_GPU_C = WARM_GPU_C
COOL_MAX_SECONDS = 180.0
COOL_READS = 3          # at rest the CPU reads 54-58 C but single readings jump to 60-83 C: the lowest of a few
COOL_EVERY = 2.0


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


# KDE's / GNOME's power profile, asked of the system's power-profiles service (power-profiles-daemon, or Fedora's
# tuned-ppd, which has no powerprofilesctl: GEEKOM 2026-11-20). busctl prints: s "performance"
PROFILE_COMMAND = ["busctl", "get-property", "net.hadess.PowerProfiles", "/net/hadess/PowerProfiles",
                   "net.hadess.PowerProfiles", "ActiveProfile"]


def _power_profile(run=None) -> str:
    """power-saver, balanced or performance; raises when no such service answers."""
    import subprocess
    out = (run or subprocess.run)(PROFILE_COMMAND, capture_output=True, text=True, timeout=5)
    value = out.stdout.strip().removeprefix("s ").strip('"') if out.returncode == 0 else ""
    if not value:
        raise OSError("no power profile")
    return value


def linux_plan_item(read=None, profile_of=None) -> Optional[dict]:
    def default(path):
        return Path(path).read_text(encoding="utf-8").strip()
    read = read or default
    parts, ok = [], True
    try:
        # Fedora 2026-11-20 (GEEKOM, amd-pstate-epp): "governor powersave" alone said nothing; the profile decides
        profile = (profile_of or _power_profile)()
        parts.append(f"power profile {profile}")
        ok = profile != "power-saver"
    except Exception:
        pass
    try:
        profile = read("/sys/firmware/acpi/platform_profile")
        parts.append(f"platform profile {profile}")
        ok = ok and profile != "low-power"
    except Exception:
        pass
    try:
        parts.append(f"CPU energy preference {read('/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference')}")
    except Exception:
        pass
    try:
        parts.append(f"CPU governor {read('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')}")
    except Exception:
        pass
    if not parts:
        return None
    return _item("Power plan", ", ".join(parts), ok,
                 "" if ok else "the power-saver profile lowers the CPU's speed: pick balanced or performance")


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


def _hot_parts(reading: dict) -> dict:
    out = {}
    cpu = reading.get("cpu_temperature_hottest")
    if cpu:
        out["CPU"] = float(cpu[0])
    gpus = [v[0] for k, v in reading.items() if k.startswith("gpu") and k.endswith("_temperature") and v]
    if gpus:
        out["GPU"] = float(max(gpus))
    return out


def cool_down(stop, say: Callable[[str], object] = lambda text: None, read=None, clock=time.monotonic,
              into: Optional[dict] = None) -> str:
    """Before a test: while the CPU is above HOT_CPU_C or the GPU above HOT_GPU_C, wait (at most COOL_MAX_SECONDS),
    saying so through say. stop: an Event (wait/is_set; the Stop and Skip buttons end the wait). Nothing is changed
    in Windows. Returns what happened, "" when there was no wait (no reading: no wait); into: a timed_check() dict,
    whose items get a "Cooling wait" row for the report."""
    read = read or _read_temperatures

    def lowest(first: dict) -> Optional[dict]:
        low = dict(first)
        for _ in range(COOL_READS - 1):
            if stop.wait(COOL_EVERY):
                return None
            try:
                now = _hot_parts(read())
            except Exception:
                now = {}
            low = {k: min(v, now.get(k, v)) for k, v in low.items()}
        return low

    def hot(temps: dict) -> dict:
        return {k: v for k, v in temps.items() if v > (HOT_CPU_C if k == "CPU" else HOT_GPU_C)}

    try:
        temps = _hot_parts(read())
    except Exception:
        return ""
    if not hot(temps):              # one reading under the limits is enough: a jump only reads hotter
        return ""
    start, began = clock(), None
    while True:
        temps = lowest(temps)
        if temps is None:
            return ""               # stopped or skipped
        began = began or temps
        waited = clock() - start
        if not hot(temps) and waited < COOL_EVERY * COOL_READS + 1:
            if into is not None:    # 2026-11-12: the report said 98 C and "waits" with no wait shown
                into.setdefault("items", []).append(_item("Cooling wait", "not needed: the lowest of "
                    f"{COOL_READS} readings was " + ", ".join(f"{k} {v:.0f} C" for k, v in temps.items()), True))
            return ""               # it was one hot reading, not a hot PC
        shown = ", ".join(f"{k} {v:.0f} C" for k, v in temps.items())
        if not hot(temps) or waited >= COOL_MAX_SECONDS:
            from app.report_set import duration_text
            cooled = not hot(temps)
            text = (f"waited {duration_text(waited)} for the PC to cool: "
                    + ", ".join(f"{k} {began[k]:.0f} -> {v:.0f} C" for k, v in temps.items() if k in began)
                    + ("" if cooled else f"; still above {HOT_CPU_C:g} C CPU / {HOT_GPU_C:g} C GPU, went on"))
            say(text[0].upper() + text[1:])
            if into is not None:
                into.setdefault("items", []).append(_item("Cooling wait", text, cooled,
                                                          "the PC was still hot when the test started"))
            return text
        say(f"Cooling: {shown}, waiting until under {HOT_CPU_C:g} C CPU / {HOT_GPU_C:g} C GPU "
            f"({COOL_MAX_SECONDS - waited:.0f} s left at most)")
        try:
            temps = _hot_parts(read())
        except Exception:
            temps = {}
        if not temps:
            return ""


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
                     f"{' and '.join(warm)} read warm (above {WARM_CPU_C:g} C CPU / {WARM_GPU_C:g} C GPU) once: before each test "
                     f"the app reads it {COOL_READS} times and waits, at most {COOL_MAX_SECONDS / 60:g} minutes, "
                     "while the lowest is still above (one reading can jump 25 C)")
    return _item("Start temperature", ", ".join(parts), True)


def kwin_allowed(rc: Path) -> List[str]:
    """The programs KDE lets send keys and clicks without asking ('Always allow': kwinrc [Xwayland]
    XwaylandEisNoPromptApps, comma separated; Fedora 2026-11-20 had "XwaylandEisNoPromptApps=python")."""
    try:
        lines = rc.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    section = ""
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            section = line
        elif section == "[Xwayland]" and line.startswith("XwaylandEisNoPromptApps="):
            return [x.strip() for x in line.split("=", 1)[1].split(",") if x.strip()]
    return []


def _ask_input() -> None:
    """One XTest call that moves nothing: KDE asks its question on a program's first one."""
    from app.simulation.x11_desktop import X11Desktop
    desktop = X11Desktop.open()
    if desktop is not None:
        desktop.mouse_move(0, 0)


def kde_input_item(env=None, rc: Optional[Path] = None, name: Optional[str] = None, ask=_ask_input) -> Optional[dict]:
    """KDE on Wayland asks once whether the app may send keys and clicks ("python is asking to control input
    devices"). Asked mid run, the game waits for someone at the PC (Fedora 2026-11-19); so it is asked here, at the
    run's question, while the owner is there."""
    import os
    env = os.environ if env is None else env
    from app.simulation.x11_desktop import is_wayland
    if "KDE" not in (env.get("XDG_CURRENT_DESKTOP") or "").upper() or not is_wayland(env):
        return None
    if rc is None:
        rc = Path(env.get("XDG_CONFIG_HOME") or Path(env.get("HOME") or Path.home()) / ".config") / "kwinrc"
    if name is None:
        import psutil
        name = psutil.Process().name()
    if name in kwin_allowed(rc):
        return _item("KDE input permission", f"{name} is always allowed", True)
    try:
        ask()
    except Exception:
        pass
    return _item("KDE input permission", f"{name} is not always allowed yet", False,
                 f"KDE asks now whether {name} may control input devices: tick 'Always allow' and press Allow before "
                 "you start; asked during a game it would wait for someone at the PC")


def check(platform: str = sys.platform, battery=None, plan=None, sample=None, temps=None,
          kde=kde_input_item) -> List[dict]:
    """The items that could be read, in order: power, power plan, background load, start temperature, KDE's input
    permission (Linux)."""
    items = [battery_item(battery),
             windows_plan_item(plan) if platform == "win32" else
             linux_plan_item(plan) if platform.startswith("linux") else None,
             load_item(sample), temperature_item(temps),
             kde() if platform.startswith("linux") else None]
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


def other_idle(source=None, reads: int = IDLE_READS, sleep=time.sleep) -> Optional[float]:
    """The middle of a few one-second reads of the CPU share used by programs that are not this app (the same
    measure as each run's other programs), at rest before the run; None when it cannot be read."""
    try:
        if source is None:
            from app.telemetry.other_programs import OtherProgramsSource
            source = OtherProgramsSource()
        source.available()
        values = []
        for _ in range(reads):
            sleep(1.0)
            value = source.read().get("cpu_other")
            if value:
                values.append(value[0])
        return statistics.median(values) if values else None
    except Exception:
        return None


def screen_now() -> Optional[dict]:
    """The main screen as the run starts: its size in pixels (not scaled), refresh rate and name, from the app's own
    Qt window system (owner 2026-11-19: a package must say what resolution it ran at); None without a Qt app."""
    try:
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.instance() is None:
            return None
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        size, ratio = screen.size(), screen.devicePixelRatio() or 1.0
        return {"width": round(size.width() * ratio), "height": round(size.height() * ratio),
                "refresh_hz": round(screen.refreshRate(), 2), "name": screen.name() or None}
    except Exception:
        return None


HEX = re.compile(r"0x([0-9a-fA-F]+)")
RAPL = "/sys/class/powercap/intel-rapl:0"


def cpu_power_limit(platform: str = sys.platform, run=None, read=None) -> Optional[dict]:
    """What caps the CPU besides the power plan (owner, 2026-11-19). Linux: the RAPL package limits (constraint 0 long,
    1 short term), readable without root unlike its energy counter. Windows: the plan's maximum processor state, AC and
    battery, from powercfg; PL1/PL2 themselves need admin rights (MSR) there and are not read. None where unknown."""
    try:
        if platform.startswith("linux"):
            read = read or (lambda p: Path(p).read_text(encoding="utf-8").strip())
            out = {}
            for i, key in ((0, "long_w"), (1, "short_w")):
                try:
                    out[key] = round(int(read(f"{RAPL}/constraint_{i}_power_limit_uw")) / 1e6, 1)
                except (OSError, ValueError):
                    pass
            return out or None
        if platform == "win32":
            text = run() if run else subprocess.run(
                ["powercfg", "/query", "SCHEME_CURRENT", "SUB_PROCESSOR", "PROCTHROTTLEMAX"], capture_output=True,
                text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            values = [int(h, 16) for h in HEX.findall(text or "")]
            # its last two numbers are the AC and battery indexes (the GUIDs hold no 0x)
            return {"max_state_ac": values[-2], "max_state_dc": values[-1]} if len(values) >= 2 else None
    except Exception:
        return None
    return None


def file_system(path: Optional[Path] = None, partitions=None) -> Optional[str]:
    """The file system of the drive a disk test writes to (the app's data folder): "NTFS", "ext4"... None if unknown."""
    try:
        if path is None:
            from app.paths import resolve_database_path
            path = Path(resolve_database_path()[0]).resolve().parent
        if partitions is None:
            import psutil
            partitions = psutil.disk_partitions(all=False)
        here = str(path).lower()
        found = [p for p in partitions if here.startswith(str(p.mountpoint).lower())]
        return max(found, key=lambda p: len(p.mountpoint)).fstype or None if found else None
    except Exception:
        return None


MESA = re.compile(r"Mesa (\d+\.\d+(?:\.\d+)?)")


def mesa_version(platform: str = sys.platform, run=None) -> Optional[str]:
    """Linux graphics driver version: "Mesa 24.2.8" from glxinfo -B, else vulkaninfo --summary. None elsewhere or
    when neither tool is installed."""
    if not platform.startswith("linux"):
        return None
    for command in (["glxinfo", "-B"], ["vulkaninfo", "--summary"]):
        try:
            text = run(command) if run else subprocess.run(command, capture_output=True, text=True,
                                                           timeout=10).stdout
        except Exception:
            continue
        m = MESA.search(text or "")
        if m:
            return m.group(1)
    return None


def timed_check(idle=other_idle, screen=screen_now, power_limit=cpu_power_limit, fs=file_system, mesa=mesa_version,
                **kw) -> dict:
    """The items with the time they were read (stored in the session's manifest), and the other programs' CPU at rest
    as `other_cpu_idle` (app/report_set.busy_runs measures each run's other programs against it), and the screen."""
    out = {"read_at": time.strftime("%Y-%m-%d %H:%M:%S"), "items": check(**kw)}
    shown = screen()
    if shown:
        out["screen"] = shown
    for key, reader in (("cpu_power_limit", power_limit), ("data_file_system", fs), ("mesa", mesa)):
        value = reader()
        if value:
            out[key] = value
    rest = idle()
    if rest is not None:
        out["other_cpu_idle"] = rest
        out["items"].append(_item("Other programs at rest", f"{rest:.1f}% of the CPU", True))
    return out
