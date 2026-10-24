"""Readings over a test (temperature, power, clock, load), kept in the library next to the result (owner, 2026-10-25).

Each run writes telemetry.csv in its run folder (app/telemetry/sampler.py), on that PC only. Here the measured
runs of a test are put in one file beside the package, in a folder of their own, so a package is never changed
(its hash stays) and an app that does not know this folder never reads it:
  <library>/readings/<unit id>/s<session>/<test>.json      written once, like a package
It goes the same way as a package: this PC's outbox first, then the library.
The chart shows the worst run of the reading chosen: the highest peak (temperature, power, load, memory), or the
lowest average for a clock (owner, 2026-10-25).
"""

import csv
import html
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from app.library.package import safe_name

logger = logging.getLogger(__name__)

FORMAT = "detailsbench.library.readings"
FORMAT_VERSION = 1
FOLDER = "readings"
MATCH_HOURS = 12        # a run folder belongs to the session only when its time is this close to the session's


def relative_path(pkg: Mapping) -> Path:
    return (Path(FOLDER) / safe_name(pkg["pc"]["unit_id"]) / f"s{pkg['session']['id']}"
            / f"{safe_name(pkg['test']['id'])}.json")


def _when(text) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(text).replace(" ", "T")[:19])
    except ValueError:
        return None


def session_folder(runs_root: Optional[Path], session_id, created_at) -> Optional[Path]:
    """The session's run folder (session_<id>_<YYYYMMDD_HHMMSS>), only when its time matches the session's: after
    the database was made again, an id can be used twice, and readings must never go to another session."""
    if not runs_root or session_id is None or not Path(runs_root).is_dir():
        return None
    start = _when(created_at)
    best = None
    for path in Path(runs_root).glob(f"session_{session_id}_*"):
        try:
            stamp = datetime.strptime(path.name.split("_", 2)[2], "%Y%m%d_%H%M%S")
        except (IndexError, ValueError):
            continue
        gap = abs((stamp - start).total_seconds()) if start else None
        if gap is not None and gap <= MATCH_HOURS * 3600 and (best is None or gap < best[0]):
            best = (gap, path)
    return best[1] if best else None


def read_csv(path: Path) -> Optional[dict]:
    """{"seconds": [...], "series": {key: {"unit", "values"}}} from a telemetry.csv; a missing value is None."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
    except (OSError, csv.Error, UnicodeDecodeError):
        return None
    if len(rows) < 3 or not rows[0] or rows[0][0] != "seconds":
        return None
    heads = []
    for h in rows[0][1:]:
        m = re.fullmatch(r"(.+?) \[(.*)\]", h)
        heads.append((m.group(1), m.group(2)) if m else (h, ""))

    def num(text):
        try:
            return float(text)
        except ValueError:
            return None
    seconds, series = [], {key: {"unit": unit, "values": []} for key, unit in heads}
    for row in rows[1:]:
        t = num(row[0]) if row else None
        if t is None:
            continue
        seconds.append(t)
        for i, (key, _) in enumerate(heads, 1):
            series[key]["values"].append(num(row[i]) if i < len(row) and row[i] != "" else None)
    series = {k: v for k, v in series.items() if any(x is not None for x in v["values"])}
    return {"seconds": seconds, "series": series} if seconds and series else None


def from_runs(folder: Optional[Path], test_id: str) -> List[dict]:
    """The measured runs of the test (not warm-ups); a run tried again keeps its last try."""
    if folder is None:
        return []
    last: Dict[int, Path] = {}
    for path in sorted(Path(folder).glob(f"{test_id}_r*")):
        m = re.fullmatch(re.escape(test_id) + r"_r(\d+)(?:_retry(\d+))?", path.name)
        if m and (path / "telemetry.csv").is_file():
            n, tries = int(m.group(1)), int(m.group(2) or 0)
            if n not in last or tries >= _tries(last[n]):
                last[n] = path
    out = []
    for n in sorted(last):
        data = read_csv(last[n] / "telemetry.csv")
        if data:
            out.append({"run": n, **data})
    return out


def _tries(path: Path) -> int:
    m = re.search(r"_retry(\d+)$", path.name)
    return int(m.group(1)) if m else 0


def build(pkg: Mapping, runs: List[dict]) -> Optional[dict]:
    if not runs:
        return None
    return {"format": FORMAT, "format_version": FORMAT_VERSION, "package_id": pkg["package_id"],
            "interval_seconds": 1, "runs": runs}


def _write_once(path: Path, data: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.part")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def to_outbox(outbox: Path, packages: List[Mapping], runs_root: Optional[Path]) -> int:
    """The readings of each package (one session's) into this PC's outbox; never raises (a result never waits on
    its readings)."""
    n = 0
    try:
        if not packages:
            return 0
        session = packages[0]["session"]
        folder = session_folder(runs_root, session.get("id"), session.get("created_at"))
        for pkg in packages:
            path = Path(outbox) / relative_path(pkg)
            data = build(pkg, from_runs(folder, pkg["test"]["id"])) if folder and not path.exists() else None
            if data:
                _write_once(path, data)
                n += 1
    except Exception:
        logger.exception("Results library: readings not kept")
    return n


def send(outbox: Path, library: Path) -> int:
    """What waits in the outbox into the library (the library is open already); one there already is kept."""
    n = 0
    for path in sorted(Path(outbox).glob(f"{FOLDER}/*/*/*.json")):
        dest = Path(library) / path.relative_to(outbox)
        if not dest.exists():
            _write_once(dest, json.loads(path.read_text(encoding="utf-8")))
            n += 1
        path.unlink()
    return n


def load(places: List[Optional[Path]], pkg: Mapping) -> Optional[dict]:
    """The package's readings from the library or, not sent yet, this PC's outbox."""
    for place in places:
        if not place:
            continue
        try:
            data = json.loads((Path(place) / relative_path(pkg)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (isinstance(data, dict) and data.get("format") == FORMAT and data.get("package_id") == pkg["package_id"]
                and isinstance(data.get("format_version"), int) and data["format_version"] <= FORMAT_VERSION):
            return data
    return None


def choices(data: Mapping) -> List[Tuple[str, str, str]]:
    """[(key, label, unit)] of the readings the runs have, in the reports' order; totals are not a reading."""
    from app.report import GAME_READINGS
    have = {}
    for run in data.get("runs") or []:
        for key, s in run["series"].items():
            if not key.endswith("_total") and not key.endswith("_total_gib"):
                have.setdefault(key, "°C" if s.get("unit") == "C" else s.get("unit") or "")
    out = []
    for label, keys, _ in GAME_READINGS:
        for key in keys:
            if key in have:
                out.append((key, label, have.pop(key)))
                break
    return out + [(k, k.replace("_", " "), u) for k, u in sorted(have.items())]


def _values(run: Mapping, key: str) -> List[float]:
    return [v for v in (run["series"].get(key) or {}).get("values", []) if v is not None]


def worst(data: Mapping, key: str) -> Tuple[Optional[dict], str]:
    """(the run to show, why that one)."""
    runs = [r for r in data.get("runs") or [] if _values(r, key)]
    if not runs:
        return None, ""
    if len(runs) == 1:
        return runs[0], "the only run"
    if "clock" in key:
        run = min(runs, key=lambda r: sum(_values(r, key)) / len(_values(r, key)))
        return run, f"run {run['run']} of {len(runs)}: the lowest average"
    run = max(runs, key=lambda r: max(_values(r, key)))
    return run, f"run {run['run']} of {len(runs)}: the highest peak"


def summary(run: Mapping, key: str) -> Dict[str, float]:
    v = _values(run, key)
    return {"min": min(v), "avg": sum(v) / len(v), "max": max(v)} if v else {}


def svg(run: Mapping, key: str, label: str, unit: str) -> str:
    from app.charts import line_chart
    s = run["series"][key]["values"]
    return line_chart(label, run["seconds"], s, x_unit="s", y_unit=unit, width=760, height=300)


def report_html(title: str, detail: str, label: str, unit: str, run: Mapping, key: str, now: Optional[str] = None) -> str:
    """One page that stands on its own: the chart and its lowest, average and highest, light or dark."""
    e = html.escape
    when = now or datetime.now().isoformat(timespec="minutes").replace("T", " ")
    stats = summary(run, key)
    cells = "".join(f"<td>{name}</td><td><b>{stats[k]:,.1f} {e(unit)}</b></td>"
                    for k, name in (("min", "Lowest"), ("avg", "Average"), ("max", "Highest")) if k in stats)
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{e(title)}: {e(label)}</title><style>"
            ":root{--bg:#fff;--fg:#111827;--muted:#4b5563;--line:#d1d5db;--accent:#2563eb}"
            "@media (prefers-color-scheme:dark){:root{--bg:#111318;--fg:#e5e7eb;--muted:#9ca3af;--line:#374151;"
            "--accent:#60a5fa}}"
            "body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;"
            "max-width:860px;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}p{color:var(--muted);margin:0 0 16px}"
            ".chart{--chart-line:var(--accent);--chart-text:var(--muted);--chart-grid:var(--line)}"
            "table{border-collapse:collapse;margin-top:12px}td{padding:4px 12px 4px 0}"
            "</style></head><body>"
            f"<h1>{e(title)}</h1><p>{e(label)} over the test; {e(detail)}. Made by DetailsBench {e(when)}.</p>"
            f"<div class=\"chart\">{svg(run, key, label, unit)}</div><table><tr>{cells}</tr></table>"
            "</body></html>")
