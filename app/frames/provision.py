"""Get the frame capture tool onto this computer automatically (Phase 6).

Windows: the official PresentMon command-line file from GitHub releases
(GameTechDev/PresentMon). Order of preference, so nothing is downloaded when
not needed:
  1. the path saved in settings.json (if still valid);
  2. an Intel PresentMon installation (Program Files\\Intel\\PresentMon\\PresentMonConsoleApplication);
  3. a copy this app downloaded earlier (<data dir>\\tools\\presentmon\\<version>\\);
  4. download: newest release with a file for this architecture, after the user confirms.
A downloaded file is kept only when its size and SHA-256 match the values
GitHub publishes for it AND its Authenticode signature is valid and from
Intel Corporation; otherwise it is deleted (it is our own partial file) and
nothing is saved.

ARM64: Intel ships no native ARM64 build (GameTechDev/PresentMon#667, closed
"not planned" on 2026-07-16). An arm64 file is preferred automatically if one
ever appears; until then the x64 file is used under Windows x64 emulation,
which is NOT verified on ARM hardware and is reported as such.

Linux: PresentMon is Windows-only (ETW). The alternative is MangoHud, installed
with the distribution's package manager through pkexec (the OS password
prompt) after the user confirms; see app/frames/linux_install.py.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from app.frames.release_select import AssetChoice, select_asset

RELEASES_URL = "https://api.github.com/repos/GameTechDev/PresentMon/releases?per_page=10"
USER_AGENT = "PC-Benchmark-Manager"
EXPECTED_SIGNER = "CN=Intel Corporation"
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024        # the console file is about 1 MB
INTEL_CONSOLE_DIR = Path("Intel") / "PresentMon" / "PresentMonConsoleApplication"
_FILE_RE = re.compile(r"^PresentMon-(\d+(?:\.\d+){1,3})-(x64|x86|arm64)\.exe$")


@dataclass
class Found:
    path: Optional[str] = None
    source: Optional[str] = None             # "settings", "intel install", "downloaded", "download"
    version: Optional[str] = None
    emulated: bool = False
    notes: List[str] = field(default_factory=list)
    error: Optional[str] = None


def _version_key(version: str):
    return tuple(int(p) for p in version.split("."))


def native_arch() -> str:
    """Architecture of the machine itself (not of this Python): x64, x86, arm64 or unknown.
    An x64 Python on an ARM64 PC reports arm64 here (IsWow64Process2)."""
    from app.environment.arch import normalize_arch
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            from app.environment.adapters import _IMAGE_FILE_MACHINE
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            fn = k.IsWow64Process2
            fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.USHORT), ctypes.POINTER(wintypes.USHORT)]
            fn.restype = wintypes.BOOL
            k.GetCurrentProcess.restype = wintypes.HANDLE
            proc_m, native_m = wintypes.USHORT(), wintypes.USHORT()
            if fn(k.GetCurrentProcess(), ctypes.byref(proc_m), ctypes.byref(native_m)):
                return _IMAGE_FILE_MACHINE.get(native_m.value, "unknown")
        except (OSError, AttributeError):
            pass
        return normalize_arch(os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE"))
    import platform
    return normalize_arch(platform.machine())


def tools_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "tools" / "presentmon"


def _console_files(folder: Path):
    """[(version, arch, path)] of PresentMon-<ver>-<arch>.exe directly in folder."""
    out = []
    if folder.is_dir():
        for p in folder.iterdir():
            m = _FILE_RE.match(p.name)
            if m and p.is_file():
                out.append((m.group(1), m.group(2), p))
    return out


def _pick_local(candidates, native_arch: str):
    prefs = {"x64": ("x64",), "x86": ("x86",), "arm64": ("arm64", "x64")}.get(native_arch, ())
    best = None
    for version, arch, path in candidates:
        if arch not in prefs:
            continue
        key = (_version_key(version), -prefs.index(arch))
        if best is None or key > best[0]:
            best = (key, version, arch, path)
    return best


def find_local(native_arch: str, data_dir: Path, settings_path: Optional[str] = None,
               env=None, signature_check: Optional[Callable[[str], Optional[str]]] = None) -> Found:
    """Steps 1-3 above; never downloads, never runs PresentMon."""
    from app.frames.presentmon import check_presentmon_path
    env = os.environ if env is None else env
    check = signature_check or authenticode_problem
    if settings_path and check_presentmon_path(settings_path) is None:
        m = _FILE_RE.match(Path(settings_path).name)
        return Found(settings_path, "settings", m.group(1) if m else None,
                     emulated=bool(m and native_arch == "arm64" and m.group(2) == "x64"))
    places = []
    for var in ("ProgramFiles", "ProgramW6432"):
        if env.get(var):
            places.append(("intel install", Path(env[var]) / INTEL_CONSOLE_DIR))
    root = tools_dir(data_dir)
    if root.is_dir():
        for sub in root.iterdir():
            places.append(("downloaded", sub))
    for source, folder in places:
        best = _pick_local(_console_files(folder), native_arch)
        if best is None:
            continue
        _, version, arch, path = best
        problem = check(str(path))
        if problem:
            continue                       # never use an unsigned or foreign file
        return Found(str(path), source, version, emulated=(native_arch == "arm64" and arch == "x64"))
    return Found(error="PresentMon not found on this computer")


# ---- network ------------------------------------------------------------------------
def fetch_releases(opener: Callable = urllib.request.urlopen, timeout: float = 20) -> list:
    req = urllib.request.Request(RELEASES_URL, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/vnd.github+json"})
    with opener(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download_verified(url: str, dest: Path, size: int, sha256: str,
                      opener: Callable = urllib.request.urlopen, timeout: float = 60,
                      progress: Callable[[int, int], None] = lambda done, total: None,
                      max_bytes: int = MAX_DOWNLOAD_BYTES) -> Optional[str]:
    """Download to dest via dest.partial; None when size and SHA-256 match, else the reason
    (the partial file is removed). An existing dest is never overwritten."""
    dest = Path(dest)
    if dest.exists():
        return f"already exists: {dest}"
    if size <= 0 or size > max_bytes:
        return f"unexpected size {size}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    digest, done = hashlib.sha256(), 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with opener(req, timeout=timeout) as resp, open(partial, "wb") as f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                done += len(chunk)
                if done > size:
                    raise ValueError(f"more data than the published size {size}")
                digest.update(chunk)
                f.write(chunk)
                progress(done, size)
        if done != size:
            raise ValueError(f"size {done} differs from the published size {size}")
        if digest.hexdigest() != sha256:
            raise ValueError("SHA-256 differs from the published checksum")
        os.replace(partial, dest)
        return None
    except Exception as e:
        try:
            partial.unlink()               # our own incomplete file only
        except OSError:
            pass
        return f"download failed: {e}"


def authenticode_problem(path: str, runner: Callable = subprocess.run) -> Optional[str]:
    """None when the file has a valid Authenticode signature from Intel Corporation (Windows only)."""
    if sys.platform != "win32":
        return "signature check is only available on Windows"
    script = ("$s = Get-AuthenticodeSignature -LiteralPath $env:PCBM_SIG_PATH; "
              "[pscustomobject]@{status=[string]$s.Status; subject=[string]$s.SignerCertificate.Subject} "
              "| ConvertTo-Json -Compress")
    try:
        res = runner(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                     capture_output=True, text=True, timeout=30,
                     env=dict(os.environ, PCBM_SIG_PATH=str(path)),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        info = json.loads(res.stdout.strip() or "{}")
    except Exception as e:
        return f"signature check failed: {e}"
    if info.get("status") != "Valid":
        return f"signature status {info.get('status') or 'unknown'}"
    if not str(info.get("subject", "")).startswith(EXPECTED_SIGNER + ","):
        return f"signed by {info.get('subject')!r}, not Intel Corporation"
    return None


def plan_download(native_arch: str, fetch: Callable[[], list] = fetch_releases) -> AssetChoice:
    try:
        releases = fetch()
    except Exception as e:
        return AssetChoice(error=f"could not read the release list: {e}")
    return select_asset(releases, native_arch)


def download_presentmon(choice: AssetChoice, data_dir: Path, opener: Callable = urllib.request.urlopen,
                        signature_check: Optional[Callable[[str], Optional[str]]] = None,
                        progress: Callable[[int, int], None] = lambda d, t: None) -> Found:
    """Download the chosen file (the user has confirmed); keep it only if every check passes."""
    if choice.error:
        return Found(error=choice.error)
    dest = tools_dir(data_dir) / choice.version / choice.name
    check = signature_check or authenticode_problem
    if dest.exists():
        problem = check(str(dest))
        if problem:
            return Found(error=f"existing file {dest} rejected: {problem}")
        return Found(str(dest), "downloaded", choice.version, choice.emulated)
    problem = download_verified(choice.url, dest, choice.size, choice.sha256, opener, progress=progress)
    if problem:
        return Found(error=problem)
    problem = check(str(dest))
    if problem:
        try:
            dest.unlink()                  # the file we just downloaded failed verification
        except OSError:
            pass
        return Found(error=f"downloaded file rejected: {problem}")
    notes = ["size, SHA-256 and Intel signature verified"]
    if choice.emulated:
        notes.append("no native ARM64 build from Intel; the x64 file runs under Windows emulation "
                     "(not verified on ARM hardware)")
    return Found(str(dest), "download", choice.version, choice.emulated, notes)
