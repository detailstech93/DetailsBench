"""Sessions from before the parts record (phase 6, owner 2026-10-24).

Those sessions kept only the names of the CPU and graphics cards, the OS and the RAM size: no driver, no device, no
unit id. When the processor is this PC's and so is a graphics card (the built-in one may be listed or not), the
session ran here, so it is filed under this PC's parts; the driver it ran with is not known (owner: the laptop's
graphics card was always at the same power limit, so its variant stays). When they are not, it is filed under a build named by those parts. Each result says "driver unknown (older session)",
and the comparison takes it only where no newer result of the same test is there.
"""

import copy
import hashlib
from typing import Mapping, Optional

from app.library.attribution import clean_name

NOTE = "driver unknown (older session)"
VENDORS = (("nvidia", "10de"), ("geforce", "10de"), ("radeon", "1002"), ("amd", "1002"), ("intel", "8086"))


def _value(env: Mapping, name: str):
    item = env.get(name)
    return item.get("value") if isinstance(item, Mapping) else item


def _gpu_names(env: Mapping) -> list:
    names = _value(env, "gpu") or []
    return [n for n in ([names] if isinstance(names, str) else names) if n]


def hardware(manifest: Mapping, this_pc: Optional[Mapping] = None) -> Optional[dict]:
    """A parts record for an old session, or None when it did not keep even the processor's name."""
    env = (manifest or {}).get("environment") or {}
    cpu = _value(env, "cpu")
    if not cpu:
        return None
    gpus = _gpu_names(env)
    when = str((manifest.get("session") or {}).get("created_at") or manifest.get("created_at") or "")[:10] or None
    here = this_pc or {}
    here_gpus = {clean_name(g.get("name")) for g in here.get("gpus") or []}
    if (here.get("unit_id") and clean_name((here.get("cpu") or {}).get("name")) == clean_name(cpu)
            and (not gpus or {clean_name(g) for g in gpus} & here_gpus)):
        hw = copy.deepcopy(dict(here))
        for g in hw.get("gpus") or []:
            g["driver"] = None
            g.pop("driver_name", None)
        if hw.get("device"):
            hw["device"]["bios"] = None
    else:
        unit = "older-" + hashlib.sha256("|".join([cpu] + sorted(gpus)).encode()).hexdigest()[:12]
        hw = {"unit_id": unit, "captured_at": when,
              "device": {"maker": None, "model": None, "kind": None, "placeholder": True},
              "cpu": {"name": cpu},
              "gpus": [{"name": g, "vendor_id": next((v for word, v in VENDORS if word in g.lower()), None)}
                       for g in gpus],
              "ram": {"total_gb": _value(env, "ram_gb"), "modules": []}, "drives": []}
    os_name = _value(env, "os_name")
    hw["os"] = {"name": os_name, "build": _value(env, "os_build")} if os_name else hw.get("os")
    return hw


def mark(pkg: dict) -> dict:
    """The package says it comes from an older session; its hash is made again."""
    from app.library.package import content_hash
    pkg["old_session"] = True
    pkg["attribution"]["guessed"] = list(pkg["attribution"].get("guessed") or []) + [NOTE]
    pkg["hash"] = content_hash(pkg)
    return pkg
