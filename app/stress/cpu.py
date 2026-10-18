"""CPU stress: one process per logical CPU, each multiplying the same matrices (numpy, one BLAS thread per
process) again and again and comparing every answer with the reference computed before the load started.

Same code on Windows and Linux, x64 and ARM64 (numpy wheels for all four). A wrong answer is an error (the CPU
is not stable at this load / temperature); the number of answers per second is the work rate.
"""

import multiprocessing as mp
import os
import time
from typing import List, Optional

SIZE = 256                  # 256 x 256 float64: fits in the caches, keeps the FPU busy
SEED = 20261018


def _matrices():
    import numpy as np
    rng = np.random.default_rng(SEED)
    return rng.random((SIZE, SIZE)), rng.random((SIZE, SIZE))


def reference_checksum() -> bytes:
    """The answer every worker must give, computed with one thread before the load."""
    import numpy as np
    a, b = _matrices()
    return np.ascontiguousarray(a @ b).tobytes()


def _worker(index: int, expected: bytes, stop, counts, errors):
    """Runs in its own process until stop is set; counts[index] answers, errors[index] wrong answers."""
    import numpy as np
    a, b = _matrices()
    while not stop.is_set():
        for _ in range(8):
            if np.ascontiguousarray(a @ b).tobytes() != expected:
                with errors.get_lock():
                    errors[index] += 1
            with counts.get_lock():
                counts[index] += 1


def available() -> tuple:
    try:
        import numpy  # noqa: F401
    except ImportError:
        return False, "numpy is not installed"
    return True, f"{os.cpu_count() or 1} logical CPUs"


class CpuStress:
    """start() the workers, read() (answers so far, errors so far) any time, stop() them. Our own processes only."""

    def __init__(self, workers: Optional[int] = None, low_priority: bool = False):
        self.workers = max(1, workers or os.cpu_count() or 1)
        self.low_priority = low_priority        # with the GPU stress: the process feeding the GPU goes first
        self._ctx = mp.get_context("spawn")
        self._stop = self._ctx.Event()
        self._counts = self._ctx.Array("q", self.workers)
        self._errors = self._ctx.Array("q", self.workers)
        self._procs: List = []

    def start(self, expected: Optional[bytes] = None):
        env = {k: os.environ.get(k) for k in _THREAD_VARS}
        try:
            for k in _THREAD_VARS:                      # children inherit it: one BLAS thread per process
                os.environ[k] = "1"
            if expected is None:                        # same conditions as the workers, before the load
                with self._ctx.Pool(1) as pool:
                    expected = pool.apply(reference_checksum)
            for i in range(self.workers):
                p = self._ctx.Process(target=_worker, args=(i, expected, self._stop, self._counts, self._errors),
                                      daemon=True, name=f"pcbm-cpu-stress-{i}")
                p.start()
                self._procs.append(p)
                if self.low_priority:
                    _lower_priority(p.pid)
        finally:
            for k, v in env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return self

    def read(self) -> tuple:
        return sum(self._counts[:]), sum(self._errors[:])

    def alive(self) -> int:
        return sum(1 for p in self._procs if p.is_alive())

    def stop(self, grace: float = 10.0):
        self._stop.set()
        end = time.monotonic() + grace
        for p in self._procs:
            p.join(max(0.0, end - time.monotonic()))
        for p in self._procs:
            if p.is_alive():
                p.terminate()                           # our own child process
                p.join(2)


def _lower_priority(pid: int) -> None:
    """Below normal priority for one of our own worker processes (Windows class / Linux nice 10)."""
    try:
        import psutil
        proc = psutil.Process(pid)
        proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
    except Exception:
        pass                                    # the stress still runs, only at normal priority


_THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")
