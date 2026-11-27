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


# ---- plan step 3: the AI model program's own memory -----------------------------------------------------------
PROCESS_MEMORY = {"dedicated": r"\GPU Process Memory(*)\Dedicated Usage",
                  "shared": r"\GPU Process Memory(*)\Shared Usage"}
# the engine that holds the model, though LM Studio started it, not this app (owner's laptop 2026-11-27: LM Studio's
# llama-server.exe held 17.08 GiB on the RTX 5090 and 1.55 GiB shared; nothing else over 0.6 GiB)
MODEL_RUNNERS = ("llama-server",)
PID_LUID = re.compile(r"pid_(\d+)_luid_0x([0-9a-f]+)_0x([0-9a-f]+)", re.I)


def model_memory(values: Dict[str, Dict[str, float]], luid: Tuple[int, int], candidates) -> Dict[str, float]:
    """{"dedicated"|"shared": {instance: bytes}} -> the candidate program with the most dedicated memory on the
    card: {"dedicated": GiB, "shared": GiB}; {} when no candidate holds any."""
    per: Dict[int, Dict[str, float]] = {}
    for kind in ("dedicated", "shared"):
        for instance, value in (values.get(kind) or {}).items():
            m = PID_LUID.match(instance)
            if m and (int(m.group(2), 16), int(m.group(3), 16)) == luid and int(m.group(1)) in candidates:
                per.setdefault(int(m.group(1)), {"dedicated": 0.0, "shared": 0.0})[kind] += max(0.0, value)
    if not per:
        return {}
    best = max(per.values(), key=lambda d: d["dedicated"])
    return {k: round(v / 2 ** 30, 2) for k, v in best.items()} if best["dedicated"] > 0 else {}


class ModelMemorySource:
    """gpu_model_dedicated / gpu_model_shared (GiB): the memory of the program holding an AI test's model, on the
    test's card and in the PC's memory. The program is this app's own children (a runner it started) or a known model
    engine (MODEL_RUNNERS), whichever holds the most dedicated memory. Windows only."""
    name = "AI model program memory"

    def __init__(self, platform_name: str = sys.platform, adapters: Optional[Callable] = None,
                 counters: Optional[Callable] = None, psutil_module=None, own_pid: Optional[int] = None):
        self.platform_name = platform_name
        self._adapters_fn, self._counters_fn = adapters, counters
        self.ps, self.own_pid = psutil_module, own_pid
        self._counters, self.luid = None, None

    def available(self):
        if self.platform_name != "win32":
            return False, "the model program's memory is read on Windows only"
        try:
            from app.telemetry import gpu_memory as gm
            cards = [a for a in (self._adapters_fn or gm.dxgi_adapters)() if a.vendor_id != gm.MICROSOFT_VENDOR]
            if not cards:
                return False, "no graphics adapter listed by DXGI"
            card = max(cards, key=lambda a: a.dedicated_total)
            self.luid = card.luid
            self._counters = (self._counters_fn or (lambda: gm.PdhCounters(PROCESS_MEMORY)))()
            if self.ps is None:
                import psutil as ps
                self.ps = ps
        except Exception as e:
            return False, f"GPU Process Memory counters could not be read: {e}"
        return True, f"the AI model program's memory on {card.name}"

    def _candidates(self, pids) -> set:
        import os
        own = self.own_pid or os.getpid()
        out = set()
        try:
            out |= {c.pid for c in self.ps.Process(own).children(recursive=True)}
        except Exception:
            pass
        for pid in pids:
            try:
                if self.ps.Process(pid).name().lower().startswith(MODEL_RUNNERS):
                    out.add(pid)
            except Exception:
                pass
        return out

    def read(self):
        values = self._counters.read()
        pids = {int(m.group(1)) for kind in values.values() for inst in kind for m in [PID_LUID.match(inst)] if m}
        found = model_memory(values, self.luid, self._candidates(pids))
        return {f"gpu_model_{k}": (v, "GiB") for k, v in found.items()}
