"""How much of the test's graphics card other programs used while a test ran (owner, 2026-11-27; plan "GPU per
program", step 1). Windows only, from the GPU Engine performance counters (the numbers Task Manager shows per
program), on the adapter with the most dedicated memory:
  gpu_other              other programs' share of the card, in %: per program, its engines summed per engine type,
                         its busiest type taken (as Task Manager does); the programs added up, at most 100
  gpu_other_<program>    each such program using at least NAMED_PERCENT
  gpu_windows            Windows' own share (dwm.exe, System, csrss.exe), recorded apart and never flagged: System's
                         copy engine does the paging the test itself asks for, and dwm composes the screen (2026-11-16:
                         System 0.4-1.8%, dwm 0-0.1% during games; nothing else)
"The test" is the same as for the CPU share (other_programs.py): the window in front and what it started, this app
and what it started, PresentMon and the sensor readers.
"""

import re
import sys
from typing import Callable, Dict, Optional, Tuple

from app.telemetry.other_programs import NAMED_PERCENT, OURS

ENGINE = {"engine": r"\GPU Engine(*)\Utilization Percentage"}
INSTANCE = re.compile(r"pid_(\d+)_luid_0x([0-9a-f]+)_0x([0-9a-f]+)_.*engtype_(.+)$", re.I)
WINDOWS_GPU = ("dwm.exe", "system", "csrss.exe")


def per_program(values: Dict[str, float], luid: Tuple[int, int]) -> Dict[int, float]:
    """{instance: %} -> {pid: % of the card}: engines summed per type, the busiest type kept."""
    by_type: Dict[Tuple[int, str], float] = {}
    for instance, value in values.items():
        m = INSTANCE.match(instance.split("#")[0])
        if not m or (int(m.group(2), 16), int(m.group(3), 16)) != luid:
            continue
        key = (int(m.group(1)), m.group(4).lower())
        by_type[key] = by_type.get(key, 0.0) + max(0.0, value)
    out: Dict[int, float] = {}
    for (pid, _), share in by_type.items():
        out[pid] = max(out.get(pid, 0.0), share)
    return out


class GpuProgramsSource:
    name = "GPU other programs"

    def __init__(self, other, platform_name: str = sys.platform, adapters: Optional[Callable] = None,
                 counters: Optional[Callable] = None):
        self.other = other                     # the OtherProgramsSource: its test programs and program names
        self.platform_name = platform_name
        self._adapters_fn, self._counters_fn = adapters, counters
        self._counters, self.luid = None, None

    def available(self):
        if self.platform_name != "win32":
            return False, "per-program GPU use is read on Windows only"
        try:
            from app.telemetry import gpu_memory as gm
            cards = [a for a in (self._adapters_fn or gm.dxgi_adapters)() if a.vendor_id != gm.MICROSOFT_VENDOR]
            if not cards:
                return False, "no graphics adapter listed by DXGI"
            card = max(cards, key=lambda a: a.dedicated_total)
            self.luid = card.luid
            self._counters = (self._counters_fn or (lambda: gm.PdhCounters(ENGINE)))()
            self._counters.read()                # a rate: the first read only starts it
        except Exception as e:
            return False, f"GPU Engine counters could not be read: {e}"
        return True, f"other programs' share of {card.name}"

    def _names(self) -> Dict[int, str]:
        last = getattr(self.other, "_last", None)          # read just before this one in the same sample
        times = last[1] if last else self.other.times()
        return {pid: name for pid, (name, _) in times.items()}

    def read(self):
        shares = per_program(self._counters.read().get("engine") or {}, self.luid)
        if not shares:
            return {"gpu_other": (0.0, "%"), "gpu_windows": (0.0, "%")}
        test, names = getattr(self.other, "_test", None) or self.other._test_pids(), self._names()
        other = windows = 0.0
        named: Dict[str, float] = {}
        for pid, share in shares.items():
            name = names.get(pid, "")
            low = name.lower()
            if pid in test or low.startswith(OURS):
                continue
            if low in WINDOWS_GPU:
                windows += share
                continue
            other += share
            if name and share >= NAMED_PERCENT:
                named[name] = named.get(name, 0.0) + share
        reading = {"gpu_other": (round(min(other, 100.0), 1), "%"), "gpu_windows": (round(min(windows, 100.0), 1), "%")}
        reading.update({f"gpu_other_{n}": (round(v, 1), "%") for n, v in named.items()})
        return reading
