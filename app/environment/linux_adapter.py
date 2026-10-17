"""LinuxAdapter: environment detection on Linux (and inside WSL).

Only tested with mocked inputs on Windows, so every value is reported as
"unverified on real hardware". Inputs come from injectable readers so tests
feed fake files into the REAL parsing code.
"""

import os
from typing import Callable, Dict, List, Mapping, Optional

from app.environment.adapters import PlatformAdapter, Step, emulation_field
from app.environment.arch import normalize_arch
from app.environment.linux_parsers import display_server, is_wsl, parse_os_release
from app.environment.profile import ProfileField, unavailable, unverified

NOTE = " (unverified on real hardware)"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


class LinuxAdapter(PlatformAdapter):
    os_family = "linux"

    def __init__(self,
                 read_text: Callable[[str], Optional[str]] = _read_text,
                 env: Optional[Mapping[str, str]] = None,
                 machine: Optional[Callable[[], str]] = None,
                 euid: Optional[Callable[[], int]] = None):
        self.read_text = read_text
        self.env = os.environ if env is None else env
        self.machine = machine or (lambda: os.uname().machine)
        self.euid = euid or (lambda: os.geteuid())   # lazy: os.geteuid is POSIX-only

    def steps(self) -> List[Step]:
        return [
            ("os", self._os),
            ("architecture", self._architecture),
            ("elevation", self._elevation),
            ("hardware", self._hardware),
            ("graphics", self._graphics),
        ]

    def _os(self) -> Dict[str, ProfileField]:
        text = self.read_text("/etc/os-release") or self.read_text("/usr/lib/os-release")
        release = parse_os_release(text) if text else {}
        name = release.get("PRETTY_NAME") or release.get("NAME")
        wsl = is_wsl(self.read_text("/proc/version"), self.env)
        server = display_server(self.env)
        return {
            "os_name": unverified(name, "/etc/os-release" + NOTE) if name else unavailable("/etc/os-release"),
            "os_version": (unverified(release["VERSION_ID"], "/etc/os-release" + NOTE)
                           if release.get("VERSION_ID") else unavailable("/etc/os-release")),
            "os_build": unavailable("no build number on Linux"),
            "is_wsl": unverified(wsl, "WSL env vars / /proc/version" + NOTE),
            "is_virtual_machine": unavailable("no reliable non-invasive method"),
            "display_server": (unverified(server, "XDG_SESSION_TYPE / WAYLAND_DISPLAY / DISPLAY" + NOTE)
                               if server else unavailable("XDG_SESSION_TYPE")),
        }

    def _architecture(self) -> Dict[str, ProfileField]:
        native = normalize_arch(self.machine())
        source = "uname -m" + NOTE
        out = {
            # Python runs natively on Linux (no user-mode x64 emulation assumed)
            "process_arch": unverified(native, source) if native != "unknown" else unavailable(source),
            "native_arch": unverified(native, source) if native != "unknown" else unavailable(source),
        }
        field = emulation_field(native, source)
        if field.status != "unavailable":
            field = unverified(field.value, source)
        out["x64_emulated"] = field
        return out

    def _elevation(self) -> Dict[str, ProfileField]:
        return {"app_is_elevated": unverified(self.euid() == 0, "os.geteuid() == 0" + NOTE)}

    def _graphics(self) -> Dict[str, ProfileField]:
        return {
            "directx": unavailable("not available on Linux"),
            "vulkan": unavailable("not probed on Linux yet"),
            "opengl": unavailable("not probed on Linux yet"),
        }
