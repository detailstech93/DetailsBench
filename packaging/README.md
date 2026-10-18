# Packaging (PyInstaller)

The app can be built into one folder, `dist\PCBenchmarkManager`, that runs without Python:
`PCBenchmarkManager.exe` (with a console window, like `run.bat`, so start-up errors stay visible).
Nothing is installed: copy the folder anywhere (a portable SSD too) and run the exe.

## Build

PyInstaller 6.16 refuses pefile 2024.8.26, the version in `requirements.txt`, so the build uses its own
environment and the app's `.venv` stays as it is:

```
C:\Users\<you>\AppData\Local\Programs\Python\Python310\python.exe -m venv .venv-build
.venv-build\Scripts\python.exe -m pip install PySide6==6.12.0 psutil==7.2.2 pyinstaller==6.16.0 pytest
.venv-build\Scripts\python.exe -m pytest -q
.venv-build\Scripts\python.exe -m PyInstaller packaging\pc_benchmark_manager.spec --noconfirm
```

That environment gets pefile 2023.2.7 (PyInstaller's requirement); the whole test suite passed with it on
2026-10-18 (924 passed, 1 skipped), so the build ships it. `build\`, `dist\` and `.venv-build\` are not tracked.

The spec bundles `data\tools\*.json` and `data\scripts\*.json` (read from `_internal\data`) and writes
`build_info.json` (the commit, whether there were uncommitted changes, the build time); session manifests of the
packaged app take the commit from it, never from git.

## Check

```
.venv\Scripts\python.exe scripts\verify_package.py
```

starts the built exe with a temporary `LOCALAPPDATA` (a new, empty database; the real one is not touched), waits
for the main window, saves a screenshot, closes it and prints the console output. Raw result on 2026-10-18:
main window after 6.1 s, exit code 0, database created, migrations logged, folder size 117 MB.

## What the packaged app cannot do

- Tool specs that run Python (`"python": true`, only the framework self-test) show "needs Python, which the
  packaged app does not include".
- llama-benchy is a Python program installed into `.venv`; the packaged app uses it only when `PCBM_LLAMA_BENCHY`
  points to its `llama-benchy.exe`.
- External tools (7-Zip, DiskSpd, fio, MLPerf Client, PresentMon, LibreHardwareMonitor) are found as before
  (installed, `%USERPROFILE%\Tools`, or their `PCBM_*` variable); none is bundled.
- The exe is not signed: SmartScreen may warn on first start on another computer.
