"""Whole-device power from a smart plug, read through Home Assistant (owner, 2026-10-27).

Any plug Home Assistant shows as a power sensor works, whatever its brand (Shelly, Tuya, TP-Link, Zigbee...).
The app only reads: GET /api/states with a long-lived access token the owner pastes in. Settings (settings.json,
on this PC only, never in the library): plug_url, plug_token, plug_entity. The token never goes into a message.
"""

import json
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional, Tuple

URL_KEY, TOKEN_KEY, ENTITY_KEY = "plug_url", "plug_token", "plug_entity"
TIMEOUT = 3
WATTS = {"W": 1.0, "kW": 1000.0}


def _get(url: str, token: str, path: str, opener: Callable = urllib.request.urlopen):
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("the address must start with http:// or https://")
    req = urllib.request.Request(url.rstrip("/") + path, headers={"Authorization": f"Bearer {token}"})
    with opener(req, timeout=TIMEOUT) as res:
        return json.loads(res.read().decode("utf-8"))


def _watts(state: dict) -> Optional[float]:
    factor = WATTS.get((state.get("attributes") or {}).get("unit_of_measurement"))
    try:
        return float(state.get("state")) * factor if factor else None
    except (TypeError, ValueError):
        return None                     # "unavailable", "unknown"


def power_sensors(url: str, token: str, opener: Callable = urllib.request.urlopen) -> List[Tuple[str, str, Optional[float]]]:
    """[(entity id, name, watts now)] of every sensor measured in W or kW, by name."""
    out = []
    for state in _get(url, token, "/api/states", opener):
        attrs = state.get("attributes") or {}
        if state.get("entity_id", "").startswith("sensor.") and attrs.get("unit_of_measurement") in WATTS:
            out.append((state["entity_id"], attrs.get("friendly_name") or state["entity_id"], _watts(state)))
    return sorted(out, key=lambda s: s[1].lower())


def settings(data_dir: Path) -> Tuple[str, str, str]:
    from app.paths import load_settings
    data = load_settings(data_dir)[0]
    return tuple(str(data.get(k) or "").strip() for k in (URL_KEY, TOKEN_KEY, ENTITY_KEY))


class PlugSource:
    name = "smart plug (Home Assistant)"

    def __init__(self, url: str, token: str, entity: str, opener: Callable = urllib.request.urlopen):
        self.url, self.token, self.entity, self.opener = url, token, entity, opener

    def _read(self) -> float:
        value = _watts(_get(self.url, self.token, f"/api/states/{self.entity}", self.opener))
        if value is None:
            raise RuntimeError(f"{self.entity} has no power value now")
        return value

    def available(self) -> Tuple[bool, str]:
        try:
            self._read()
        except Exception as e:
            return False, f"Home Assistant plug {self.entity} not read: {e}"
        return True, f"whole device power from {self.entity}"

    def read(self):
        return {"plug_power": (self._read(), "W")}
