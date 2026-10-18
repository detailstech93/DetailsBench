"""One-click install of a tool the vendor ships as a zip or tar.gz (no package), e.g. the Blender Benchmark launcher.

The file's address, size and SHA-256 are pinned in data/installers.py ("zip"), read from the vendor's own download
server and checked by hand when they were added. The app downloads it into its own tools folder
(%LOCALAPPDATA%\\PCBenchmarkManager\\tools), refuses it if size or SHA-256 differ, and extracts it into a
folder named after the tool and version. Nothing runs and no administrator rights are needed; the scan then
finds the program there (app/benchmark_manager.py looks in the tools folder too).
"""

import os
import shutil
import stat
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from app.frames.provision import download_verified
from app.install.winget import Outcome, size_text
from app.paths import get_data_dir

MAX_EXTRACTED_BYTES = 500 * 1024 * 1024
MAX_EXTRACTED_FILES = 5000
MAX_DOWNLOAD_BYTES = 2 * 1024 ** 3                # V-Ray Benchmark is one 623 MB program


def tools_root(data_dir: Optional[Path] = None) -> Path:
    return Path(data_dir or get_data_dir()) / "tools"


def target_folder(plan: dict, data_dir: Optional[Path] = None) -> Path:
    return tools_root(data_dir) / plan["folder"]


def problem(plan: dict) -> Optional[str]:
    """Why this pinned download must not be used (None: fine)."""
    url = urlparse(plan.get("url", ""))
    if url.scheme != "https" or url.hostname not in plan.get("hosts", []):
        return f"the download address is not the vendor's: {plan.get('url')!r}"
    if not (isinstance(plan.get("size"), int) and plan["size"] > 0):
        return "no size pinned"
    if len(str(plan.get("sha256", ""))) != 64:
        return "no SHA-256 pinned"
    folder = str(plan.get("folder", ""))
    if not folder or os.path.basename(folder) != folder or folder in (".", ".."):
        return f"bad folder name {folder!r}"
    return None


def confirm_text(name: str, plan: dict, data_dir: Optional[Path] = None) -> str:
    return (f"Download and install {name}?\n\n"
            f"{plan['product']} {plan['version']} from its official server:\n{plan['url']}\n"
            f"Size {size_text(plan['size'])}, SHA-256 {plan['sha256']}\n"
            f"Kept only if the size and SHA-256 match the ones checked when it was added to the app.\n\n"
            f"It is {'saved' if plan.get('single') else 'extracted'} to {target_folder(plan, data_dir)}; "
            f"{'' if plan.get('then') else 'nothing is run and '}no administrator rights are needed. {plan.get('note', '')}").strip()


def archive_suffix(url: str) -> str:
    return ".tar.gz" if urlparse(url).path.endswith((".tar.gz", ".tgz")) else ".zip"


def place_single(download: Path, dest: Path, exe: str) -> Optional[str]:
    """A tool shipped as one program file ("single": True): put it in dest as exe, through dest.partial."""
    partial = dest.with_name(dest.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)                     # our own leftover
    try:
        partial.mkdir(parents=True)
        os.replace(download, partial / exe)
        if os.name != "nt":
            mode = (partial / exe).stat().st_mode
            (partial / exe).chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.replace(partial, dest)
        return None
    except Exception as e:
        shutil.rmtree(partial, ignore_errors=True)
        return f"could not place the program: {e}"


def _check(names_sizes, partial: Path):
    if len(names_sizes) > MAX_EXTRACTED_FILES:
        raise ValueError(f"{len(names_sizes)} files in the archive")
    if sum(size for _, size in names_sizes) > MAX_EXTRACTED_BYTES:
        raise ValueError("the archive expands to more than 500 MB")
    root = partial.resolve()
    for name, _ in names_sizes:
        target = (partial / name).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"unsafe path in the archive: {name!r}")


def extract(archive: Path, dest: Path, exe: str) -> Optional[str]:
    """Extract a zip or tar.gz into dest (which must not exist) through dest.partial; None when done, else why."""
    partial = dest.with_name(dest.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)                     # our own leftover from an interrupted extraction
    try:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as z:
                infos = z.infolist()
                partial.mkdir(parents=True)
                _check([(i.filename, i.file_size) for i in infos], partial)
                z.extractall(partial)
        else:
            with tarfile.open(archive, "r:gz") as t:
                members = t.getmembers()
                bad = [m.name for m in members if not (m.isfile() or m.isdir())]
                if bad:
                    raise ValueError(f"links or special files in the archive: {bad[:3]}")
                partial.mkdir(parents=True)
                _check([(m.name, m.size) for m in members], partial)
                t.extractall(partial, members=members)
        if not (partial / exe).is_file():
            raise ValueError(f"{exe} is not in the archive")
        if os.name != "nt":
            mode = (partial / exe).stat().st_mode
            (partial / exe).chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.replace(partial, dest)
        return None
    except Exception as e:
        shutil.rmtree(partial, ignore_errors=True)
        return f"extraction failed: {e}"


def install(name: str, plan: dict, data_dir: Optional[Path] = None, opener: Callable = urllib.request.urlopen,
            progress: Callable[[int, int], None] = lambda d, t: None) -> Outcome:
    """Download, check and extract (the owner has confirmed)."""
    bad = problem(plan)
    if bad:
        return Outcome(False, f"{name} was not installed: {bad}.")
    dest = target_folder(plan, data_dir)
    if (dest / plan["exe"]).is_file():
        return Outcome(True, f"{name} is already in {dest}.")
    if dest.exists():
        return Outcome(False, f"{name} was not installed: {dest} exists but has no {plan['exe']}; "
                              "remove that folder yourself and try again.")
    zip_path = tools_root(data_dir) / (plan["folder"] + (".download" if plan.get("single")
                                                         else archive_suffix(plan["url"])))
    try:
        zip_path.unlink()                          # our own leftover, not yet verified
    except FileNotFoundError:
        pass
    why = download_verified(plan["url"], zip_path, plan["size"], plan["sha256"], opener, progress=progress,
                          max_bytes=MAX_DOWNLOAD_BYTES)
    if why:
        return Outcome(False, f"{name} was not installed: {why}.")
    why = place_single(zip_path, dest, plan["exe"]) if plan.get("single") else extract(zip_path, dest, plan["exe"])
    try:
        zip_path.unlink()                          # verified and extracted: the folder is what is kept
    except OSError:
        pass
    if why:
        return Outcome(False, f"{name} was not installed: {why}.")
    return Outcome(True, f"{name} installed in {dest} (size and SHA-256 verified).")
