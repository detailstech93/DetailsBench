"""A fixed set of LM Studio models measured with llama-benchy, one test per model, so results compare between PCs.

The free "llama-benchy (LM Studio)" test measures whatever model the owner picks. These tests always use the same
model, the same quantization and the same load settings:

- small: Qwen3.5 4B, Q4_K_M (any PC, also laptops; LM Studio lists 3.75 GB)
- medium: Gemma 4 12B QAT, Q4_0 (graphics card with 8-12 GB; 7.2 GB)
- large: Qwen3.8 27B, Q4_K_M (graphics card with 24 GB; 17.7 GB)

Install (one button, after the owner saw name and size): LM Studio's own CLI downloads the model from LM Studio's
catalog, `lms get <key>@<quant> --gguf -y` (lms get --help, LM Studio's CLI on this computer, 2026-10-19).
Installed = `lms ls --json` lists the model key with that quantization as its selected variant (a model with another
variant selected is named, never measured as if it were the fixed one).

Run: LM Studio's server must answer on localhost:1234 (`lms server start` when it does not; it stays on this PC).
The app loads its own copy, `lms load <key> --identifier pcbm-<test id> -c 4096 --gpu max --parallel 1 -y`
(context 4096, every layer on the graphics card, one request at a time), runs llama-benchy against that identifier
(pp 512, tg 128, depth 0, concurrency 1, 3 runs) and always unloads only that identifier afterwards (`lms unload`).
Other loaded models are not touched, but they share the graphics card: the warning says so. A model that does not
fit with every layer on the card fails to load and the run says so (no score). llama-benchy counts tokens with the
gpt2 tokenizer on every PC (--tokenizer gpt2: the identifier is not a Hugging Face name, and the same tokenizer
everywhere keeps the counts comparable). Real runs on this computer 2026-10-19 (Gemma 4 12B): first with the NVIDIA
card "lost" (nvidia-smi), so on the CPU (8.6 tok/s, not a score); after a restart on the RTX 5090 Laptop GPU: loaded in
9.8 s, pp 1,664.7 tok/s, tg 83.8 tok/s, unloaded after. Only Gemma 4 12B is marked verified (the others' downloads
and loads have not been seen here yet).

The owner is not bound to these three: "AI models..." on the Tests screen adds any LM Studio model and quantization
(kept in settings.json, "ai_fixed_models"); each becomes its own test with the same fixed settings, and can be
removed again. The three above stay.
"""

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from app.ai import llama_benchy, lmstudio
from app.automation.workload import RunContext, WorkloadResult

# test id: (title, LM Studio model key, quantization, size as LM Studio lists it)
MODELS = {
    "llama_benchy_qwen35_4b": ("Qwen3.5 4B", "qwen/qwen3.5-4b", "Q4_K_M", "3.75 GB"),
    "llama_benchy_gemma4_12b": ("Gemma 4 12B QAT", "google/gemma-4-12b-qat", "Q4_0", "7.2 GB"),
    "llama_benchy_qwen38_27b": ("Qwen3.8 27B", "qwen/qwen3.8-27b", "Q4_K_M", "17.7 GB"),
}
CONTEXT, PP, TG, RUNS = 4096, 512, 128, 3
LOAD_SECONDS, GET_SECONDS = 600, 6 * 3600
ANSI = re.compile(r"\x1b\[[0-9;]*m")
# a dropped download: lms keeps the part it has and resumes when `lms get` runs again (owner, 2026-10-20: Qwen3.8 27B
# stopped at 57% with "Download failed: Timed-out. Please try to resume.")
GET_ATTEMPTS = 4
RESUMABLE = re.compile(r"timed?[- ]?out|try to resume|ECONNRESET|socket hang up|network", re.I)
TERMINAL = re.compile(r"\x1b(\[[0-9;?]*[A-Za-z]|[()][A-Za-z0-9])")
PROGRESS = re.compile(r"\d+(\.\d+)?%\s*\|")
_LS: List[Tuple[float, object]] = []          # (time, `lms ls --json` result): the Tests screen asks once per model
_LS_ERROR: List[str] = []                      # why the last `lms ls` gave no list
CUSTOM_KEY = "ai_fixed_models"
KEY = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
QUANT = re.compile(r"^[A-Za-z0-9_]{1,24}$")


def custom_id(key: str, quant: str) -> str:
    return "llama_benchy_my_" + re.sub(r"[^a-z0-9]+", "_", f"{key}_{quant}".lower()).strip("_")


def custom_models(data_dir: Optional[Path] = None) -> Dict[str, Tuple[str, str, str, str]]:
    """The owner's own models from settings.json (each checked; a bad entry is left out)."""
    from app.paths import get_data_dir, load_settings
    items = load_settings(Path(data_dir or get_data_dir()))[0].get(CUSTOM_KEY)
    out = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        key, quant = str(item.get("key", "")), str(item.get("quant", "")).upper()
        if KEY.match(key) and QUANT.match(quant):
            title = str(item.get("title") or key.split("/")[-1])[:80]
            out[custom_id(key, quant)] = (title, key, quant, str(item.get("size") or "size not known")[:40])
    return out


def all_models(data_dir: Optional[Path] = None) -> Dict[str, Tuple[str, str, str, str]]:
    return {**MODELS, **{k: v for k, v in custom_models(data_dir).items() if k not in MODELS}}


def add_model(key: str, quant: str, title: str = "", size: str = "", data_dir: Optional[Path] = None) -> str:
    """Adds one of the owner's models; returns its test id. ValueError for a key or quantization that is not one."""
    from app.paths import get_data_dir, load_settings, save_setting
    quant = quant.upper()
    if not KEY.match(key) or not QUANT.match(quant):
        raise ValueError(f"not an LM Studio model key and quantization: {key}@{quant}")
    test_id = custom_id(key, quant)
    if test_id in MODELS or any(v[1] == key and v[2] == quant for v in MODELS.values()):
        raise ValueError(f"{key} {quant} is already one of the fixed models")
    folder = Path(data_dir or get_data_dir())
    items = load_settings(folder)[0].get(CUSTOM_KEY)
    items = [i for i in (items if isinstance(items, list) else [])
             if isinstance(i, dict) and custom_id(str(i.get("key", "")), str(i.get("quant", ""))) != test_id]
    items.append({"key": key, "quant": quant, "title": title or key.split("/")[-1], "size": size})
    save_setting(folder, CUSTOM_KEY, items)
    return test_id


def remove_model(test_id: str, data_dir: Optional[Path] = None) -> bool:
    """Removes one of the owner's models (the three fixed ones cannot be removed). Earlier results stay."""
    from app.paths import get_data_dir, load_settings, save_setting
    folder = Path(data_dir or get_data_dir())
    items = load_settings(folder)[0].get(CUSTOM_KEY)
    items = items if isinstance(items, list) else []
    kept = [i for i in items if not (isinstance(i, dict) and
                                     custom_id(str(i.get("key", "")), str(i.get("quant", ""))) == test_id)]
    if len(kept) == len(items):
        return False
    save_setting(folder, CUSTOM_KEY, kept)
    return True


def on_disk(models: list) -> List[Tuple[str, str, str, str]]:
    """(key, quantization, title, size) of every LLM variant `lms ls --json` lists, for the "AI models..." choice."""
    out = []
    for m in models:
        if not isinstance(m, dict) or m.get("type") != "llm" or not m.get("modelKey"):
            continue
        size = m.get("sizeBytes")
        size = f"{size / 1e9:.1f} GB" if isinstance(size, (int, float)) and size > 0 else ""
        for v in m.get("variants") or [f"{m['modelKey']}@{(m.get('quantization') or {}).get('name', '')}"]:
            key, _, quant = str(v).partition("@")
            if key and quant:
                out.append((key, quant.upper(), str(m.get("displayName") or key), size))
    return out


def identifier(test_id: str) -> str:
    return "pcbm-" + test_id


def variant(key: str, quant: str) -> str:
    return f"{key}@{quant.lower()}"


def get_command(lms: str, key: str, quant: str) -> List[str]:
    return [lms, "get", variant(key, quant), "--gguf", "-y"]


def load_command(lms: str, key: str, ident: str) -> List[str]:
    return [lms, "load", key, "--identifier", ident, "-c", str(CONTEXT), "--gpu", "max", "--parallel", "1", "-y"]


def unload_command(lms: str, ident: str) -> List[str]:
    return [lms, "unload", ident]


STOPPED = "stopped by user"


def _run(argv: List[str], timeout: float, stop=None) -> Tuple[int, str]:
    """`stop` (threading.Event): the Stop button ends the command (a download: lms keeps the part it has)."""
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    except OSError as e:
        return -1, str(e)
    end = time.monotonic() + timeout
    while True:
        try:
            out, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            stopped = stop is not None and stop.is_set()
            if stopped or time.monotonic() > end:
                proc.kill()
                proc.communicate()
                return -1, STOPPED if stopped else f"no answer after {timeout:g} s"
    text = ANSI.sub("", (out + err).decode("utf-8", "replace"))
    return proc.returncode, text.strip()


def list_models(lms: Optional[Path], run: Callable = _run, now: Callable[[], float] = time.monotonic) -> list:
    """`lms ls --json` (kept 10 s), [] when LM Studio is missing or answers something else (why: list_error()).

    On Linux LM Studio is the llmster service, which is not running until something starts it: the full run on
    Linux (2026-10-21) showed the three fixed models as "not installed" although all three were on disk, and LM
    Studio's server log starts only when the run began. So when `lms ls` gives no list, `lms server start` (its own
    command) runs once and the list is read again."""
    if lms is None:
        return []
    if _LS and now() - _LS[0][0] < 10:
        return _LS[0][1]

    def read() -> Tuple[Optional[list], str]:
        code, text = run([str(lms), "ls", "--json"], 30)
        try:
            data = json.loads(text) if code == 0 else None
        except ValueError:
            data = None
        if isinstance(data, list):
            return data, ""
        lines = [l for l in text.splitlines() if l.strip()]
        return None, (lines[-1][:200] if lines else f"exit code {code}")
    data, why = read()
    if data is None:
        run([str(lms), "server", "start"], 60)
        data, why = read()
    _LS_ERROR[:] = [] if data is not None else [why]
    data = data if data is not None else []
    _LS[:] = [(now(), data)]
    return data


def list_error() -> Optional[str]:
    """Why the last `lms ls` gave no list, or None."""
    return _LS_ERROR[0] if _LS_ERROR else None


def installed(models: list, key: str, quant: str) -> Tuple[bool, str]:
    """(True, "") when the key is on disk with this quantization selected, else (False, why)."""
    entry = next((m for m in models if isinstance(m, dict) and m.get("modelKey") == key), None)
    if entry is None:
        return False, f"{key} {quant} is not installed (Install button)"
    want = variant(key, quant)
    if want not in [str(v).lower() for v in entry.get("variants") or []]:
        return False, f"{key} is on disk but not as {quant}: it is not installed (Install button)"
    if str(entry.get("selectedVariant", want)).lower() != want:
        return False, (f"{key} is on disk as {quant}, but LM Studio has {entry.get('selectedVariant')} selected: "
                       f"select {quant} in LM Studio (My Models) so this test loads the fixed one")
    return True, ""


def fit_warning(test_id: str, fit: Optional[Callable[[str], str]] = None) -> str:
    """'' or why the model most likely does not fit on this computer (app/ai/model_fit.py)."""
    from app.ai import model_fit
    return (fit or model_fit.warning_here)(all_models()[test_id][3])


def install_text(test_id: str, fit: Optional[Callable[[str], str]] = None) -> str:
    """The question before the download; a model that most likely does not fit here says so first (the owner
    decides: app/ai/model_fit.py)."""
    title, key, quant, size = all_models()[test_id]
    warning = fit_warning(test_id, fit)
    return ((warning + "\n\n" if warning else "")
            + f"Download {title} ({quant}, about {size} as LM Studio lists it) with LM Studio's own command:\n\n"
            f"lms {' '.join(get_command('', key, quant)[1:])}\n\nIt comes from LM Studio's model catalog (Hugging "
            "Face) into LM Studio's models folder. The model's own license applies (shown on lmstudio.ai/models).")


def install(test_id: str, progress=lambda text: None, lms: Optional[Path] = None, run: Callable = _run, stop=None):
    """The Install button (winget.Outcome): lms get, then the model must be listed. `stop` (threading.Event): the
    Stop button ends the download (owner, 2026-10-22)."""
    from app.install import winget
    title, key, quant, _size = all_models()[test_id]
    lms = lms or lmstudio.lms_path()
    if lms is None:
        return winget.Outcome(False, "LM Studio is not installed (its CLI lms was not found in ~/.lmstudio/bin).")
    for attempt in range(1, GET_ATTEMPTS + 1):
        progress(f"Downloading {title} {quant} with LM Studio (several GB, this can take a while)..."
                 + (f" Resuming after a dropped download (try {attempt} of {GET_ATTEMPTS})." if attempt > 1 else ""))
        code, text = (run(get_command(str(lms), key, quant), GET_SECONDS) if stop is None
                      else run(get_command(str(lms), key, quant), GET_SECONDS, stop))
        if stop is not None and stop.is_set():
            return winget.Outcome(False, f"{title}: the download was stopped (LM Studio keeps the part it has and "
                                         "resumes it at the next Install).")
        _LS.clear()
        ok, why = installed(list_models(lms, run), key, quant)
        if ok:
            return winget.Outcome(True, f"{title} {quant} is ready.")
        if not RESUMABLE.search(text or ""):
            break
    last = lms_error(text)
    return winget.Outcome(False, f"{title} was not installed: {why}" + (f" (lms said: {last[:200]})" if last else
                                                                         f" (exit code {code})"))


def lms_error(text: str) -> str:
    """The line worth showing from lms' output: its error, without the progress bar and terminal codes."""
    clean = TERMINAL.sub("", text or "").replace("\r", "\n")
    lines = [l.strip() for l in clean.splitlines() if l.strip() and not PROGRESS.search(l)]
    errors = [l for l in lines if "error" in l.lower()]
    line = (errors or lines or [""])[-1]
    return line[line.lower().find("error"):] if "error" in line.lower() else line


class FixedModelWorkload(llama_benchy.LlamaBenchyWorkload):
    VERIFIED = ("llama_benchy_gemma4_12b",)     # a real run on this computer (2026-10-19, RTX 5090 Laptop GPU)
    asks_settings = False             # fixed model and settings: the free test's dialog is not shown
    unload_jit = False                # its own copy is unloaded by run() below
    tokenizer = "gpt2"                # --tokenizer gpt2 on every PC (recorded in ai_run.json)

    def __init__(self, test_id: str, exe: Optional[Path] = None, lms: Optional[Path] = None,
                 run: Callable = _run, model: Optional[Tuple[str, str, str, str]] = None,
                 free_gb: Callable[[], Optional[float]] = None, card_gb: Callable[[], Optional[float]] = None,
                 **kwargs):
        title, self.key, self.quant, self.size = model or all_models()[test_id]
        super().__init__(identifier(test_id), pp=PP, tg=TG, depths=(0,), runs=RUNS, exe=exe, **kwargs)
        self.id = test_id
        self.verified = test_id in self.VERIFIED
        self.title = title
        self.display_name = f"llama-benchy: {title} ({self.quant})"
        self.lms = lms if lms is not None else lmstudio.lms_path()
        self._lms_run = run
        self._free_gb, self._card_gb = free_gb, card_gb

    def availability(self) -> Tuple[bool, str]:
        if self.lms is None:
            return False, "LM Studio is not installed (its CLI lms was not found)"
        if not self.exe:
            # not "not installed": the Tests screen would offer the Install button, which downloads only the model
            return False, ("llama-benchy is missing from the app's Python: pip install -r requirements.txt (or set "
                           "PCBM_LLAMA_BENCHY)")
        models = list_models(self.lms, self._lms_run)
        if not models and list_error():     # LM Studio did not answer: not a model to download
            return False, f"LM Studio did not list its models (lms ls): {list_error()}"
        return installed(models, self.key, self.quant)

    def memory_short(self) -> Optional[str]:
        """Why the model is not loaded now, or None. Where the graphics share the computer's memory (no card with its
        own memory) the model must fit in the memory free right now (Fedora, 2026-10-23: Gemma 4 12B on 16 GB with
        another model still loaded: the computer ran out of memory and the app was closed with it). A card with its
        own memory: LM Studio itself refuses a model that does not fit there."""
        from app.ai import model_fit
        model = model_fit.size_gb(self.size)
        if model is None or (self._card_gb or model_fit.card_gb)() is not None:
            return None
        free = (self._free_gb or free_memory_gb)()
        need = model * model_fit.NEEDS
        if free is None or need <= free:
            return None
        return (f"{llama_benchy.NOT_ENOUGH_MEMORY}: {self.title} needs about {need:.1f} GB with its context and "
                f"{free:.1f} GB of memory is free now (the graphics use the computer's memory), so it was not "
                "loaded and the computer does not run out of memory")

    def _command(self, result_path: str) -> List[str]:
        return super()._command(result_path) + ["--tokenizer", "gpt2"]

    def describe_command(self) -> Optional[str]:
        if not (self.exe and self.lms):
            return None
        return " && ".join(subprocess.list2cmdline(c) for c in (
            load_command(str(self.lms), self.key, self.model),
            self._command("<run folder>/" + llama_benchy.RESULT_FILE),
            unload_command(str(self.lms), self.model)))

    def _server(self) -> Optional[str]:
        """None when LM Studio's server answers (started here if needed), else why not."""
        try:
            self._read_models(self.base_url)
            return None
        except (OSError, ValueError):
            pass
        code, text = self._lms_run([str(self.lms), "server", "start"], 60)
        try:
            self._read_models(self.base_url)
            return None
        except (OSError, ValueError):
            return f"LM Studio's server does not answer on {self.base_url} (lms server start: {text[-200:] or code})"

    def run(self, ctx: RunContext) -> WorkloadResult:
        available, why = self.availability()
        if not available:
            return WorkloadResult(status="skipped", error=f"{self.display_name}: {why}")
        problem = self._server()
        if problem:
            return WorkloadResult(status="failed", error=problem)
        lms = str(self.lms)
        self._lms_run(unload_command(lms, self.model), 60)        # a copy left by a stopped run
        short = self.memory_short()
        if short:
            return WorkloadResult(status="skipped", error=short)
        load = load_command(lms, self.key, self.model)
        code, text = self._lms_run(load, LOAD_SECONDS)
        folder = Path(ctx.output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "lms_load.txt").write_text(subprocess.list2cmdline(load) + "\n\n" + text, encoding="utf-8")
        try:
            if code != 0:
                last = text.splitlines()[-1] if text else f"exit code {code}"
                return WorkloadResult(status="failed", error=f"LM Studio could not load {self.title} with every "
                                      f"layer on the graphics card and context {CONTEXT}: {last[:300]}",
                                      command=load, exit_code=code)
            result = super().run(ctx)
        finally:
            self._lms_run(unload_command(lms, self.model), 60)
        if result.status == "completed":
            parts = [("Model", f"{self.key} {self.quant}"), ("Context", str(CONTEXT)), ("GPU offload", "max"),
                     ("Parallel", "1"), ("pp/tg", f"{PP}/{TG}"), ("Runs", str(RUNS))]
            result.note = "; ".join(f"{k}: {v}" for k, v in parts)
        return result


def free_memory_gb() -> Optional[float]:
    try:
        import psutil
        return psutil.virtual_memory().available / 1024 ** 3
    except Exception:
        return None


def model_workloads() -> list:
    return [FixedModelWorkload(t, model=m) for t, m in all_models().items()]
