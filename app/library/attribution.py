"""Which part a result belongs to (hardware results library, phase 2, owner 2026-10-21/24).

attribute() is pure: it takes the test, the PC's parts saved with the session (app/environment/hardware.py) and
what the run recorded, and says:
- part: cpu | gpu | memory | storage | system, with an identity key, so the same part on two PCs is one part
  (CPU: model + cores/threads; GPU: PCI vendor:device + subsystem + VRAM; RAM: size + speed + modules; drive:
  model + size; system: the device's maker + model)
- variant: a laptop GPU's power limit ("@ 175 W"): the same name runs very differently
- linked: other parts that change the score (a game is CPU-bound at low settings)
- context: the software the score depends on: the part's driver or firmware, the OS, the browser (web tests),
  the AI runtime
- library: False for what stays with the session only (stress tests, the app's self-test, and games until the
  Games section is ready, owner 2026-10-24), with why
- ranked: False when the result is filed but not compared with the others (AI with part of the model on the CPU or
  an unknown GPU offload, WSL, a VM, emulated, on battery), with flags saying why
- guessed: what was picked without the run saying so (which of two graphics cards)
"""

import re
from typing import List, Mapping, Optional

from data.test_catalog import GROUP_PARTS, TEST_PARTS

STRESS_PARTS = {"stress_cpu": "cpu", "stress_gpu": "gpu", "stress_cpu_gpu": "system"}
GAMES_READY = False             # owner, 2026-10-24: game results join the library once the Games section is ready
GPU_BACKEND = re.compile(r"cuda|vulkan|rocm|hip|metal|opencl|sycl|directml|\bdml\b", re.I)
NOISE = re.compile(r"\((?:R|TM|C)\)|\bCPU\b|\bProcessor\b|@\s*[\d.]+\s*GHz|\d+-Core", re.I)


def clean_name(name: Optional[str]) -> Optional[str]:
    """'Intel(R) Core(TM) Ultra 9 275HX' -> 'Intel Core Ultra 9 275HX'."""
    if not name:
        return None
    return " ".join(NOISE.sub(" ", name).split()) or None


def _key(*parts) -> str:
    return ":".join(re.sub(r"\s+", "-", str(p).strip().lower()) if p not in (None, "") else "?" for p in parts)


def _os(hw: Mapping) -> Optional[str]:
    osd = hw.get("os") or {}
    text = " ".join(str(x) for x in (osd.get("name"), osd.get("build") or osd.get("kernel")) if x)
    return text or None


def cpu_part(hw: Mapping) -> Optional[dict]:
    cpu = hw.get("cpu") or {}
    name = clean_name(cpu.get("name"))
    if not name:
        return None
    return {"part": "cpu", "part_key": _key("cpu", name, f"{cpu.get('cores')}c{cpu.get('threads')}t"),
            "part_name": name, "variant": None,
            "context": {"bios": (hw.get("device") or {}).get("bios"), "os": _os(hw)}}


def pick_gpu(hw: Mapping, api: Optional[str] = None):
    """(card, guessed note): a CUDA test runs on the NVIDIA card; otherwise the discrete card (NVIDIA, AMD, or
    Intel with its own memory) is taken when there are two."""
    gpus = [g for g in hw.get("gpus") or [] if g.get("vendor_id") or g.get("name")]
    if api == "cuda":
        gpus = [g for g in gpus if g.get("vendor_id") == "10de"]
    if not gpus:
        return None, None
    if len(gpus) == 1:
        return gpus[0], None

    def rank(g):
        discrete = g.get("vendor_id") in ("10de", "1002") or (g.get("vram_mb") or 0) >= 4096
        return (discrete, g.get("vram_mb") or 0)
    best = max(gpus, key=rank)
    return best, f"graphics card taken as {best.get('name') or best.get('device_id')} (the discrete one of {len(gpus)})"


def gpu_part(hw: Mapping, gpu: Mapping) -> dict:
    name = clean_name(gpu.get("name")) or f"GPU {gpu.get('vendor_id')}:{gpu.get('device_id')}"
    vram = round((gpu.get("vram_mb") or 0) / 1024) or None
    laptop = "laptop" in name.lower() or (hw.get("device") or {}).get("kind") == "laptop"
    limit = gpu.get("power_limit_w")
    driver = gpu.get("driver")
    if not driver and gpu.get("driver_name"):           # in-kernel Linux driver: its version is the kernel's
        driver = f"{gpu['driver_name']} (kernel {(hw.get('os') or {}).get('kernel')})"
    return {"part": "gpu", "part_key": _key("gpu", gpu.get("vendor_id"), gpu.get("device_id"), gpu.get("subsys_id"),
                                            f"{vram}gb" if vram else None),
            "part_name": name + (f" {vram} GB" if vram and "laptop" not in name.lower() else ""),
            "variant": f"{name} @ {limit:.0f} W" if laptop and limit else None,
            "context": {"driver": driver, "os": _os(hw)}}


def memory_part(hw: Mapping) -> Optional[dict]:
    ram = hw.get("ram") or {}
    total = ram.get("total_gb")
    if not total:
        return None
    mods = ram.get("modules") or []
    speed = next((m.get("configured_mts") or m.get("speed_mts") for m in mods
                  if m.get("configured_mts") or m.get("speed_mts")), None)
    size = round(total)
    name = f"{size} GB" + (f" {speed} MT/s" if speed else "") + (f" ({len(mods)} x {mods[0].get('size_gb'):g} GB)"
                                                                 if mods and mods[0].get("size_gb") else "")
    return {"part": "memory", "part_key": _key("memory", f"{size}gb", f"{speed}mts" if speed else None,
                                               f"{len(mods)}x" if mods else None),
            "part_name": name, "variant": None,
            "context": {"bios": (hw.get("device") or {}).get("bios"), "os": _os(hw)}}


def storage_part(hw: Mapping, drive: Optional[Mapping] = None) -> Optional[dict]:
    """The drive the disk test measured (saved with it), else the one holding the app's data."""
    drive = drive or next((d for d in hw.get("drives") or [] if d.get("holds_app_data")), None)
    if not drive or not drive.get("model"):
        return None
    return {"part": "storage", "part_key": _key("storage", drive.get("model"), f"{drive.get('size_gb')}gb"),
            "part_name": f"{drive['model']} {drive.get('size_gb')} GB" if drive.get("size_gb") else drive["model"],
            "variant": None, "context": {"firmware": drive.get("firmware"), "os": _os(hw)}}


def system_part(hw: Mapping) -> Optional[dict]:
    """The device tested itself (owner, 2026-10-24): a named device (a mini PC, a laptop, a ready-made desktop) by
    its maker and model; a desktop whose firmware names no model is a build, named by its graphics card, processor
    and the date it was first tested: "Build: RTX 4090 + Ryzen 9 9950X (2026-10-24)". A build with another card or
    processor is another build. A laptop or mini PC with no model name is this unit, until the owner names it."""
    d = hw.get("device") or {}
    if not d.get("maker") and not d.get("model") and not hw.get("cpu"):
        return None
    ctx = {"bios": d.get("bios"), "os": _os(hw)}
    if not d.get("placeholder"):
        return {"part": "system", "part_key": _key("system", d.get("maker"), d.get("model")),
                "part_name": " ".join(x for x in (d.get("maker"), d.get("model")) if x), "variant": None,
                "context": ctx}
    if d.get("kind") in (None, "desktop"):
        cpu = cpu_part(hw)
        gpu = pick_gpu(hw)[0]
        gname = gpu_part(hw, gpu) if gpu else None
        date = str(hw.get("captured_at") or "")[:10] or None
        parts = " + ".join(x["part_name"] for x in (gname, cpu) if x) or "unknown parts"
        return {"part": "system", "build": True,
                "part_key": _key("system", "build", hw.get("unit_id"), cpu and cpu["part_key"],
                                 gname and gname["part_key"]),
                "part_name": f"Build: {parts}" + (f" ({date})" if date else ""), "variant": None, "context": ctx}
    return {"part": "system", "part_key": _key("system", d.get("maker"), d.get("model"), hw.get("unit_id")),
            "part_name": f"{d.get('maker') or 'Unknown'} {d.get('kind') or 'PC'} (not named yet)", "variant": None,
            "context": ctx}


def ai_device(test_id: str, ai_run: Optional[Mapping], note: Optional[str]) -> dict:
    """Where an AI run ran, from what it recorded: MLPerf's device type (GPU / NPU / CPU), else the LM Studio
    runtime (a CUDA/Vulkan/ROCm/Metal engine is the GPU) and how much of the model was on it."""
    ai_run = ai_run or {}
    backend = str(ai_run.get("backend") or "")
    kind = str(ai_run.get("device_type") or "").strip().lower()
    if kind not in ("gpu", "npu", "cpu"):
        kind = "gpu" if GPU_BACKEND.search(backend) else ("cpu" if backend else None)
    if kind != "gpu":
        offload = "none" if kind else None
    elif ai_run.get("tool") == "MLPerf Client" or "GPU offload: max" in (note or "") or (
            test_id.startswith("llama_benchy_")):         # the fixed models load every layer on the card
        offload = "full"
    else:
        offload = "unknown"
    return {"device": kind, "offload": offload, "backend": backend or None}


def flags_of(environment: Optional[Mapping] = None, conditions: Optional[Mapping] = None,
             emulated: bool = False) -> List[str]:
    """Why a result is not compared with the others: it ran somewhere that changes scores."""
    env = environment or {}

    def on(name):
        f = env.get(name)
        return bool(f.get("value")) if isinstance(f, Mapping) else bool(f)
    flags = [w for w, name in (("WSL", "is_wsl"), ("virtual machine", "is_virtual_machine")) if on(name)]
    if emulated:
        flags.append("emulated")
    for item in (conditions or {}).get("items") or []:
        if item.get("name") == "Power" and "battery" in str(item.get("value", "")):
            flags.append("on battery")
    return flags


def attribute(test_id: str, group: str, hardware: Optional[Mapping], *, ai_run: Optional[Mapping] = None,
              note: Optional[str] = None, drive: Optional[Mapping] = None, flags: List[str] = (),
              is_test: bool = False) -> dict:
    hw = hardware or {}
    spec = TEST_PARTS.get(test_id, {})
    kind = spec["part"] if "part" in spec else GROUP_PARTS.get(group)
    out = {"test_id": test_id, "part": None, "part_key": None, "part_name": None, "variant": None, "linked": [],
           "context": {}, "library": True, "why_not": None, "ranked": True, "flags": list(flags), "guessed": []}
    if is_test or ("part" in spec and spec["part"] is None):
        return dict(out, library=False, ranked=False, why_not="the app's own check")
    stress = group == "stress"
    if stress:
        # owner, 2026-10-24: how the part (or the whole device) holds up under load: stability, power, temperature.
        # Filed with its part, shown on its page, never ranked with performance scores
        kind = STRESS_PARTS.get(test_id)
        if kind is None:
            return dict(out, library=False, ranked=False, why_not="a stress test of no known part")
        out["kind"] = "stress"
    if kind is None:
        return dict(out, library=False, ranked=False, why_not="not a test of one part")
    if group == "game" and not GAMES_READY:
        out.update(library=False, why_not="game results join the library once the Games section is ready")
    ai = None
    if kind == "ai":
        ai = ai_device(test_id, ai_run, note)
        out["ai"] = ai
        kind = {"gpu": "gpu", "cpu": "cpu", "npu": "cpu"}.get(ai["device"])
        if kind is None:
            return dict(out, library=False, ranked=False, why_not="where the AI model ran was not recorded")
        if ai["device"] == "npu":
            out["flags"].append("NPU (filed with the processor)")
        if ai["offload"] == "unknown":
            out["flags"].append("GPU offload not known")
        elif ai["offload"] not in ("full", "none"):
            out["flags"].append("mixed: part of the model on the CPU")

    def make(k: str) -> Optional[dict]:
        if k == "cpu":
            return cpu_part(hw)
        if k == "gpu":
            api = spec.get("api") or ("cuda" if ai and re.search(r"cuda", ai["backend"] or "", re.I) else None)
            gpu, guess = pick_gpu(hw, api)
            if guess:
                out["guessed"].append(guess)
            return gpu_part(hw, gpu) if gpu else None
        if k == "memory":
            return memory_part(hw)
        if k == "storage":
            return storage_part(hw, drive)
        if k == "system":
            return system_part(hw)
        return None
    main = make(kind) if kind else None
    if main is None:
        why = "the PC's parts were not read for this session" if not hw.get("cpu") else f"no {kind} found"
        return dict(out, library=False, ranked=False, why_not=out["why_not"] or why)
    out.update(main)
    linked = list(spec.get("linked") or (["cpu"] if group == "game" else []))
    if kind == "system":
        linked = linked or ["cpu", "gpu"]
    if stress and kind != "system":
        linked = ["system"]                     # the cooling of this device decides the temperatures
    out["linked"] = [p for p in (make(k) for k in linked) if p]
    if group == "web" or test_id in ("speedometer", "jetstream"):
        m = re.search(r"Browser:\s*([^;]+)", note or "")
        out["context"]["browser"] = m.group(1).strip() if m and "unknown" not in m.group(1) else None
        if not out["context"]["browser"]:
            out["flags"].append("browser not recorded")
    if ai:
        out["context"]["ai_runtime"] = ai["backend"]
    out["ranked"] = not out["flags"] and not stress
    return out
