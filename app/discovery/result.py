"""DiscoveryResult: matched software, games, launcher clients, diagnostics."""

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryResult:
    software: List[dict] = field(default_factory=list)     # match_software() output only
    games: List[dict] = field(default_factory=list)        # GameEntry dicts (store, app_id, name, install_path, version)
    # Per store/client: {"status": "detected" | "not detected" | "error", "path": ..., "source": ...}
    stores: Dict[str, dict] = field(default_factory=dict)
    diagnostics: List[str] = field(default_factory=list)
    step_seconds: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DiscoveryResult":
        return cls(**{k: data[k] for k in ("software", "games", "stores", "diagnostics", "step_seconds")
                      if k in data})

    def run_step(self, name: str, step: Callable[[], None]) -> None:
        """Run one discovery step isolated: a failure is recorded, never raised."""
        start = time.perf_counter()
        try:
            step()
        except Exception as exc:
            logger.warning("discovery step %s failed: %s", name, exc)
            self.diagnostics.append(f"step {name} failed: {exc}")
        self.step_seconds[name] = round(time.perf_counter() - start, 3)


def store_status(status: str, path: Optional[str] = None, source: Optional[str] = None) -> dict:
    return {"status": status, "path": path, "source": source}
