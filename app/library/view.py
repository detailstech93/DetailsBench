"""What the library screen shows (phase 4), from the index's packages: the parts by kind, and each part's results
with their driver, accuracy and the PC they ran on. Pure, so the screen only lays it out."""

import re
from typing import Dict, List, Mapping, Optional

KINDS = (("system", "Devices"), ("cpu", "Processors"), ("gpu", "Graphics cards"), ("memory", "Memory"),
         ("storage", "Storage"))
CONTEXT_KEYS = (("driver", "Driver"), ("firmware", "Firmware"), ("browser", "Browser"), ("ai_runtime", "AI runtime"),
                ("bios", "BIOS"))


def part_name(pkgs: List[Mapping], names: Mapping[str, str]) -> str:
    """The owner's name, else the name of the part's first result (a build keeps the date it was first tested)."""
    key = pkgs[0]["attribution"]["part_key"]
    if names.get(key):
        return names[key]
    first = min(pkgs, key=lambda p: p.get("created_at") or "")
    return first["attribution"]["part_name"]


def parts(results: List[Mapping], names: Mapping[str, str]) -> Dict[str, List[dict]]:
    """{kind: [{key, name, results, deleted, unnamed}]} sorted by name; deleted results count apart."""
    by_key: Dict[str, List[Mapping]] = {}
    for pkg in results:
        by_key.setdefault(pkg["attribution"]["part_key"], []).append(pkg)
    out: Dict[str, List[dict]] = {k: [] for k, _ in KINDS}
    for key, pkgs in by_key.items():
        kind = pkgs[0]["attribution"]["part"]
        if kind not in out:
            continue
        out[kind].append({"key": key, "name": part_name(pkgs, names),
                          "results": sum(1 for p in pkgs if not p.get("deleted")),
                          "deleted": sum(1 for p in pkgs if p.get("deleted")),
                          "unnamed": "(not named yet)" in pkgs[0]["attribution"]["part_name"] and key not in names,
                          "kind": kind})
    for kind in out:
        out[kind].sort(key=lambda p: p["name"].lower())
    return out


def _version_key(text: Optional[str]):
    """'617.42' > '581.8': numbers compared as numbers, so the newest driver comes first."""
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", text or "")]


def _number(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:,.2f}".rstrip("0").rstrip(".")


def software(pkg: Mapping) -> str:
    ctx = pkg["attribution"].get("context") or {}
    text = "; ".join(f"{label} {ctx[k]}" for k, label in CONTEXT_KEYS if ctx.get(k))
    return text or ("driver unknown" if pkg.get("old_session") else "-")      # phase 6: an older session


def driver_label(pkg: Mapping) -> str:
    """The one version a part's results are told apart by: its driver, else firmware, BIOS or browser."""
    ctx = pkg["attribution"].get("context") or {}
    return next((f"{label} {ctx[k]}" for k, label in CONTEXT_KEYS if k != "ai_runtime" and ctx.get(k)),
                "driver unknown" if pkg.get("old_session") else "-")


def stress_text(pkg: Mapping) -> str:
    """Owner, 2026-10-24: under load, stable or not, how much it drew, how hot it got."""
    m = pkg["result"].get("metrics") or {}

    def mean(name):
        v = (m.get(name) or {}).get("mean")
        return v if isinstance(v, (int, float)) else None
    kind = pkg["attribution"]["part"]
    out = []
    for side, power, temp in (("cpu", "tel_cpu_package_power", "tel_cpu_temperature_hottest"),
                              ("gpu", "tel_gpu0_power", "tel_gpu0_temperature")):
        if kind not in (side, "system") or mean(f"{side}_stability") is None:
            continue
        bits = [f"{mean(f'{side}_stability'):.1f}% stable"]
        errors = mean(f"{side}_errors")
        if errors:
            bits.append(f"{errors:.0f} errors")
        if mean(f"{power}_avg") is not None:
            bits.append(f"{mean(f'{power}_avg'):.0f} W avg / {mean(f'{power}_max'):.0f} W max")
        if mean(f"{temp}_max") is not None:
            bits.append(f"{mean(f'{temp}_max'):.0f} C max")
        out.append(side.upper() + " " + ", ".join(bits))
    if mean("tel_plug_power_avg") is not None:
        out.append(f"whole device {mean('tel_plug_power_avg'):.0f} W avg / {mean('tel_plug_power_max'):.0f} W max")
    minutes = (pkg["test"].get("settings") or {}).get("stress_minutes")
    return (f"{minutes:g} min: " if minutes else "") + ("; ".join(out) or "no load numbers")


def _pc(device: Mapping) -> str:
    """'Dell Inc. XPS 15'; a maker that repeats as the model ('CWL CWL') once."""
    maker, model = device.get("maker"), device.get("model")
    return " ".join(x for x in (maker, model if model != maker else None) if x)


def _device_keys(results: List[Mapping]) -> Dict[str, str]:
    """unit id -> its device's part key, from its device results (packages before system_key was kept)."""
    out: Dict[str, str] = {}
    for p in sorted(results, key=lambda p: p.get("created_at") or ""):
        if p["attribution"]["part"] == "system":
            out[p["pc"]["unit_id"]] = p["attribution"]["part_key"]
    return out


def pc_name(pkg: Mapping, names: Mapping[str, str], devices: Mapping[str, str]) -> str:
    """The device a result ran on: the owner's name for it, else maker + model, else its unit id."""
    return (names.get(pkg["pc"].get("system_key") or devices.get(pkg["pc"]["unit_id"], ""))
            or _pc(pkg["pc"].get("device") or {}) or pkg["pc"]["unit_id"])


def rows(results: List[Mapping], part_key: str, names: Optional[Mapping[str, str]] = None) -> List[dict]:
    """One row per result of the part: newest driver first, then newest date. "Tested on" is the device's name
    in the library when the owner gave one."""
    names = names or {}
    devices = _device_keys(results)
    pkgs = [p for p in results if p["attribution"]["part_key"] == part_key]
    pkgs.sort(key=lambda p: (_version_key((p["attribution"].get("context") or {}).get("driver")),
                             p["session"].get("created_at") or p.get("created_at") or ""), reverse=True)
    pkgs.sort(key=lambda p: p["test"].get("name") or p["test"]["id"])
    out = []
    for p in pkgs:
        a, r = p["attribution"], p["result"]
        stress = a.get("kind") == "stress"
        flags = list(a.get("flags") or []) + (["noisy"] if r.get("noisy") else []) + list(a.get("guessed") or [])
        out.append({"package_id": p["package_id"], "test": p["test"].get("name") or p["test"]["id"],
                    "result": (r.get("text") or "-") if a.get("kind") == "info" else stress_text(p) if stress
                    else f"{_number(r.get('mean'))} {p['test'].get('unit') or ''}".strip(),
                    "accuracy": "" if stress else r.get("accuracy") or "", "software": software(p),
                    "variant": a.get("variant") or "",
                    "pc": pc_name(p, names, devices),
                    "date": (p["session"].get("created_at") or p.get("created_at") or "")[:16].replace("T", " "),
                    "notes": ", ".join(flags), "stress": stress,
                    "deleted": p.get("deleted")})
    return out


def by_test(rows_: List[dict]) -> List[tuple]:
    """[(test, [its rows, newest first])] by test name: a test run more than once is one line that opens
    (owner, 2026-10-24)."""
    groups: Dict[str, List[dict]] = {}
    for r in rows_:
        groups.setdefault(r["test"], []).append(r)
    return [(test, sorted(rs, key=lambda r: r["date"], reverse=True)) for test, rs in sorted(groups.items())]
