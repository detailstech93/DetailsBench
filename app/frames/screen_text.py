"""Read the words on the screen with Windows' own OCR (Windows.Media.Ocr), to check a game's menu before
each key is pressed (data/game_start_keys.py "steps").

Nothing is installed: Windows 10/11 ship the OCR engine and the recognizer of each display language (on this
computer, 2026-10-18: ar-SA, en-GB, en-US; about 0.8 s per read). Windows PowerShell 5.1 reaches it through
WinRT; the script is passed with -EncodedCommand (no file, no execution policy change). The screenshot is the
primary screen through GDI (app/simulation/desktop.py); a game in exclusive full screen may come out black,
then no words are seen and the step says so.

Linux (owner, 2026-10-19): Tesseract, the open-source OCR engine every distribution packages (tesseract-ocr), run
as "tesseract <image> stdout -l eng --psm 11 tsv": its TSV output has one row per word with its box and its
block / paragraph / line numbers (level 5 = a word), so the same lines and word boxes come back as on Windows.
--psm 11 ("sparse text") finds text scattered over a game's menu. Installed with the app's Install click
(pkexec + the package manager), like MangoHud. The screenshot comes from the X11 desktop.
"""

import base64
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

OCR_TIMEOUT_SECONDS = 30
TESSERACT_ARGS = ["stdout", "-l", "eng", "--psm", "11", "tsv"]

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and
    $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('__PATH__')) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US'))
if ($engine -eq $null) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($engine -eq $null) { [Console]::Error.WriteLine('no OCR language is installed in Windows'); exit 3 }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$stream.Dispose()
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
foreach ($line in $result.Lines) {
    $parts = @($line.Text)
    foreach ($w in $line.Words) {
        $r = $w.BoundingRect
        $parts += ('{0}|{1}|{2}|{3}|{4}' -f [int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height, $w.Text)
    }
    [Console]::Out.WriteLine($parts -join [char]0x1f)
}
"""
WORD_SEPARATOR = "\x1f"     # after each line's text: "x|y|width|height|word" of each of its words (screen pixels)


class Line(str):
    """A text line read on the screen; .words holds (word, x, y, width, height) of each of its words."""
    words: Tuple[Tuple[str, int, int, int, int], ...] = ()


def parse_line(raw: str) -> Line:
    text, *boxes = raw.split(WORD_SEPARATOR)
    line = Line(text.strip())
    words = []
    for box in boxes:
        parts = box.split("|", 4)
        try:
            words.append((parts[4], int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])))
        except (IndexError, ValueError):
            continue
    line.words = tuple(words)
    return line


def word_center(lines: List[str], word: str) -> Optional[Tuple[int, int]]:
    """Screen point in the middle of the first word containing `word` (case-insensitive), or None."""
    for line in lines:
        for text, x, y, w, h in getattr(line, "words", ()):
            if word.lower() in text.lower():
                return x + w // 2, y + h // 2
    return None


def ocr_command(image_path) -> List[str]:
    """The PowerShell command that prints the image's text lines (UTF-8, one per line)."""
    path = str(Path(image_path).resolve()).replace("'", "''")
    script = _SCRIPT.replace("__PATH__", path)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def parse_tsv(text: str) -> List[Line]:
    """Tesseract's TSV (level, page, block, par, line, word, left, top, width, height, conf, text) -> lines."""
    lines: dict = {}
    for row in text.splitlines()[1:]:
        cols = row.split("	")
        if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():
            continue
        try:
            key = tuple(int(c) for c in cols[1:5])
            box = tuple(int(c) for c in cols[6:10])
        except ValueError:
            continue
        lines.setdefault(key, []).append((cols[11].strip(),) + box)
    out = []
    for key in sorted(lines):
        words = lines[key]
        line = Line(" ".join(w[0] for w in words))
        line.words = tuple(words)
        out.append(line)
    return out


def read_image_tesseract(image_path, run=subprocess.run, which=shutil.which) -> Tuple[List[str], Optional[str]]:
    tesseract = which("tesseract")
    if not tesseract:
        return [], "Tesseract (the OCR program for Linux) is not installed"
    try:
        done = run([tesseract, str(image_path)] + TESSERACT_ARGS, capture_output=True,
                   timeout=OCR_TIMEOUT_SECONDS, shell=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [], f"Tesseract did not answer ({e})"
    if done.returncode != 0:
        said = done.stderr.decode("utf-8", "replace").strip().splitlines()
        return [], "Tesseract failed: " + (said[-1] if said else f"exit code {done.returncode}")
    return [line for line in parse_tsv(done.stdout.decode("utf-8", "replace")) if line], None


def _on_windows() -> bool:
    return os.name == "nt"


def read_image(image_path, run=subprocess.run) -> Tuple[List[str], Optional[str]]:
    """(text lines, error) of one image."""
    if not _on_windows():
        return read_image_tesseract(image_path, run=run)
    try:
        done = run(ocr_command(image_path), capture_output=True, timeout=OCR_TIMEOUT_SECONDS, shell=False,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [], f"Windows OCR did not answer ({e})"
    if done.returncode != 0:
        said = done.stderr.decode("utf-8", "replace").strip().splitlines()
        return [], "Windows OCR failed: " + (said[-1] if said else f"exit code {done.returncode}")
    lines = [parse_line(raw) for raw in done.stdout.decode("utf-8", "replace").splitlines()]
    return [line for line in lines if line], None


def read_screen(desktop, image_path, run=subprocess.run) -> Tuple[List[str], Optional[str]]:
    """Screenshot of the primary screen, then its text lines."""
    try:
        desktop.screenshot(image_path)
    except Exception as e:
        return [], f"no screenshot ({e})"
    return read_image(image_path, run=run)


def find_word(lines: List[str], words) -> Optional[str]:
    """The first of words seen in the lines (case-insensitive, inside a line), or None."""
    text = "\n".join(lines).lower()
    for word in ([words] if isinstance(words, str) else words):
        if word.lower() in text:
            return word
    return None
