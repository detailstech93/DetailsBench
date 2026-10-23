"""Read and change Shadow of the Tomb Raider graphics settings: DWORD values in the registry.

HKEY_CURRENT_USER\\Software\\Eidos Montreal\\Shadow of the Tomb Raider\\Graphics (read on this computer on
2026-10-23): the same shape as Rise of the Tomb Raider (app/games/rise_tomb_raider.py, whose functions this module
uses with its own settings). Seen then: FullscreenWidth/Height 1920x1080, Fullscreen 1, EnableDX12 1, RayTracing 0,
DLSS 3, XESS 0, CAS 0. Quality levels, ray tracing, DLSS and XeSS are numbers whose meaning the game does not
store, so only numbers this game has used before (learned) are offered or written, and the run dialog offers only
the resolution until each number is matched to the game's menu.
"""

from functools import partial
from typing import Dict, List, Tuple

from app.games import rise_tomb_raider as _rt

APP_ID = 750920
LAUNCH_ARGS: List[str] = []
REGISTRY_KEY = "Software\\Eidos Montreal\\Shadow of the Tomb Raider\\Graphics"

# key -> (kind, registry value names); kinds as in rise_tomb_raider
SETTINGS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "resolution": ("resolution", ("FullscreenWidth", "FullscreenHeight")),
    "fullscreen": ("bool", ("Fullscreen",)),
    "exclusive_fullscreen": ("bool", ("ExclusiveFullscreen",)),
    "vsync": ("learned", ("VSync",)),
    "graphics_api": ("api", ("EnableDX12",)),
    "ray_tracing": ("learned", ("RayTracing",)),
    "dlss": ("learned", ("DLSS",)),
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
