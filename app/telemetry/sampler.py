"""Sample telemetry sources in a background thread while one run executes.

A source has `name`, `available() -> (bool, reason)` and `read() -> {key: (value, unit)}`.
A source that is not available is reported with its reason and never read; a read that
raises is counted as an error for that source and sampling continues. Readings that are
missing are simply absent (never stored as 0). After the run, `summarize` turns the samples
into avg/max metrics per key and `write_csv` keeps every sample in the run folder.
"""

import csv
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

Reading = Dict[str, Tuple[float, str]]
METRIC_PREFIX = "tel_"


@dataclass
class TelemetryResult:
    samples: List[Tuple[float, Reading]] = field(default_factory=list)   # (seconds since start, readings)
    unavailable: Dict[str, str] = field(default_factory=dict)            # source name -> reason
    errors: Dict[str, int] = field(default_factory=dict)                 # source name -> failed reads
    last_error: Dict[str, str] = field(default_factory=dict)


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class TelemetrySampler:
    def __init__(self, sources: list, interval_seconds: float = 1.0, clock: Callable[[], float] = time.monotonic):
        self.sources = list(sources)
        self.interval = max(0.1, float(interval_seconds))
        self.clock = clock
        self.result = TelemetryResult()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._active = []
        self._started = 0.0

    def start(self):
        for src in self.sources:
            try:
                ok, reason = src.available()
            except Exception as e:
                ok, reason = False, f"availability check failed: {e}"
            if ok:
                self._active.append(src)
            else:
                self.result.unavailable[src.name] = reason
        self._started = self.clock()
        if self._active:
            self._thread = threading.Thread(target=self._loop, name="telemetry", daemon=True)
            self._thread.start()
        return self

    def sample_once(self):
        reading: Reading = {}
        for src in self._active:
            try:
                for key, (value, unit) in (src.read() or {}).items():
                    if _finite(value):
                        reading[key] = (float(value), unit)
            except Exception as e:
                self.result.errors[src.name] = self.result.errors.get(src.name, 0) + 1
                self.result.last_error[src.name] = str(e)
        if reading:
            self.result.samples.append((round(self.clock() - self._started, 3), reading))

    def _loop(self):
        while not self._stop.is_set():
            began = self.clock()
            self.sample_once()
            self._stop.wait(max(0.0, self.interval - (self.clock() - began)))

    def stop(self, timeout: float = 15.0) -> TelemetryResult:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        return self.result


def summarize(result: TelemetryResult) -> Dict[str, Tuple[float, str]]:
    """{tel_<key>_avg / tel_<key>_max: (value, unit)} for every key read at least once."""
    values: Dict[str, List[float]] = {}
    units: Dict[str, str] = {}
    for _, reading in result.samples:
        for key, (value, unit) in reading.items():
            values.setdefault(key, []).append(value)
            units[key] = unit
    out = {}
    for key, vals in values.items():
        out[f"{METRIC_PREFIX}{key}_avg"] = (sum(vals) / len(vals), units[key])
        out[f"{METRIC_PREFIX}{key}_max"] = (max(vals), units[key])
    return out


def write_csv(result: TelemetryResult, path: Path) -> Optional[Path]:
    """One row per sample, one column per key (empty when not read in that sample)."""
    if not result.samples:
        return None
    keys = sorted({k for _, r in result.samples for k in r})
    units = {k: u for _, r in result.samples for k, (_, u) in r.items()}
    path = Path(path)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["seconds"] + [f"{k} [{units[k]}]" if units[k] else k for k in keys])
        for t, r in result.samples:
            w.writerow([t] + [r[k][0] if k in r else "" for k in keys])
    return path
