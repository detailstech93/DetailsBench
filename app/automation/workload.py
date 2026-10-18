"""The Workload interface every automated benchmark implements."""

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

from app.database import RESULT_STATUSES


@dataclass
class RunContext:
    """What the runner gives one run: where to write files, the time limit and the stop flag."""
    output_dir: Path
    timeout_seconds: Optional[float] = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    run_index: Optional[int] = None
    is_warmup: bool = False


@dataclass
class WorkloadResult:
    """Outcome of one run. Only a completed run has a score; a failure is never a number."""
    status: str
    score: Optional[float] = None
    unit: Optional[str] = None
    metrics: dict = field(default_factory=dict)
    error: Optional[str] = None
    duration_seconds: Optional[float] = None
    command: Optional[list] = None
    exit_code: Optional[int] = None
    stdout_path: Optional[str] = None
    stderr_path: Optional[str] = None
    note: Optional[str] = None             # text kept with a completed run (e.g. the game settings it used)
    measured_window: Optional[tuple] = None   # (from, to) seconds after the run started: the part that was
                                              # measured (a game's benchmark scene); telemetry is kept for it only
    series: dict = field(default_factory=dict)   # {name: (unit, [(seconds after the start, value)])} kept for charts

    def __post_init__(self):
        if self.status not in RESULT_STATUSES:
            raise ValueError(f"unknown result status {self.status!r}")
        if self.status == "completed" and self.score is None:
            raise ValueError("a completed run needs a score")
        if self.status != "completed" and self.score is not None:
            raise ValueError(f"a {self.status} run must not have a score")


class Workload(ABC):
    """One automatable benchmark. Subclasses must not block past ctx.timeout_seconds
    and must return promptly (status "cancelled") once ctx.stop_event is set."""

    id: str = ""
    display_name: str = ""
    benchmark_type: str = "cpu"
    is_test: bool = False          # framework self-tests are stored as test data

    def availability(self) -> Tuple[bool, str]:
        """(can run now, short reason)."""
        return True, "available"

    def version(self) -> Optional[str]:
        return None

    def describe_command(self) -> Optional[str]:
        """Human-readable command shown in the confirmation dialog, if any."""
        return None

    @abstractmethod
    def run(self, ctx: RunContext) -> WorkloadResult:
        raise NotImplementedError
