"""Read and change The Witcher 3 (next-gen, DirectX 12) graphics settings in dx12user.settings.

Documents/The Witcher 3/dx12user.settings, INI text read on this computer on 2026-10-18 (the DirectX 11
mode uses user.settings, not supported here). A ref "Section:Key" is the first Key= line in [Section];
the line reader is Borderlands 3's (byte-exact). Quotes around a value (Resolution="2560x1440") are
hidden when read and kept when written. On/off values offer both; the numbers (window mode, AA, DLSS,
frame generation, FPS limit) carry no names, so only values this game has written before are offered
or written (app/games/text_settings.py).
"""

from typing import List, Optional

from app.games import borderlands3 as ini
from app.games import text_settings as ts
from app.games.text_settings import Change, Setting  # noqa: F401  (Change is used by callers)

APP_ID = 292030
LAUNCH_ARGS: List[str] = []
ON_OFF = ((True, "true"), (False, "false"))

SETTINGS = {
    "resolution": Setting("learned", ("Viewport:Resolution",)),
    "window_mode": Setting("learned", ("Viewport:FullScreenMode",)),
    "vsync": Setting("map", ("Viewport:VSync",), ON_OFF),
    "fps_limit": Setting("learned", ("Engine:LimitFPS",)),
    "anti_aliasing": Setting("learned", ("PostProcess:AAMode",)),
    "dlss_mode": Setting("learned", ("PostProcess:DLSSQuality",)),
    "dlss_frame_gen": Setting("learned", ("PostProcess:DLSSGMode",)),
    "ray_tracing": Setting("map", ("Rendering/RT:EnableRT",), ON_OFF),
    "path_tracing": Setting("map", ("Rendering/RT/PathTracer:PTEnable",), ON_OFF),
    "hairworks": Setting("learned", ("Rendering:HairWorksLevel",)),
}

parse = ini.parse
dump = ini.dump


def _raw(data, ref: str) -> Optional[str]:
    section, key = ref.rsplit(":", 1)
    return ini.get_value(data, section, key)


def get(data, ref: str) -> Optional[str]:
    raw = _raw(data, ref)
    if raw is not None and len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1]
    return raw


def put(data, ref: str, value: str):
    section, key = ref.rsplit(":", 1)
    raw = _raw(data, ref) or ""
    quoted = len(raw) >= 2 and raw[0] == raw[-1] == '"'
    return ini.set_value(data, section, key, '"%s"' % value if quoted else value)


def read_settings(data):
    return ts.read_settings(SETTINGS, get, data)


def observed_indices(data):
    return ts.observed_indices(SETTINGS, get, data)


merge_indices = ts.merge_indices


def choices(data, known):
    return ts.choices(SETTINGS, get, data, known)


def plan(data, desired, known):
    return ts.plan(SETTINGS, get, data, desired, known)


def apply(data, changes):
    return ts.apply(SETTINGS, put, data, changes)


def verify(data, desired):
    return ts.verify(SETTINGS, get, data, desired)
