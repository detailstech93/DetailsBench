"""A problem report: one zip a tester sends us with a GitHub issue, so a failure can be seen and followed up.

Owner, 2026-10-23 (before going public): a "Report a problem" button, and the same offer after a test fails or the
app hits an error. Nothing is sent by the app: the zip is written to the reports folder and the tester's browser
opens a new GitHub issue (title and a short summary filled in), where they drag the zip. The issue is where we follow
it up (open / closed, comments).

The zip holds (each text scrubbed, see scrub()):
- report.txt: app version and commit, the system (OS, processor, graphics, memory), the tester's own words, and the
  failed runs (test, status, why)
- the app's log (app/app_log.py) and the last error, when there is one
- the failed tests' run folders: their text output only (command, stdout, stderr, results), the end of a long file
- settings.json without anything that looks like a password, token or pairing code

Scrubbed: the user's home folder and name, the computer's name, e-mail addresses and IP addresses.
"""

import getpass
import json
import platform
import re
import socket
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Mapping, Optional
from urllib.parse import urlencode

from app import __version__

REPO = "detailstech93/DetailsBench"
FAILED = ("failed", "timed_out", "invalid", "crashed")
TEXT_FILES = (".txt", ".log", ".json", ".md", ".xml", ".csv", ".cfg", ".ini")
MAX_FILE_BYTES = 256 * 1024          # a longer file: its end (where the error usually is)
MAX_RUN_FILES = 200
SECRET_KEY = re.compile(r"pass|token|secret|key|pair|code|auth|cookie", re.IGNORECASE)
# bounded lengths: on a long line without "@" an unbounded pattern backtracks for minutes (a 256 KB log, 2026-10-23)
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,8}")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ISSUE_BODY_LIMIT = 4000              # GitHub's address bar limit leaves room for the title and the rest


def _who() -> List[str]:
    names = []
    for get in (getpass.getuser, socket.gethostname, platform.node):
        try:
            value = get()
        except Exception:
            value = ""
        if value and len(value) > 2 and value not in names:
            names.append(value)
    return names


def scrub(text: str, home: Optional[str] = None, names: Optional[Iterable[str]] = None) -> str:
    """The text without the tester's home folder, user and computer names, e-mail and IP addresses."""
    home = str(Path.home()) if home is None else home
    if home and len(home) > 3:
        for form in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
            text = re.sub(re.escape(form), "~", text, flags=re.IGNORECASE)
    text = EMAIL.sub("<email>", text)
    text = IPV4.sub(lambda m: m.group(0) if m.group(0).startswith(("0.", "127.")) else "<ip>", text)
    for name in (_who() if names is None else names):
        if name and len(name) > 2:
            text = re.sub(rf"(?<![\w]){re.escape(name)}(?![\w])", "<user>", text, flags=re.IGNORECASE)
    return text


def clean_settings(settings: Mapping) -> dict:
    """settings.json without values whose key looks secret (passwords, tokens, the remote pairing code)."""
    out = {}
    for key, value in settings.items():
        if SECRET_KEY.search(str(key)):
            out[key] = "<removed>"
        elif isinstance(value, Mapping):
            out[key] = clean_settings(value)
        else:
            out[key] = value
    return out


def system_lines(info: Optional[Mapping] = None) -> List[str]:
    info = info or {}
    lines = [f"DetailsBench {__version__}", f"OS: {platform.platform()}",
             f"Machine: {platform.machine()}", f"Python: {sys.version.split()[0]}"]
    try:
        from app.frames.provision import native_arch
        lines.append(f"Processor architecture: {native_arch()}")
    except Exception:
        pass
    for key, label in (("cpu_brand", "CPU"), ("core_count", "Cores"), ("ram_size_gb", "RAM (GB)"), ("gpu_name", "GPU")):
        if info.get(key) not in (None, ""):
            lines.append(f"{label}: {info[key]}")
    return lines


def failed_runs(runs: Iterable[Mapping]) -> List[Mapping]:
    """Runs that failed, hung, crashed or gave no usable result (a skip with its reason is not a problem)."""
    return [r for r in runs if str(r.get("status")) in FAILED]


def _tail(path: Path) -> str:
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > MAX_FILE_BYTES:
            f.seek(size - MAX_FILE_BYTES)
        data = f.read()
    text = data.decode("utf-8", "replace")
    return (f"[... the first {size - MAX_FILE_BYTES:,} bytes left out ...]\n" + text) if size > MAX_FILE_BYTES else text


def run_files(session_folder: Optional[Path], tests: Iterable[str]) -> List[Path]:
    """The text files of the failed tests' run folders (folders named <test>_w0, <test>_r1, ...)."""
    if session_folder is None or not session_folder.is_dir():
        return []
    tests = [t for t in tests if t]
    out = []
    for folder in sorted(p for p in session_folder.iterdir() if p.is_dir()):
        if any(folder.name == t or folder.name.startswith(t + "_") for t in tests):
            out += [f for f in sorted(folder.rglob("*")) if f.is_file() and f.suffix.lower() in TEXT_FILES]
    return out[:MAX_RUN_FILES]


def session_folder(runs_root: Path, session_id: Optional[int]) -> Optional[Path]:
    if session_id is None or not runs_root.is_dir():
        return None
    found = sorted(runs_root.glob(f"session_{session_id}_*"))
    return found[-1] if found else None


def report_text(what_happened: str, system: List[str], failed: List[Mapping], session_id: Optional[int],
                commit: Optional[str], error: str = "") -> str:
    lines = ["DetailsBench problem report", f"Made: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             f"Commit: {commit or 'unknown'}", ""]
    lines += ["What happened (the tester's words):", what_happened.strip() or "(not written)", ""]
    lines += ["System:"] + [f"  {s}" for s in system] + [""]
    if session_id is not None:
        lines.append(f"Session: #{session_id}")
    if failed:
        lines.append("Runs that did not complete:")
        for r in failed:
            lines.append(f"  {r.get('benchmark_name')} run {r.get('run_index')}"
                         f"{' (warm-up)' if r.get('is_warmup') else ''}: {r.get('status')}: {r.get('note') or ''}")
    if error:
        lines += ["", "Last error of the app:", error.rstrip()]
    return "\n".join(lines) + "\n"


def make_zip(out_dir: Path, what_happened: str = "", runs: Iterable[Mapping] = (), session_id: Optional[int] = None,
             data_dir: Optional[Path] = None, system_info: Optional[Mapping] = None, commit: Optional[str] = None,
             home: Optional[str] = None, names: Optional[Iterable[str]] = None) -> Path:
    """Writes the zip to out_dir and returns its path. Reads only; nothing is sent."""
    from app import app_log
    names = list(_who() if names is None else names)

    def clean(text: str) -> str:
        return scrub(text, home, names)

    failed = failed_runs(runs)
    error = app_log.last_error(data_dir) if data_dir else ""
    text = report_text(what_happened, system_lines(system_info), failed, session_id, commit, error)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"DetailsBench problem report {datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.txt", clean(text))
        if data_dir:
            for log in app_log.log_files(data_dir):
                z.writestr(f"log/{log.name}", clean(_tail(log)))
            folder = session_folder(data_dir / "automation_runs", session_id)
            for f in run_files(folder, {str(r.get("benchmark_name")) for r in failed}):
                try:
                    z.writestr(f"runs/{f.relative_to(folder).as_posix()}", clean(_tail(f)))
                except OSError:
                    continue
            settings = data_dir / "settings.json"
            if settings.is_file():
                try:
                    data = json.loads(settings.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        z.writestr("settings.json", clean(json.dumps(clean_settings(data), indent=2)))
                except (OSError, ValueError):
                    pass
    return path


def issue_url(what_happened: str, failed: List[Mapping], zip_name: str, system: Optional[List[str]] = None) -> str:
    """A new GitHub issue with the title and a short summary filled in; the zip is dragged in by the tester."""
    tests = sorted({str(r.get("benchmark_name")) for r in failed})
    title = ("Problem: " + ", ".join(tests[:3]) + (" ..." if len(tests) > 3 else "")) if tests else "Problem report"
    body = ["**What happened**", scrub(what_happened.strip()) or "(please write what you did and what you saw)", "",
            "**System**"] + [f"- {s}" for s in (system or system_lines())[:5]]
    if failed:
        body += ["", "**Runs that did not complete**"]
        body += [f"- {r.get('benchmark_name')}: {r.get('status')}: {scrub(str(r.get('note') or ''))[:160]}"
                 for r in failed[:10]]
    body += ["", f"**Please drag the file `{zip_name}` here** (it was saved in your reports folder)."]
    text = "\n".join(body)
    if len(text) > ISSUE_BODY_LIMIT:
        text = text[:ISSUE_BODY_LIMIT] + "\n..."
    return f"https://github.com/{REPO}/issues/new?" + urlencode({"title": title, "body": text, "labels": "bug"})
