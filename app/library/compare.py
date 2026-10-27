"""Compare parts of the results library (phase 5): two or more parts side by side, test by test.

For each part and test the result shown is the one on the newest driver the part has a result for, the newest date
on that driver (plan, "Compare rules"). Windows results line up with Windows ones and Linux with Linux; one part's
Windows results against its Linux ones is a comparison of its own (owner, 2026-10-24). What does not compare cleanly
is said, not hidden: another benchmark version or settings, one run only, a noisy result. Also one part across
its drivers (a drop after an update shows), every part ranked by one test, and filters by OS and kind of device. Pure, so
the screen and the HTML report show the same thing.
"""

import html
import re
from datetime import datetime
from typing import Dict, List, Mapping, Optional

from app.library import view

LOWER_IS_BETTER = {"s", "sec", "seconds", "ms", "min", "minutes"}       # time to finish: less is faster
SKIP_KINDS = {"info", "stress"}                                          # text and load checks: no score to line up
SET_ASIDE = ("WSL", "virtual machine", "emulated", "on battery")        # never in a ranking (owner, 2026-10-21)
# A stress test is compared by the power it drew (owner, 2026-10-27): its own score is a load check. No part is
# "best" there: less power can be thriftier or held back by a lower limit.
POWER = (("tel_cpu_package_power_avg", "CPU power, average"), ("tel_cpu_package_power_max", "CPU power, peak"),
         ("tel_gpu0_power_avg", "GPU power, average"), ("tel_gpu0_power_max", "GPU power, peak"),
         ("tel_plug_power_avg", "whole device power (plug), average"),
         ("tel_plug_power_max", "whole device power (plug), peak"))
TOTAL = "CPU + GPU power, average"      # peaks are not added: the CPU's and the GPU's need not come at one moment


def _metric(p: Mapping, name: str) -> Optional[float]:
    value = (((p["result"].get("metrics") or {}).get(name)) or {}).get("mean")
    return value if isinstance(value, (int, float)) else None


def power(results: List[Mapping]) -> List[Mapping]:
    """The results, each stress test followed by one result per power reading it has (in memory only)."""
    out = []
    for p in results:
        out.append(p)
        if p["attribution"].get("kind") != "stress" or p.get("deleted"):
            continue
        values = [(label, _metric(p, name)) for name, label in POWER]
        cpu, gpu = values[0][1], values[2][1]
        if cpu is not None and gpu is not None:
            values.append((TOTAL, cpu + gpu))
        for label, value in values:
            if value is None:
                continue
            q = dict(p, attribution=dict(p["attribution"], kind="power", test_id=f"{_test_key(p)[0]}:{label}"),
                     test=dict(p["test"], name=f"{_test_name(p)}: {label}", unit="W", no_best=True),
                     result=dict(p["result"], mean=value, noisy=False))
            out.append(q)
    return out


def lower_is_better(unit: Optional[str]) -> bool:
    return (unit or "").strip().lower() in LOWER_IS_BETTER


def os_family(pkg: Mapping) -> str:
    """'Windows', 'Linux', 'macOS' or '' (not recorded), from the result's context or its PC."""
    name = ((pkg["attribution"].get("context") or {}).get("os") or ((pkg["pc"].get("os") or {}).get("name")) or "")
    low = name.lower()
    if not low:
        return ""
    if "windows" in low:
        return "Windows"
    if "mac" in low or "darwin" in low:
        return "macOS"
    return "Linux"


def _test_key(pkg: Mapping) -> tuple:
    a = pkg["attribution"]
    return (a.get("test_id") or pkg["test"]["id"], os_family(pkg), pkg["test"].get("unit") or "")


def _test_name(pkg: Mapping) -> str:
    """The test's name without the ' - Linux' an imported result carries (the OS has its own column)."""
    return re.sub(r" - (Linux|Windows|macOS)$", "", pkg["test"].get("name") or pkg["test"]["id"])


def _pick(pkgs: List[Mapping]) -> Mapping:
    """Newest driver first, then the newest date on it."""
    return max(pkgs, key=lambda p: (view._version_key((p["attribution"].get("context") or {}).get("driver")),
                                    p["session"].get("created_at") or p.get("created_at") or ""))


def _scorable(p: Mapping) -> bool:
    return (not p.get("deleted") and p["attribution"].get("kind") not in SKIP_KINDS
            and isinstance(p["result"].get("mean"), (int, float)))


def _scores(results: List[Mapping], key: str) -> List[Mapping]:
    """The part's results that have a score to line up."""
    return [p for p in results if p["attribution"]["part_key"] == key and _scorable(p)]


def device_kind(pkg: Mapping) -> str:
    return (pkg["pc"].get("device") or {}).get("kind") or ""


def filtered(results: List[Mapping], os: str = "", kind: Optional[str] = None) -> List[Mapping]:
    """Only one OS family ('' = all) and one kind of device (None = all, '' = not recorded)."""
    return [p for p in results if (not os or os_family(p) == os) and (kind is None or device_kind(p) == kind)]


def _chosen(pkgs: List[Mapping], with_os: bool = True) -> Dict[tuple, Mapping]:
    by_test: Dict[tuple, List[Mapping]] = {}
    for p in pkgs:
        k = _test_key(p)
        by_test.setdefault(k if with_os else (k[0], "", k[2]), []).append(p)
    return {k: _pick(v) for k, v in by_test.items()}


def compare(results: List[Mapping], part_keys: List[str], names: Optional[Mapping[str, str]] = None) -> dict:
    """{"parts": [{key, name}], "rows": [...]}. Each row is one test on one OS: its result per part (None where the
    part has none), the % difference to the first part, the best part, and notes. Tests all parts have come first."""
    names = names or {}
    results = power(results)
    parts, chosen = [], {}
    for key in part_keys:
        mine = [p for p in results if p["attribution"]["part_key"] == key]
        if not mine:
            continue
        parts.append({"key": key, "name": view.part_name(mine, names)})
        chosen[key] = _chosen(_scores(results, key))
    return {"mode": "parts", "title": " vs ".join(p["name"] for p in parts), "parts": parts,
            "rows": _rows(parts, chosen)}


def os_compare(results: List[Mapping], part_key: str, names: Optional[Mapping[str, str]] = None) -> dict:
    """One part's Windows results against its Linux ones, test by test (owner, 2026-10-24)."""
    results = power(results)
    mine = [p for p in results if p["attribution"]["part_key"] == part_key]
    if not mine:
        return {"mode": "os", "parts": [], "rows": []}
    name = view.part_name(mine, names or {})
    parts, chosen = [], {}
    for family in ("Windows", "Linux"):
        key = f"{part_key}|{family}"
        parts.append({"key": key, "name": f"{name} ({family})"})
        chosen[key] = _chosen([p for p in _scores(results, part_key) if os_family(p) == family], with_os=False)
    rows = _rows(parts, chosen)
    for row in rows:
        row["os"] = "Windows vs Linux"
    return {"mode": "os", "title": f"{name}: Windows vs Linux", "parts": parts, "rows": rows}


def drivers_compare(results: List[Mapping], part_key: str, names: Optional[Mapping[str, str]] = None) -> dict:
    """One part on each driver (or firmware, browser) it was tested with, oldest first: the difference is against
    the oldest, so a drop after an update shows as a minus."""
    results = power(results)
    mine = [p for p in results if p["attribution"]["part_key"] == part_key]
    if not mine:
        return {"mode": "drivers", "title": "", "parts": [], "rows": []}
    name = view.part_name(mine, names or {})
    by_label: Dict[str, List[Mapping]] = {}
    for p in _scores(results, part_key):
        by_label.setdefault(view.driver_label(p), []).append(p)

    def order(label):
        known = label not in ("-", "driver unknown")
        return (known, view._version_key(label), min(q["session"].get("created_at") or q.get("created_at") or ""
                                                      for q in by_label[label]))
    labels = sorted(by_label, key=order)
    parts = [{"key": f"{part_key}|{label}", "name": label} for label in labels]
    chosen = {f"{part_key}|{label}": _chosen(by_label[label]) for label in labels}
    rows = _rows(parts, chosen)
    for row in rows:                                    # the column already says "driver unknown"
        row["notes"] = [n for n in row["notes"] if not n.startswith("driver unknown")]
    return {"mode": "drivers", "title": f"{name}: " + (" vs ".join(labels) or "no results"), "parts": parts,
            "rows": rows}


def tests(results: List[Mapping]) -> List[tuple]:
    """[(test key, 'name (OS)')] of every test with a score, for the ranking list."""
    out = {}
    for p in power(results):
        if _scorable(p):
            key = _test_key(p)
            out.setdefault(key, f"{_test_name(p)} ({key[1] or 'OS not recorded'})")
    return sorted(out.items(), key=lambda kv: kv[1].lower())


def ranking(results: List[Mapping], test_key: tuple, names: Optional[Mapping[str, str]] = None) -> dict:
    """Every part with a result of the test on that OS, best first (its newest driver). Results run in WSL, a VM,
    emulated or on battery are set aside, and counted."""
    names = names or {}
    results = power(results)
    by_part: Dict[str, List[Mapping]] = {}
    aside = 0
    for p in results:
        if not _scorable(p) or _test_key(p) != tuple(test_key):
            continue
        if any(f in (p["attribution"].get("flags") or []) for f in SET_ASIDE):
            aside += 1
            continue
        by_part.setdefault(p["attribution"]["part_key"], []).append(p)
    picks = {k: _pick(v) for k, v in by_part.items()}
    devices = view._device_keys(results)
    lower = lower_is_better(test_key[2])
    order = sorted(picks, key=lambda k: picks[k]["result"]["mean"], reverse=not lower)
    parts = [{"key": k, "name": view.part_name([q for q in results if q["attribution"]["part_key"] == k], names)}
             for k in order]
    rows = _rows(parts, {k: {tuple(test_key): picks[k]} for k in order}) if parts else []
    test = rows[0]["test"] if rows else str(test_key[0])
    return {"mode": "ranking", "title": f"Ranking: {test} ({test_key[1] or 'OS not recorded'})", "parts": parts,
            "rows": rows, "set_aside": aside,
            "pcs": [view.pc_name(picks[k], names, devices) for k in order]}


def _rows(parts: List[dict], chosen: Mapping[str, Mapping[tuple, Mapping]]) -> List[dict]:
    rows = []
    for test_key in {k for c in chosen.values() for k in c}:
        picked = [chosen[p["key"]].get(test_key) for p in parts]
        first = next(p for p in picked if p)
        lower = lower_is_better(test_key[2])
        means = [p["result"]["mean"] if p else None for p in picked]
        base = means[0]
        cells, notes, one_run, old = [], [], [], []
        for part, p, mean in zip(parts, picked, means):
            if p is None:
                cells.append(None)
                continue
            diff = (mean - base) / base * 100 if base and p is not picked[0] else None
            r = p["result"]
            cells.append({"mean": mean, "text": view._number(mean), "diff": diff, "runs": r.get("runs"),
                          "accuracy": r.get("accuracy") or "", "software": view.software(p),
                          "variant": p["attribution"].get("variant") or "",
                          "date": (p["session"].get("created_at") or p.get("created_at") or "")[:10],
                          "package_id": p["package_id"]})
            if r.get("runs") == 1 or "low confidence" in (r.get("accuracy") or ""):
                one_run.append(part["name"])
            if r.get("noisy"):
                notes.append(f"{part['name']}: noisy (spread over 3%)")
            if p.get("old_session"):
                old.append(part["name"])
        present = [p for p in picked if p]
        if one_run:
            notes.insert(0, "low confidence: all of them (one run, or imported)" if len(one_run) == len(present) > 1
                         else "low confidence (one run, or imported): " + ", ".join(one_run))
        if old:
            notes.append("driver unknown (older session): " + ", ".join(old))
        if len({p["test"].get("version") for p in present if p["test"].get("version")}) > 1:
            notes.append("another benchmark version: not the same test")
        if len({p["test"].get("fingerprint") for p in present if p["test"].get("fingerprint")}) > 1:
            notes.append("other settings: not the same test")
        valued = [m for m in means if m is not None]
        best = None
        if len(valued) > 1 and not first["test"].get("no_best"):
            target = min(valued) if lower else max(valued)
            best = means.index(target)
        rows.append({"test": _test_name(first), "os": test_key[1], "unit": test_key[2], "lower_is_better": lower,
                     "group": first["test"].get("group") or "", "cells": cells, "best": best,
                     "no_best": bool(first["test"].get("no_best")),
                     "all": len(valued) == len(parts), "notes": notes})
    rows.sort(key=lambda r: (not r["all"], r["test"].lower(), r["os"]))
    return rows


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def report_html(result: Mapping, now: Optional[str] = None) -> str:
    """One page that stands on its own: a table and a bar per part for each test, light or dark like the system."""
    parts = result["parts"]
    when = now or datetime.now().isoformat(timespec="minutes").replace("T", " ")
    title = result.get("title") or " vs ".join(p["name"] for p in parts)
    blocks = []
    for row in result["rows"]:
        values = [c["mean"] for c in row["cells"] if c]
        top = max(values) if values else 0
        bars = []
        for i, (part, cell) in enumerate(zip(parts, row["cells"])):
            if cell is None:
                bars.append(f'<div class="bar-row"><span class="who">{_e(part["name"])}</span>'
                            f'<span class="none">no result</span></div>')
                continue
            width = cell["mean"] / top * 100 if top else 0
            diff = "" if cell["diff"] is None else f' <span class="diff">{cell["diff"]:+.1f}%</span>'
            best = " best" if row["best"] == i else ""
            detail = ", ".join(x for x in (cell["accuracy"], cell["software"] if cell["software"] != "-" else "",
                                           cell["variant"], cell["date"]) if x)
            bars.append(f'<div class="bar-row"><span class="who">{_e(part["name"])}</span>'
                        f'<span class="track"><span class="bar{best}" style="width:{width:.1f}%"></span></span>'
                        f'<span class="val">{_e(cell["text"])} {_e(row["unit"])}{diff}</span>'
                        f'<span class="detail">{_e(detail)}</span></div>')
        better = ("power drawn: no best" if row.get("no_best") else
                  "lower is better" if row["lower_is_better"] else "higher is better")
        notes = "".join(f"<li>{_e(n)}</li>" for n in row["notes"])
        blocks.append(f'<section><h2>{_e(row["test"])}<span class="os">{_e(row["os"] or "OS not recorded")}'
                      f' &middot; {better}</span></h2>{"".join(bars)}'
                      + (f'<ul class="notes">{notes}</ul>' if notes else "") + "</section>")
    first = _e(parts[0]["name"]) if parts else ""
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_e(title)}</title><style>"
        ":root{--bg:#f6f7fb;--panel:#fff;--text:#172033;--muted:#5b6478;--line:#d8dde8;--bar:#9db3ea;"
        "--best:#2456d6;--warn:#9a6700}"
        "@media (prefers-color-scheme:dark){:root{--bg:#10141c;--panel:#171d29;--text:#eef2fb;--muted:#a7b0c5;"
        "--line:#2b3447;--bar:#3c5288;--best:#7aa2ff;--warn:#e3b341}}"
        "body{margin:0;padding:24px 16px;background:var(--bg);color:var(--text);"
        "font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;line-height:1.45}"
        "main{max-width:980px;margin:0 auto}h1{margin:0 0 4px;font-size:22px}"
        ".sub{color:var(--muted);margin:0 0 20px}"
        "section{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;"
        "margin:0 0 12px}h2{font-size:16px;margin:0 0 10px}.os{color:var(--muted);font-weight:400;"
        "font-size:13px;margin-left:10px}"
        ".bar-row{display:grid;grid-template-columns:minmax(120px,220px) 1fr auto;gap:4px 12px;"
        "align-items:center;margin:6px 0}"
        ".track{height:14px;background:var(--line);border-radius:7px;overflow:hidden}"
        ".bar{display:block;height:100%;background:var(--bar);border-radius:7px}.bar.best{background:var(--best)}"
        ".val{font-variant-numeric:tabular-nums;white-space:nowrap}.diff{color:var(--muted)}"
        ".detail{grid-column:2/4;color:var(--muted);font-size:12px}.none{color:var(--muted);grid-column:2/4}"
        ".notes{margin:8px 0 0;padding-left:18px;color:var(--warn);font-size:13px}"
        "@media (max-width:600px){.bar-row{grid-template-columns:1fr auto}.who{grid-column:1/3}"
        ".detail,.none{grid-column:1/3}}"
        "</style></head><body><main>"
        f"<h1>{_e(title)}</h1>"
        f'<p class="sub">DetailsBench results library, {_e(when)}. Each column shows its result on the newest '
        f"driver it was tested with. Differences are against {first}. "
        + {"os": "The same part on Windows and on Linux.",
           "drivers": "The same part on each driver it was tested with, oldest first.",
           "ranking": "Every part with a result of this test, best first. Results run in WSL, a virtual machine, "
                      "emulated or on battery are left out."}.get(result.get("mode"),
                                                                 "Windows results are compared with Windows, Linux "
                                                                 "with Linux.") + "</p>"
        + ("".join(blocks) or "<p>These parts have no results to compare.</p>")
        + "</main></body></html>")
