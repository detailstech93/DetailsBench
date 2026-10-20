"""One-click install of a tool the vendor ships as a zip, tar.gz or tar.xz (no package), e.g. the Blender Benchmark launcher.

The file's address, size and SHA-256 are pinned in data/installers.py ("zip"), read from the vendor's own download
server and checked by hand when they were added. The app downloads it into its own tools folder
(%LOCALAPPDATA%\\PCBenchmarkManager\\tools), refuses it if size or SHA-256 differ, and extracts it into a
folder named after the tool and version. Nothing runs and no administrator rights are needed; the scan then
finds the program there (app/benchmark_manager.py looks in the tools folder too).

Two more vendor formats, still unpacked by the app and never run:
- a "makeself" .run file (GravityMark for Linux): a shell header of N lines ("head -n N" in it), then a tar.gz of
  "filesizes" bytes. Read here as data, as `--noexec --target` would unpack it (Phoronix Test Suite does that).
- a Windows .msi (GravityMark for Windows): Windows' own msiexec makes an administrative image of it (`msiexec /a
  ... TARGETDIR=`): the files only, no administrator rights, nothing registered, and the package's
  AdminExecuteSequence runs no custom action (its Visual C++ installer and browser start only in a normal install;
  read from the MSI's tables, 2026-10-20).
"""

import os
import re
import shutil
import stat
import subprocess
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
MAX_DOWNLOAD_BYTES = 3 * 1024 ** 3                # Cinebench 2026 for Windows x64 is a 2.5 GB zip
# a tool may pin larger limits ("max_files", "max_unpacked" in its zip plan): Cinebench 2026 x64 holds 29,410
# files, 4.1 GB unpacked (its zip's own listing, 2026-10-19)


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
    if len(str(plan.get("sha256", ""))) != 64 and len(str(plan.get("md5", ""))) != 32:
        return "no SHA-256 (or vendor MD5) pinned"
    folder = str(plan.get("folder", ""))
    if not folder or os.path.basename(folder) != folder or folder in (".", ".."):
        return f"bad folder name {folder!r}"
    return None


def confirm_text(name: str, plan: dict, data_dir: Optional[Path] = None) -> str:
    return (f"Download and install {name}?\n\n"
            f"{plan['product']} {plan['version']} from its official server:\n{plan['url']}\n"
            f"Size {size_text(plan['size'])}, {checksum_text(plan)}\n"
            f"Kept only if the size and checksum match the ones checked when it was added to the app.\n\n"
            f"It is {'saved' if plan.get('single') else 'extracted'} to {target_folder(plan, data_dir)}; "
            f"{'' if plan.get('then') else 'nothing is run and '}no administrator rights are needed. {plan.get('note', '')}").strip()


def checksum_text(plan: dict) -> str:
    if plan.get("sha256"):
        return f"SHA-256 {plan['sha256']}"
    return f"MD5 {plan['md5']} (the only checksum the vendor publishes)"


def archive_suffix(url: str) -> str:
    path = urlparse(url).path
    for suffix in (".tar.xz", ".run", ".msi"):
        if path.endswith(suffix):
            return suffix
    return ".tar.gz" if path.endswith((".tar.gz", ".tgz")) else ".zip"


MAKESELF_LINES = re.compile(rb'head -n (\d+) "\$1"')
MAKESELF_SIZES = re.compile(rb'filesizes="(\d+)"')


def makeself_payload(path: Path, header_bytes: int = 65536):
    """(offset, size) of the tar.gz inside a makeself .run file, read from its header (never run)."""
    with open(path, "rb") as f:
        head = f.read(header_bytes)
    lines, sizes = MAKESELF_LINES.search(head), MAKESELF_SIZES.search(head)
    if not lines or not sizes:
        raise ValueError("not a makeself archive (no header line count or file size)")
    offset = 0
    for _ in range(int(lines.group(1))):
        offset = head.index(b"\n", offset) + 1
    size = int(sizes.group(1).split()[0])
    if offset + size != path.stat().st_size:
        raise ValueError("the makeself header does not match the file's size")
    return offset, size


def msi_image(msi: Path, partial: Path, run=subprocess.run, timeout: int = 600) -> None:
    """Windows' msiexec makes an administrative image of the .msi in partial (files only, no administrator)."""
    msiexec = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "msiexec.exe"
    result = run([str(msiexec), "/a", str(msi.resolve()), "/qn", f"TARGETDIR={partial.resolve()}"],
                 capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise ValueError(f"msiexec /a ended with code {result.returncode}")
    for leftover in partial.glob("*.msi"):          # the image keeps a copy of the package itself
        leftover.unlink()


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


def _check(names_sizes, partial: Path, max_files: int = MAX_EXTRACTED_FILES, max_bytes: int = MAX_EXTRACTED_BYTES):
    if len(names_sizes) > max_files:
        raise ValueError(f"{len(names_sizes)} files in the archive")
    if sum(size for _, size in names_sizes) > max_bytes:
        raise ValueError(f"the archive expands to more than {max_bytes // 1024 ** 2} MB")
    root = partial.resolve()
    for name, _ in names_sizes:
        target = (partial / name).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"unsafe path in the archive: {name!r}")


def extract(archive: Path, dest: Path, exe: str, max_files: int = MAX_EXTRACTED_FILES,
            max_bytes: int = MAX_EXTRACTED_BYTES) -> Optional[str]:
    """Extract a zip, tar.gz or tar.xz into dest (which must not exist) through dest.partial; None when done, else why."""
    partial = dest.with_name(dest.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)                     # our own leftover from an interrupted extraction
    try:
        if archive.suffix == ".msi":
            partial.mkdir(parents=True)
            msi_image(archive, partial)
        elif archive.suffix == ".run":              # its tar.gz copied out, then unpacked like any other
            offset, size = makeself_payload(archive)
            payload = archive.with_name(archive.name + ".tar.gz")
            try:
                with open(archive, "rb") as src, open(payload, "wb") as dst:
                    src.seek(offset)
                    shutil.copyfileobj(src, dst)
                return extract(payload, dest, exe, max_files, max_bytes)
            finally:
                payload.unlink(missing_ok=True)
        elif zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as z:
                infos = z.infolist()
                partial.mkdir(parents=True)
                _check([(i.filename, i.file_size) for i in infos], partial, max_files, max_bytes)
                z.extractall(partial)
        else:
            with tarfile.open(archive, "r:*") as t:      # gzip or xz (y-cruncher's Linux build)
                members = t.getmembers()
                bad = [m.name for m in members if not (m.isfile() or m.isdir())]
                if bad:
                    raise ValueError(f"links or special files in the archive: {bad[:3]}")
                partial.mkdir(parents=True)
                _check([(m.name, m.size) for m in members], partial, max_files, max_bytes)
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
    why = download_verified(plan["url"], zip_path, plan["size"], plan.get("sha256", ""), opener, progress=progress,
                          max_bytes=MAX_DOWNLOAD_BYTES, md5=plan.get("md5"))
    if why:
        return Outcome(False, f"{name} was not installed: {why}.")
    why = (place_single(zip_path, dest, plan["exe"]) if plan.get("single") else
           extract(zip_path, dest, plan["exe"], int(plan.get("max_files", MAX_EXTRACTED_FILES)),
                   int(plan.get("max_unpacked", MAX_EXTRACTED_BYTES))))
    try:
        zip_path.unlink()                          # verified and extracted: the folder is what is kept
    except OSError:
        pass
    if why:
        return Outcome(False, f"{name} was not installed: {why}.")
    return Outcome(True, f"{name} installed in {dest} (size and {'SHA-256' if plan.get('sha256') else 'MD5'} "
                         "verified).")
