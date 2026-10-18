"""Run profiles: named, reusable run settings recorded in each session manifest."""

from dataclasses import asdict, dataclass
from typing import List, Optional

LIMITS = {
    "repetitions": (1, 20),
    "warmup_runs": (0, 5),
    "timeout_seconds": (10, 7200),
    "cooldown_seconds": (0, 600),
}


@dataclass(frozen=True)
class RunProfile:
    name: str
    repetitions: int = 3
    warmup_runs: int = 1
    timeout_seconds: int = 600
    cooldown_seconds: int = 0
    benchmarks: Optional[List[str]] = None   # None = every ready benchmark
    builtin: bool = False

    def settings(self) -> dict:
        """The part stored in the session manifest."""
        data = asdict(self)
        data.pop("builtin")
        data["profile"] = data.pop("name")
        return data


BUILTIN_PROFILES = [
    RunProfile("Standard", 3, 1, 600, 0, builtin=True),
    RunProfile("Quick check", 1, 0, 600, 0, builtin=True),
]


def validate_profile(profile: RunProfile) -> List[str]:
    """Human-readable problems; empty list means valid."""
    problems = []
    if not profile.name.strip():
        problems.append("name is empty")
    for key, (low, high) in LIMITS.items():
        value = getattr(profile, key)
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            problems.append(f"{key} must be a whole number from {low} to {high}")
    if profile.benchmarks is not None and (
            not profile.benchmarks or not all(isinstance(b, str) and b for b in profile.benchmarks)):
        problems.append("benchmarks must be None (all) or a non-empty list of names")
    return problems
