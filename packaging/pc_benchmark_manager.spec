# PyInstaller spec for PC Benchmark Manager (one folder, with a console window like run.bat).
# Build from the project folder with the separate build environment (see packaging/README.md):
#     .venv-build\Scripts\python.exe -m PyInstaller packaging\pc_benchmark_manager.spec --noconfirm
# Output: dist\PCBenchmarkManager\PCBenchmarkManager.exe (the build and dist folders are not tracked by Git).

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))


def data_files(folder, patterns):
    import glob
    out = []
    for pattern in patterns:
        for path in glob.glob(os.path.join(ROOT, folder, pattern)):
            out.append((path, folder.replace("\\", "/")))
    return out


# Tool specs and simulation scripts are read from <bundle>/data/... (app/automation/cli_workload.py TOOLS_DIR,
# app/simulation/script.py SCRIPTS_DIR); the data package's .py modules are found through the imports.
datas = data_files("data/tools", ["*.json", "README.md"]) + data_files("data/scripts", ["*.json"])

# The commit this build comes from, for session manifests (app/manifest.py git_commit).
import json
import subprocess
from datetime import datetime

commit = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
dirty = subprocess.run(["git", "-C", ROOT, "status", "--porcelain", "--untracked-files=no"],
                       capture_output=True, text=True, timeout=10)
os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
info_path = os.path.join(ROOT, "build", "build_info.json")
with open(info_path, "w", encoding="utf-8") as f:
    json.dump({"commit": commit.stdout.strip() if commit.returncode == 0 else None,
               "uncommitted_changes": bool(dirty.stdout.strip()),
               "built": datetime.now().isoformat(timespec="seconds")}, f, indent=1)
datas.append((info_path, "."))

a = Analysis(
    [os.path.join(ROOT, "scripts", "main.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=[],
    excludes=["pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PCBenchmarkManager",
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PCBenchmarkManager", upx=False)
