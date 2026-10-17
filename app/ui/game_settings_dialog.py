"""Pick the graphics settings a game benchmark forces (only games with an adapter in app/games).

Each setting offers "keep the game's value" (not forced) or one of the values the game's own settings
file allows. List settings whose positions the game builds at runtime (resolution, DLSS mode, ...)
offer only the values seen so far: pick another one once in the game's menu and it appears here.
"""

from typing import Dict, Optional

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

KEEP = "(keep the game's value)"
LABELS = {
    "resolution": "Resolution", "window_mode": "Window mode", "vsync": "VSync", "fps_limit": "FPS limit",
    "preset": "Quality preset", "upscaler": "Upscaler", "dlss_mode": "DLSS mode", "texture_quality": "Texture quality",
    "resolution_scale": "Resolution scale", "anti_aliasing": "Anti-aliasing", "graphics_api": "Graphics API",
    "fullscreen": "Fullscreen", "exclusive_fullscreen": "Exclusive fullscreen", "dlss": "DLSS",
    "shadow_quality": "Shadow quality", "ambient_occlusion": "Ambient occlusion", "level_of_detail": "Level of detail",
    "ssaa": "SSAA", "fxaa": "FXAA", "smaa": "SMAA", "motion_blur": "Motion blur",
    "dynamic_resolution": "Dynamic resolution", "frame_gen": "Frame generation",
    "dlss_frame_gen": "DLSS Frame Generation", "multi_frame_gen": "Multi Frame Generation",
    "ray_tracing": "Ray tracing", "rt_lighting": "RT lighting", "path_tracing": "Path tracing",
    "lighting_quality": "Lighting quality", "volumetrics_quality": "Volumetrics quality",
    "reflection_quality": "Reflection quality", "msaa": "MSAA", "fsr": "FSR", "hairworks": "HairWorks",
    "resolution_mode": "Resolution (r_mode position)",
}


def value_text(value) -> str:
    if value is True:
        return "On"
    if value is False:
        return "Off"
    return str(value)


class GameSettingsDialog(QDialog):
    def __init__(self, game_name: str, choices: Dict[str, list], current: Dict[str, object],
                 chosen: Dict[str, object], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Forced settings: {game_name}")
        layout = QVBoxLayout(self)
        note = QLabel("Before each benchmark the app writes these values into the game's settings file "
                      "(a copy is kept), checks them before and after the run, and puts your own file back "
                      "afterwards.\n\nSome settings (for example resolution, quality preset, upscaler) list only "
                      "the values this game has used so far, because its file does not hold the full list. To "
                      "force another one (the Low preset, the upscaler Off, ...), choose it once in the game's own "
                      "menu, close the game, and press Benchmark again.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.combos: Dict[str, QComboBox] = {}
        for key, values in choices.items():
            combo = QComboBox()
            combo.addItem(KEEP, None)
            for v in values:
                mark = "  (now)" if key in current and current[key] == v and type(current[key]) is type(v) else ""
                combo.addItem(value_text(v) + mark, v)
            want = chosen.get(key)
            if key in chosen:
                for i in range(1, combo.count()):
                    d = combo.itemData(i)
                    if d == want and type(d) is type(want):
                        combo.setCurrentIndex(i)
            self.combos[key] = combo
            form.addRow(LABELS.get(key, key) + ":", combo)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selection(self) -> Dict[str, object]:
        """Only the forced settings (keys left on KEEP are absent)."""
        out = {}
        for key, combo in self.combos.items():
            if combo.currentIndex() > 0:
                out[key] = combo.currentData()
        return out


def describe(selection: Dict[str, object]) -> Optional[str]:
    if not selection:
        return None
    return ", ".join(f"{LABELS.get(k, k)} {value_text(v)}" for k, v in selection.items())
