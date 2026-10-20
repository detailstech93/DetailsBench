#!/usr/bin/env python
"""
Real-machine check of frame capture (Phase 6), with no mocks:
  1. find PresentMon the way the app does (chosen file, Intel install, earlier download);
  2. run `PresentMon --help` (no admin rights needed) and check that it lists every
     flag in CAPTURE_FLAGS; the raw help text is printed;
  3. with --capture: start our OWN frame source (a small Qt Quick window, rendered with
     Direct3D 11 on Windows), capture it for 10 s with PresentMon through the UAC prompt,
     store the run in a TEMPORARY database and print the raw CSV head, the stored row and
     the metrics. The frame source is our child and is closed afterwards; nothing else is touched.

Usage:
    python scripts/verify_frames.py              # steps 1-2
    python scripts/verify_frames.py --capture    # steps 1-3 (Windows asks for administrator rights once)
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

CAPTURE_SECONDS = 10

_QML = b"""
import QtQuick
Window {
    width: 480; height: 320; visible: true; title: "DetailsBench frame source"
    Rectangle {
        id: box; width: 80; height: 80; color: "steelblue"; y: 120
        NumberAnimation on x { from: 0; to: 400; duration: 1500; loops: Animation.Infinite }
        RotationAnimation on rotation { from: 0; to: 360; duration: 2000; loops: Animation.Infinite }
    }
}
"""


def frame_source(seconds: float) -> int:
    """Child mode: show an animated Qt Quick window for `seconds`, then exit."""
    from PySide6.QtCore import QByteArray, QTimer, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()
    engine.loadData(QByteArray(_QML), QUrl("frame_source.qml"))
    if not engine.rootObjects():
        print("frame source: QML did not load", flush=True)
        return 2
    print(f"FRAME_SOURCE_PID {os.getpid()}", flush=True)
    QTimer.singleShot(int(seconds * 1000), app.quit)
    return app.exec()


def read_help(exe: str) -> str:
    res = subprocess.run([exe, "--help"], capture_output=True, timeout=30,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    raw = res.stdout + res.stderr
    if raw.startswith(b"\xff\xfe") or raw[1:2] == b"\x00":
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def main(argv) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from app.frames import provision
    from app.frames.presentmon import CAPTURE_FLAGS, FrameCaptureWorkload
    from app.paths import load_settings, resolve_database_path

    ok = True
    print("== 1. Find PresentMon ==")
    arch = provision.native_arch()
    data_dir = resolve_database_path()[0].parent
    settings, _ = load_settings(data_dir)
    found = provision.find_local(arch, data_dir, settings.get("presentmon_path"))
    print(f"  native arch={arch} path={found.path} source={found.source} version={found.version} "
          f"emulated={found.emulated} error={found.error}")
    if not found.path:
        print("\nRESULT: PresentMon not found; use Download PresentMon... in the app first")
        return 1

    print("\n== 2. PresentMon --help ==")
    text = read_help(found.path)
    print(text.rstrip())
    missing = [f for f in CAPTURE_FLAGS if f + " " not in text and not text.rstrip().endswith(f)]
    print(f"\n  flags used by the app: {', '.join(CAPTURE_FLAGS)}")
    print(f"  missing from --help: {missing or 'none'}")
    ok &= not missing

    if "--capture" in argv:
        print(f"\n== 3. Real capture ({CAPTURE_SECONDS} s, UAC prompt) ==")
        from app.automation.runner import AutomationRunner, PlanItem
        from app.database import Database
        work = Path(tempfile.mkdtemp(prefix="pcbm_frames_"))
        db = Database(str(work / "verify.db"))
        print(f"  temporary database: {work / 'verify.db'}")
        # A venv python.exe on Windows is a launcher that starts the real interpreter as a CHILD
        # with another PID, so the window's process reports its own PID instead of using source.pid.
        source = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--frame-source", "180"],
                                  stdout=subprocess.PIPE, text=True, encoding="utf-8")
        try:
            line = source.stdout.readline().strip()
            if not line.startswith("FRAME_SOURCE_PID "):
                print(f"  frame source did not start: {line!r}")
                return 1
            pid = int(line.split()[1])
            time.sleep(3)                         # let the window appear and start rendering
            print(f"  launcher pid={source.pid} rendering pid={pid} running={source.poll() is None}")
            wl = FrameCaptureWorkload(found.path, "python.exe", seconds=CAPTURE_SECONDS, process_id=pid)
            print(f"  command: {wl.describe_command()}")
            sid = db.create_session("Frame capture check", "verify_frames.py", is_test=True, is_manual=False)
            summary = AutomationRunner(db, sid, [PlanItem(wl, repetitions=1, warmup_runs=0)], work / "runs",
                                       on_progress=lambda m: print("  progress:", m)).run()
        finally:
            if source.poll() is None:
                import psutil
                for proc in psutil.Process(source.pid).children(recursive=True) + [psutil.Process(source.pid)]:
                    try:
                        proc.terminate()          # our own frame source (launcher and its interpreter) only
                    except psutil.NoSuchProcess:
                        pass
                source.wait(10)
        for rec in summary.records:
            r = rec.result
            print(f"  status={r.status} score={r.score} unit={r.unit} exit={r.exit_code} error={r.error!r} "
                  f"duration={r.duration_seconds}")
            csv = Path(rec.run_dir) / "presentmon.csv"
            if csv.is_file():
                lines = csv.read_text(encoding="utf-8-sig", errors="replace").splitlines()  # PresentMon writes a BOM
                print(f"  raw CSV: {csv} ({len(lines)} lines); first 3:")
                for line in lines[:3]:
                    print("    " + line)
        rows = db.get_runs(sid, include_test=True)
        for row in rows:
            print(f"  DB row: id={row['id']} status={row['status']} score={row['score']} unit={row['unit']}")
            for name, (value, unit) in sorted(db.get_result_metrics(row["id"]).items()):
                print(f"    metric {name} = {value:g} {unit}")
        ok &= len(rows) == 1 and rows[0]["status"] == "completed" and rows[0]["score"] > 0

    print("\nRESULT:", "ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--frame-source":
        sys.exit(frame_source(float(sys.argv[2])))
    sys.exit(main(sys.argv[1:]))
