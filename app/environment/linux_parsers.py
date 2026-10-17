"""Pure parsers for Linux environment data (drafted by the local agent, reviewed).

No file, process or environment access here: callers pass the inputs.
"""

import re
from typing import Mapping, Optional


def parse_os_release(text: str) -> dict:
    r"""Parse /etc/os-release style text into a dictionary.

    Blank lines and comment lines are ignored. Lines must contain a valid
    KEY=VALUE pair. Values may be unquoted, single quoted, or double quoted.
    Double-quoted values support backslash escapes for \", \\, \$ and \`.
    Invalid lines are skipped and this function never raises on bad input.
    """
    if not isinstance(text, str):
        return {}

    result = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()

        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue

        value = value.strip()

        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            inner = value[1:-1]
            processed = []
            index = 0

            while index < len(inner):
                char = inner[index]

                if (
                    char == "\\"
                    and index + 1 < len(inner)
                    and inner[index + 1] in {'"', "\\", "$", "`"}
                ):
                    processed.append(inner[index + 1])
                    index += 2
                else:
                    processed.append(char)
                    index += 1

            value = "".join(processed)
        elif len(value) >= 2 and value[0] == "'" and value[-1] == "'":
            value = value[1:-1]

        result[key] = value

    return result


def is_wsl(proc_version: Optional[str], env: Mapping[str, str]) -> bool:
    """Return True when the environment or proc version indicates WSL.

    WSL is detected when WSL_DISTRO_NAME or WSL_INTEROP is a non-empty
    environment variable, or when proc_version contains "microsoft" or
    "wsl" case-insensitively. A None proc_version is treated as an empty
    string.
    """
    # env is any Mapping (os.environ is not a dict)
    for key in ("WSL_DISTRO_NAME", "WSL_INTEROP"):
        value = env.get(key)
        if isinstance(value, str) and value.strip():
            return True

    if proc_version is None:
        return False

    if not isinstance(proc_version, str):
        proc_version = str(proc_version)

    lowered = proc_version.lower()
    return "microsoft" in lowered or "wsl" in lowered


def display_server(env: Mapping[str, str]) -> Optional[str]:
    """Detect the display server from environment variables.

    XDG_SESSION_TYPE is checked first, case-insensitively and after
    stripping whitespace. If it is not "x11" or "wayland", WAYLAND_DISPLAY
    and DISPLAY are used as fallbacks.
    """
    session_type = env.get("XDG_SESSION_TYPE")
    if isinstance(session_type, str):
        normalized = session_type.strip().lower()
        if normalized == "x11":
            return "x11"
        if normalized == "wayland":
            return "wayland"

    wayland_display = env.get("WAYLAND_DISPLAY")
    if isinstance(wayland_display, str) and wayland_display.strip():
        return "wayland"

    display = env.get("DISPLAY")
    if isinstance(display, str) and display.strip():
        return "x11"

    return None
