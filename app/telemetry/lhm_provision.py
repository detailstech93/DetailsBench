"""Get LibreHardwareMonitor onto this Windows PC with its web server ready for telemetry (Phase 7).

Order, so nothing is downloaded when not needed:
  1. a copy this app set up earlier (<data dir>\\tools\\librehardwaremonitor\\<version>\\);
  2. download, after the user confirms: the newest stable release from GitHub
     (LibreHardwareMonitor/LibreHardwareMonitor, MPL-2.0) that has the .NET Framework zip
     (LibreHardwareMonitor.zip; .NET Framework 4.7.2 ships with Windows 10/11, the .NET 10 zip
     would need a separate runtime). It is kept only when its size and SHA-256 match the values
     GitHub publishes; the zip is not code-signed, so the published checksum is the check.
     It is extracted (no path may leave the target folder) into a temporary folder that is
     renamed when complete.

Settings (LibreHardwareMonitor.config next to the exe, written only while it is NOT running):
the web server is turned on (port 8085) WITH a random password. Checked in its v0.9.6 source
(HttpServer.cs): it cannot listen on 127.0.0.1 only (it falls back to "+", every network
interface), and the server can also SET sensor controls such as fan speeds, so it must not be
open without a password. It keeps the password as a SHA-256 hash and writes that hash back to
the file on exit, where the next start hashes it again; it saves only on exit or sign-out
(MainForm.cs), so while it runs the password it expects is always the value in its file, which
is what telemetry sends (see app/telemetry/sources.py).

Launch: LibreHardwareMonitor needs administrator rights to read CPU sensors, so it is started
through the UAC prompt (ShellExecuteEx "runas"), after the user confirms. Its sensor driver
(PawnIO in 0.9.5+) is installed by LibreHardwareMonitor itself, with its own prompt; this app
never installs a driver.

ARM64: the .NET Framework build runs on Windows on ARM, but whether its sensor driver works
there is NOT verified; CPU sensors may be missing, which telemetry then reports.
Linux: LibreHardwareMonitor is Windows-only. Telemetry on Linux reads hwmon temperatures and the
live clock through psutil and the CPU package power from RAPL (app/telemetry/sources.py).
"""

import json
import os
import secrets
import shutil
import sys
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from app.frames.provision import USER_AGENT, download_verified
from app.telemetry.lhm_files import (
    ConfigParse, LhmAsset, parse_config, render_config, select_lhm_asset, with_web_server,
)

RELEASES_URL = "https://api.github.com/repos/LibreHardwareMonitor/LibreHardwareMonitor/releases?per_page=10"
EXE_NAME = "LibreHardwareMonitor.exe"
CONFIG_NAME = "LibreHardwareMonitor.config"
MAX_EXTRACTED_BYTES = 300 * 1024 * 1024
MAX_EXTRACTED_FILES = 5000
ARM_NOTE = ("Windows on ARM: the .NET Framework build runs there, but its sensor driver is not verified on ARM; "
            "CPU sensors may be missing.")


@dataclass
class LhmFound:
    exe: Optional[str] = None
    version: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    error: Optional[str] = None


def tools_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "tools" / "librehardwaremonitor"


def _version_key(name: str):
    try:
        return tuple(int(p) for p in name.split("."))
    except ValueError:
        return None


def find_local(data_dir: Path) -> Optional[Path]:
    """The exe of the newest version this app set up, or None."""
    root = tools_dir(data_dir)
    if not root.is_dir():
        return None
    best = None
    for d in root.iterdir():
        key = _version_key(d.name)
        exe = d / EXE_NAME
        if key is not None and d.is_dir() and exe.is_file() and (best is None or key > best[0]):
            best = (key, exe)
    return best[1] if best else None


def fetch_releases(opener: Callable = urllib.request.urlopen, timeout: float = 20) -> list:
    req = urllib.request.Request(RELEASES_URL, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/vnd.github+json"})
    with opener(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def plan_download(platform_name: str = sys.platform, fetch: Callable[[], list] = fetch_releases) -> LhmAsset:
    if platform_name != "win32":
        return LhmAsset(error="LibreHardwareMonitor is Windows-only; on Linux telemetry uses hwmon (psutil) and RAPL")
    try:
        releases = fetch()
    except Exception as e:
        return LhmAsset(error=f"could not read the release list: {e}")
    return select_lhm_asset(releases)


def extract_zip(zip_path: Path, dest: Path) -> Optional[str]:
    """Extract into dest (which must not exist) through dest.partial; None when done, else the reason."""
    dest = Path(dest)
    if dest.exists():
        return f"already exists: {dest}"
    partial = dest.with_name(dest.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)                     # our own leftover from an interrupted extraction
    try:
        with zipfile.ZipFile(zip_path) as z:
            infos = z.infolist()
            if len(infos) > MAX_EXTRACTED_FILES:
                raise ValueError(f"{len(infos)} files in the zip")
            if sum(i.file_size for i in infos) > MAX_EXTRACTED_BYTES:
                raise ValueError("the zip expands to more than 300 MB")
            root = partial.resolve()
            for info in infos:
                target = (partial / info.filename).resolve()
                if target != root and root not in target.parents:
                    raise ValueError(f"unsafe path in the zip: {info.filename!r}")
            partial.mkdir(parents=True)
            z.extractall(partial)
        top = partial
        if not (top / EXE_NAME).is_file():
            subdirs = [p for p in partial.iterdir() if p.is_dir()]
            if len(subdirs) == 1 and len(list(partial.iterdir())) == 1 and (subdirs[0] / EXE_NAME).is_file():
                top = subdirs[0]                   # everything inside one top-level folder
            else:
                raise ValueError(f"{EXE_NAME} is not at the top of the zip")
        os.replace(top, dest)
        if top != partial:
            shutil.rmtree(partial, ignore_errors=True)
        return None
    except Exception as e:
        shutil.rmtree(partial, ignore_errors=True)
        return f"extraction failed: {e}"


def download_lhm(asset: LhmAsset, data_dir: Path, opener: Callable = urllib.request.urlopen,
                 progress: Callable[[int, int], None] = lambda d, t: None, arch: str = "x64") -> LhmFound:
    """Download and extract the chosen release (the user has confirmed)."""
    if asset.error:
        return LhmFound(error=asset.error)
    folder = tools_dir(data_dir) / asset.version
    notes = [ARM_NOTE] if arch == "arm64" else []
    if (folder / EXE_NAME).is_file():
        return LhmFound(str(folder / EXE_NAME), asset.version, notes)
    zip_path = tools_dir(data_dir) / f"{asset.version}-{asset.name}"
    if not zip_path.exists():
        problem = download_verified(asset.url, zip_path, asset.size, asset.sha256, opener, progress=progress)
        if problem:
            return LhmFound(error=problem)
    problem = extract_zip(zip_path, folder)
    try:
        zip_path.unlink()                          # our own verified download; the folder is what is kept
    except OSError:
        pass
    if problem:
        return LhmFound(error=problem)
    return LhmFound(str(folder / EXE_NAME), asset.version, ["size and SHA-256 verified"] + notes)


def config_path(exe) -> Path:
    return Path(exe).with_name(CONFIG_NAME)


def read_settings(exe) -> ConfigParse:
    path = config_path(exe)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return ConfigParse()
    except OSError as e:
        return ConfigParse(error=f"cannot read {path}: {e}")
    return parse_config(text)


def is_running(process_iter: Optional[Callable] = None) -> bool:
    """Any LibreHardwareMonitor.exe process (an elevated one hides its path, so the name is used)."""
    if process_iter is None:
        import psutil
        process_iter = psutil.process_iter
    for proc in process_iter(["name"]):
        try:
            if (proc.info.get("name") or "").lower() == EXE_NAME.lower():
                return True
        except Exception:
            continue
    return False


def configure_web_server(exe, running: Callable[[], bool] = is_running,
                         new_password: Callable[[], str] = lambda: secrets.token_urlsafe(24)) -> Optional[str]:
    """Turn the web server on with a fresh random password; None when written, else the reason."""
    if running():
        return "LibreHardwareMonitor is running; close it first (it rewrites its settings when it exits)"
    current = read_settings(exe)
    if current.error:
        return current.error
    text = render_config(with_web_server(current.settings, new_password()))
    path = config_path(exe)
    partial = path.with_name(path.name + ".partial")
    try:
        with open(partial, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(text)
        os.replace(partial, path)
    except OSError as e:
        return f"cannot write {path}: {e}"
    return None


def launch(exe, api=None) -> Optional[str]:
    """Start it through the UAC prompt and do not wait; None when started, else the reason."""
    if sys.platform != "win32" and api is None:
        return "LibreHardwareMonitor is Windows-only"
    from app.frames.elevated import ERROR_CANCELLED, SW_SHOWMINNOACTIVE, WinApi
    api = api or WinApi()
    handle, err = api.shell_execute(str(exe), "", str(Path(exe).parent), SW_SHOWMINNOACTIVE)
    if handle is None:
        return "the UAC prompt was declined" if err == ERROR_CANCELLED else f"could not start it (error {err})"
    api.close(handle)
    return None
