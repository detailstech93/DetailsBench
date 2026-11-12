"""How much of the CPU other programs used while a test ran (owner, 2026-11-12).

BL3 and SOTTR scored 7-14% low on runs where something else kept the CPU busy (whole-CPU load 12-24 points
above the clean runs, the CPU clock lower); the check before a launch saw nothing. Each sample gives:
  cpu_other              share of the whole CPU used by programs that are not the test, in %
  cpu_other_<program>    the share of each such program using at least NAMED_PERCENT of the whole CPU
"The test" is the window in front (a game, a benchmark's window) and what it started, this app and what it
started (a benchmark tool), PresentMon and the sensor readers. Only names and CPU times are read.
On Windows all programs' CPU times come from one system call (6 ms for 330 programs; asking each program,
as psutil does, took 750 ms and would itself have loaded the CPU during a test).
"""

import os
import struct
import sys
import time
from typing import Callable, Dict, Optional, Set, Tuple

NAMED_PERCENT = 2.0
# started for a test but not as this app's child: PresentMon runs elevated, its service always runs
OURS = ("presentmon", "librehardwaremonitor", "nvidia-smi")
Times = Dict[int, Tuple[str, float]]       # pid -> (program name, CPU seconds used so far)


def _windows_times() -> Times:
    import ctypes
    ntdll = ctypes.WinDLL("ntdll")
    size = 1 << 20
    while True:
        buf, needed = ctypes.create_string_buffer(size), ctypes.c_ulong()
        status = ntdll.NtQuerySystemInformation(5, buf, size, ctypes.byref(needed)) & 0xFFFFFFFF   # processes
        if status == 0xC0000004:                                                  # buffer too small
            size = max(size * 2, needed.value + 65536)
            continue
        if status:
            raise OSError(f"NtQuerySystemInformation: {status:#x}")
        break
    raw, out, off = buf.raw, {}, 0
    while True:       # SYSTEM_PROCESS_INFORMATION (64-bit): next @0, user/kernel time @40/48, name @56, pid @80
        nxt, = struct.unpack_from("<I", raw, off)
        user, kernel = struct.unpack_from("<qq", raw, off + 40)
        length, = struct.unpack_from("<H", raw, off + 56)
        ptr, = struct.unpack_from("<Q", raw, off + 64)
        pid, = struct.unpack_from("<Q", raw, off + 80)
        out[pid] = (ctypes.wstring_at(ptr, length // 2) if ptr and length else "", (user + kernel) / 1e7)
        if not nxt:
            return out
        off += nxt


def _psutil_times(ps) -> Times:
    out = {}
    for p in ps.process_iter(["name", "cpu_times"]):
        t = p.info.get("cpu_times")
        if t is not None:
            out[p.pid] = (p.info.get("name") or "", t.user + t.system)
    return out


def _foreground_pid() -> Optional[int]:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) or None


class OtherProgramsSource:
    name = "other programs"

    def __init__(self, psutil_module=None, times: Optional[Callable[[], Times]] = None,
                 foreground: Callable[[], Optional[int]] = _foreground_pid, own_pid: Optional[int] = None,
                 clock: Callable[[], float] = time.monotonic):
        if psutil_module is None:
            import psutil as psutil_module
        self.ps = psutil_module
        self.times = times or (_windows_times if sys.platform == "win32" and struct.calcsize("P") == 8
                               else lambda: _psutil_times(self.ps))
        self.foreground, self.clock = foreground, clock
        self.own_pid = own_pid or os.getpid()
        self.cores = max(1, self.ps.cpu_count() or 1)
        self._last: Optional[Tuple[float, Times]] = None

    def available(self):
        self._last = (self.clock(), self.times())         # the first read only starts the measurement
        return True, "CPU used by other programs"

    def _test_pids(self) -> Set[int]:
        pids = {0}                                         # the idle process
        for root in (self.own_pid, self.foreground()):
            if not root:
                continue
            pids.add(root)
            try:
                pids |= {c.pid for c in self.ps.Process(root).children(recursive=True)}
            except Exception:
                pass
        return pids

    def read(self):
        now, times = self.clock(), self.times()
        last, self._last = self._last, (now, times)
        if last is None or now - last[0] <= 0:
            return {}
        span = (now - last[0]) * self.cores
        test = self._test_pids()
        total, named = 0.0, {}
        for pid, (name, used) in times.items():
            before = last[1].get(pid)
            if pid in test or before is None or before[0] != name or not name or name.lower().startswith(OURS):
                continue
            share = max(0.0, used - before[1]) / span * 100.0
            total += share
            if share >= NAMED_PERCENT:
                named[name] = named.get(name, 0.0) + share
        reading = {"cpu_other": (round(min(total, 100.0), 1), "%")}
        reading.update({f"cpu_other_{n}": (round(v, 1), "%") for n, v in named.items()})
        return reading
