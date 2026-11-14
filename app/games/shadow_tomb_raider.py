"""Read and change Shadow of the Tomb Raider graphics settings: DWORD values in the registry.

HKEY_CURRENT_USER\\Software\\Eidos Montreal\\Shadow of the Tomb Raider\\Graphics (read on this computer on
2026-10-23): the same shape as Rise of the Tomb Raider (app/games/rise_tomb_raider.py, whose functions this module
uses with its own settings). Seen then: FullscreenWidth/Height 1920x1080, Fullscreen 1, EnableDX12 1, RayTracing 0,
DLSS 3, XESS 0, CAS 0. The game stores numbers, not names: DLSS, ray traced shadows and the five graphics presets
were matched to its Setup window with the owner on 2026-10-29 (each set in Setup, Ok, then read here). The game
keeps no preset name, so a preset is the numbers of all its settings. XeSS and the single quality settings stay
learned numbers.
"""

from functools import partial
from typing import Dict, List, Tuple

from app.games import rise_tomb_raider as _rt

APP_ID = 750920
LAUNCH_ARGS: List[str] = []
REGISTRY_KEY = "Software\\Eidos Montreal\\Shadow of the Tomb Raider\\Graphics"

DLSS = {"Off": 0, "Performance": 1, "Balanced": 2, "Quality": 3, "Ultra Performance": 4}
RAY_TRACING = {"Off": 0, "Medium": 1, "High": 2, "Ultra": 3}       # Ray Traced Shadow Quality; High by its place
PRESET_VALUES = ("TextureQuality", "TextureFiltering", "ShadowQuality", "DOFQuality", "LevelOfDetail",
                 "AmbientOcclusionQuality", "TressFX", "ScreenSpaceContactShadows", "MotionBlur", "Bloom",
                 "ScreenSpaceReflections", "LensFlares", "ScreenEffects", "VolumetricLighting", "Tessellation")
PRESETS = {     # in PRESET_VALUES order, read after choosing each preset in Setup (ray tracing off)
    "Lowest": (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0),
    "Low": (0, 0, 1, 0, 1, 0, 0, 0, 0, 1, 0, 1, 1, 1, 0),
    "Medium": (1, 1, 1, 1, 2, 1, 1, 0, 1, 1, 1, 1, 1, 1, 0),
    "High": (2, 2, 2, 1, 2, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1),
    "Highest": (3, 3, 3, 2, 3, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1),
}

# key -> (kind, registry value names[, names]); kinds as in rise_tomb_raider
SETTINGS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "resolution": ("resolution", ("FullscreenWidth", "FullscreenHeight")),
    "fullscreen": ("bool", ("Fullscreen",)),
    "exclusive_fullscreen": ("bool", ("ExclusiveFullscreen",)),
    "vsync": ("learned", ("VSync",)),
    "graphics_api": ("api", ("EnableDX12",)),
    "ray_tracing": ("named", ("RayTracing",), RAY_TRACING),
    "dlss": ("named", ("DLSS",), DLSS),
    "preset": ("preset", PRESET_VALUES, PRESETS),
    "xess": ("learned", ("XESS",)),
    "cas": ("learned", ("CAS",)),
    "texture_quality": ("learned", ("TextureQuality",)),
    "shadow_quality": ("learned", ("ShadowQuality",)),
    "ambient_occlusion": ("learned", ("AmbientOcclusionQuality",)),
    "level_of_detail": ("learned", ("LevelOfDetail",)),
    "anti_aliasing": ("learned", ("AA",)),
    "motion_blur": ("bool", ("MotionBlur",)),
}

Change = _rt.Change
read_settings = partial(_rt.read_settings, spec=SETTINGS)
observed_indices = partial(_rt.observed_indices, spec=SETTINGS)
merge_indices = _rt.merge_indices
choices = partial(_rt.choices, spec=SETTINGS)
plan = partial(_rt.plan, spec=SETTINGS)
apply = partial(_rt.apply, spec=SETTINGS)
verify = partial(_rt.verify, spec=SETTINGS)


# The game's own result: Documents\Shadow of the Tomb Raider\SOTTR_<PC>_<date>.txt (NucBox K10, 2026-11-13: "Average
# Benchmark Statistics ... Average FPS: 32.7"). Its Min FPS reads 0.0 (the load), so only the average is taken.
REPORT_DIR = "Shadow of the Tomb Raider"
REPORT_KEYS: Dict[str, str] = {}


def game_report(settings_path, since=None, env=None):
    """{"avg": ..., "frames": ...} from the newest result file written at or after since; None when there is none."""
    import os
    from app.games.settings_file import documents_dir
    docs = documents_dir(env if env is not None else os.environ)
    if docs is None:
        return None
    try:
        found = [f for f in (docs / REPORT_DIR).glob("SOTTR_*.txt") if "frametimes" not in f.name.lower()
                 and (since is None or os.path.getmtime(f) >= since - 1)]
        if not found:
            return None
        text = max(found, key=os.path.getmtime).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    out, frames, in_average = {}, 0, False
    for line in text.splitlines():
        line = line.strip()
        try:
            if line.startswith("Average Benchmark Statistics"):
                in_average = True
            elif line.startswith("Num Frames:"):
                frames += int(float(line.split(":", 1)[1]))
            elif in_average and line.startswith("Average FPS:"):
                out["avg"] = float(line.split(":", 1)[1])
        except ValueError:
            return None
    if frames:
        out["frames"] = frames
    return out if "avg" in out else None


def own_numbers(report):
    return dict(report)


def report_conditions(report) -> List[str]:
    return []
