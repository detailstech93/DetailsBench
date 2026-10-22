"""LM Studio on Linux with one button (owner, 2026-10-20: the AI models only offered "Get LM Studio first").

LM Studio's official headless install (lmstudio.ai/docs, "llmster"): its install.sh, read here on 2026-10-20 (MIT,
version 0.0.25-1). It downloads llmster for this machine from llmster.lmstudio.ai, checks it with the .sha512 next
to it, and installs it into ~/.lmstudio (lms in ~/.lmstudio/bin): no administrator, nothing system-wide. It needs
the system's libatomic (Ubuntu: libatomic1) and stops without it, so the app installs that package first with the
package manager when it is missing (the owner types the password). The app runs it with --no-modify-path: the
owner's shell profiles are not changed; app/ai/lmstudio.py finds lms in ~/.lmstudio/bin itself.

Windows (owner, 2026-10-22: "Select all not installed" should install everything): the same llmster from LM
Studio's install.ps1 (read on 2026-10-22, version 0.0.25-1): x64 and ARM64, from llmster.lmstudio.ai, checked with
its .sha512, into ~/.lmstudio as the user; LMS_NO_MODIFY_PATH=1 leaves PATH alone.
"""

import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional

SCRIPT_URL = "https://lmstudio.ai/install.sh"
WINDOWS_SCRIPT_URL = "https://lmstudio.ai/install.ps1"
DOCS_URL = "https://lmstudio.ai/docs/developer/core/headless"
MAX_SCRIPT_BYTES = 256 * 1024
TIMEOUT = 1800                  # it downloads about 1 GB (the full build with its runtimes)


def libatomic_missing(run: Callable = subprocess.run) -> bool:
    try:
        done = run(["ldconfig", "-p"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        try:
            done = run(["/sbin/ldconfig", "-p"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False        # cannot tell: the script itself says so if it is missing
    return not any(line.strip().startswith("libatomic.so.1 ") for line in (done.stdout or "").splitlines())


def question(first: List[List[str]], windows: bool = False) -> str:
    text = ("Install LM Studio (its server, llmster, and the lms tool) for the AI tests?\n\n"
            f"The app downloads LM Studio's official install script ({WINDOWS_SCRIPT_URL if windows else SCRIPT_URL})"
            f" and runs it as you, the way "
            f"LM Studio's page says ({DOCS_URL}). It downloads llmster (about 1 GB) from llmster.lmstudio.ai, checks "
            "it with LM Studio's own SHA-512 and installs it into ~/.lmstudio. No administrator rights; your "
            + ("PATH" if windows else "shell profile") + " is not changed. By installing it you accept LM Studio's terms (lmstudio.ai/app-terms).")
    if first:
        text += ("\n\nIt needs the system library libatomic, which is missing: it is installed first (your "
                 "password):\n" + "\n".join(" ".join(c) for c in first))
    return text


def install(opener: Callable = urllib.request.urlopen, run: Callable = subprocess.run,
            data_dir: Optional[Path] = None, lms=None, windows: Optional[bool] = None):
    """Download install.sh (Windows: install.ps1) and run it. Returns winget.Outcome."""
    from app.ai.lmstudio import lms_path
    from app.frames.provision import USER_AGENT
    from app.install import winget
    from app.install.direct import tools_root
    lms = lms or lms_path
    windows = os.name == "nt" if windows is None else windows
    url = WINDOWS_SCRIPT_URL if windows else SCRIPT_URL
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with opener(request, timeout=60) as response:
            if not str(getattr(response, "url", url) or url).startswith("https://lmstudio.ai/"):
                return winget.Outcome(False, "LM Studio was not installed: the script came from another address.")
            script = response.read(MAX_SCRIPT_BYTES + 1)
    except Exception as e:
        return winget.Outcome(False, f"LM Studio was not installed: its install script could not be downloaded ({e}).")
    looks_right = (b"llmster.lmstudio.ai" in script and b"Install-Llmster" in script) if windows else         (script.startswith(b"#!/bin/sh") and b"lmstudio.ai" in script)
    if len(script) > MAX_SCRIPT_BYTES or not looks_right:
        return winget.Outcome(False, "LM Studio was not installed: the download is not LM Studio's install script.")
    folder = tools_root(data_dir) / "downloads"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("lmstudio-install.ps1" if windows else "lmstudio-install.sh")
    path.write_bytes(script)
    log = folder / "lmstudio-install.log"
    try:                        # not --quiet: the script then hides its own error messages too
        if windows:
            done = run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                        str(path)], capture_output=True, text=True, timeout=TIMEOUT,
                       env=dict(os.environ, LMS_NO_MODIFY_PATH="1"))
        else:
            done = run(["sh", str(path), "--no-modify-path"], capture_output=True, text=True, timeout=TIMEOUT,
                       env=dict(os.environ))
    except subprocess.TimeoutExpired:
        return winget.Outcome(False, f"LM Studio was not installed: its installer took more than {TIMEOUT // 60} minutes.")
    except OSError as e:
        return winget.Outcome(False, f"LM Studio was not installed: {e}.")
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    output = ((done.stdout or "") + (done.stderr or "")).strip()
    try:
        log.write_text(output, encoding="utf-8")
    except OSError:
        pass
    found = lms()
    if done.returncode == 0 and found:
        return winget.Outcome(True, f"LM Studio installed ({found}).")
    last = output.splitlines()[-1][:300] if output else "no output"
    return winget.Outcome(False, f"LM Studio was not installed: its installer ended with code {done.returncode} "
                                 f"({last}). The whole output is in {log}.")
