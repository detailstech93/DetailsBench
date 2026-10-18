"""Real-machine check (run manually) of the PyInstaller build: start dist\\PCBenchmarkManager\\PCBenchmarkManager.exe
with a temporary LOCALAPPDATA (so a new, empty database is used, never the real one), wait for its main window,
save a screenshot, check that the bundled tool specs and simulation scripts are there, then close it.
    .venv\\Scripts\\python.exe scripts\\verify_package.py [path to PCBenchmarkManager.exe]
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.automation.process import terminate_tree  # noqa: E402
from app.simulation.desktop import Win32Desktop  # noqa: E402

import psutil  # noqa: E402


def main(exe: Path):
    print("exe", exe, "exists", exe.is_file())
    internal = exe.parent / "_internal" / "data"
    print("bundled data:", sorted(str(p.relative_to(internal)) for p in internal.rglob("*.*")))
    temp = Path(tempfile.mkdtemp(prefix="pcbm_pkg_"))
    env = dict(os.environ, LOCALAPPDATA=str(temp))
    log = open(temp / "console.txt", "wb")
    proc = subprocess.Popen([str(exe)], env=env, stdout=log, stderr=subprocess.STDOUT, cwd=str(temp))
    desk = Win32Desktop()
    root = psutil.Process(proc.pid)
    window = None
    start = time.monotonic()
    while time.monotonic() - start < 90 and proc.poll() is None:
        pids = {p.pid for p in desk.tree(root)}
        window = next((w for w in desk.windows(pids) if "Benchmark" in w.title), None)
        if window:
            break
        time.sleep(1)
    print("main window:", window, "after", round(time.monotonic() - start, 1), "s")
    if window:
        time.sleep(8)                               # let the start-up scans finish
        print("screenshot", desk.screenshot(temp / "package_window.png"), temp / "package_window.png")
        desk.close(window.hwnd)
        try:
            print("exit code", proc.wait(30))
        except subprocess.TimeoutExpired:
            print("did not close; ended", terminate_tree(proc.pid))
    else:
        print("no window; exit code", proc.poll())
        terminate_tree(proc.pid)
    log.close()
    print("database created:", (temp / "PCBenchmarkManager" / "benchmark_manager.db").is_file())
    print("---- console ----")
    print((temp / "console.txt").read_text(encoding="utf-8", errors="replace")[-3000:])


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "PCBenchmarkManager" / "PCBenchmarkManager.exe")
