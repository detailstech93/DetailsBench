"""Render a standalone HTML benchmark session report.

One file: inline CSS, no scripts, no external resources; every input value is
escaped by esc(). Drafted by the local agent, reviewed.
"""

import html
import math
import re
from collections.abc import Mapping
from typing import Any, Optional

ENVIRONMENT_FIELDS = (
    ("os_name", "Operating system"),
    ("os_version", "OS version"),
    ("os_build", "OS build"),
    ("native_arch", "Architecture"),
    ("cpu", "CPU"),
    ("ram_gb", "RAM (GB)"),
    ("gpu", "GPU"),
    ("is_virtual_machine", "Virtual machine"),
)


def esc(value: Any) -> str:
    """Escape input text and neutralize colon-based forbidden substrings."""
    return html.escape(str(value), quote=True).replace(":", "&#58;")


def _mapping(value: Any) -> Optional[Mapping]:
    if isinstance(value, Mapping):
        return value
    return None


def _truthy(value: Any) -> bool:
    try:
        return bool(value)
    except Exception:
        return False


def _text(value: Any, default: str = "-") -> str:
    if value is None:
        return default
    if isinstance(value, str) and value == "":
        return default
    return esc(value)


def _format_number(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return "-"
    if isinstance(value, (int, float)):
        try:
            if not math.isfinite(value):
                return "-"
            return f"{value:,.2f}"
        except Exception:
            return "-"
    return "-"


def _format_percent(value: Any) -> str:
    formatted = _format_number(value)
    if formatted == "-":
        return "-"
    return formatted + " %"


def _format_excluded(value: Any) -> str:
    excluded = _mapping(value)
    if excluded is None:
        return "-"

    parts = []
    for key in ("test", "warmup", "not_completed", "missing_score"):
        count = excluded.get(key)
        if isinstance(count, bool):
            continue
        if isinstance(count, int) and count > 0:
            parts.append(f"{key} {count}")

    if not parts:
        return "-"
    return ", ".join(parts)


def _format_environment_value(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        if not value:
            return "unavailable"
        return ", ".join(esc(item) for item in value)
    return esc(value)


def _whole(value: Any) -> Optional[int]:
    """76.43 -> 76: game numbers are shown without decimals."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return int(round(value))


# (label, telemetry key, unit shown): what the game section reports about the PC during the benchmark
GAME_READINGS = (
    ("CPU load", ("cpu_load",), "%"),
    ("CPU temperature", ("cpu_temperature_hottest",), "\u00b0C"),
    ("CPU clock", ("cpu_core_clock_avg", "cpu_clock_avg"), "MHz"),
    ("CPU power", ("cpu_package_power",), "W"),
    ("GPU load", ("gpu0_load",), "%"),
    ("GPU temperature", ("gpu0_temperature",), "\u00b0C"),
    ("GPU power", ("gpu0_power",), "W"),
    ("GPU clock", ("gpu0_sm_clock",), "MHz"),
    ("GPU memory clock", ("gpu0_mem_clock",), "MHz"),
    ("RAM used", ("ram_used",), "%"),
)


def _note_parts(note: Any) -> dict:
    """"Settings used: a, b; Forced: c; Readings: ..." -> {"Settings used": "a, b", ...}."""
    out = {}
    for part in str(note or "").split("; "):
        key, sep, value = part.partition(": ")
        if sep and key in ("Settings used", "Forced", "Readings", "Proof screenshot"):
            out[key] = value
    return out


PROOF_MAX_BYTES = 6 * 1024 ** 2
PROOF_ATTR = re.compile(r'(<img class="proof" alt="[^"<>]*" )data-proof="([A-Za-z0-9+/=]+)"')


def _proof_html(path: Any) -> str:
    """The screen the app saw when the built-in benchmark started (menu steps), put into the report itself so it
    still shows when the report is sent alone. Only a PNG the app wrote, and not a huge one."""
    if not path:
        return ""
    import base64
    from pathlib import Path
    p = Path(str(path))
    try:
        data = p.read_bytes() if p.name == "benchmark_started.png" and p.stat().st_size <= PROOF_MAX_BYTES else b""
    except OSError:
        data = b""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ""
    return ("<h4>When the benchmark started</h4>"
            f'<img class="proof" alt="The game\'s screen when its benchmark started" '
            f'data-proof="{base64.b64encode(data).decode("ascii")}">'
            '<p class="muted">The screen the app read when the game\'s own benchmark began; the measured windows '
            "count from this moment.</p>")


def _game_runs(export: Mapping) -> dict:
    """{benchmark: [measured runs]} for game benchmarks (completed, not warm-up; test runs only in a test session)."""
    session_is_test = _truthy((_mapping(export.get("session")) or {}).get("is_test"))
    out: dict = {}
    for run in export.get("runs") or []:
        m = _mapping(run) or {}
        if m.get("benchmark_type") != "game":
            continue
        out.setdefault(str(m.get("benchmark_name") or "-"), [])
        if (m.get("status") == "completed" and not _truthy(m.get("is_warmup"))
                and (session_is_test or not _truthy(m.get("is_test")))):
            out[str(m.get("benchmark_name") or "-")].append(m)
    return out


def _readings_rows(extra: Mapping) -> list:
    """Table rows (reading, average, highest) in whole numbers from a benchmark's metric summaries."""
    rows = []
    for label, keys, unit in GAME_READINGS:
        key = next((k for k in keys if f"tel_{k}_avg" in extra), None)
        avg = _whole((_mapping(extra.get(f"tel_{key}_avg")) or {}).get("mean")) if key else None
        top = _whole((_mapping(extra.get(f"tel_{key}_max")) or {}).get("max")) if key else None
        if avg is None and top is None:
            rows.append(f'<tr><td>{label}</td><td colspan="2" class="muted">not measured (no reading '
                        "in this run)</td></tr>")
            continue
        rows.append(f"<tr><td>{label}</td>"
                    f"<td>{'-' if avg is None else f'{avg:,} {unit}'}</td>"
                    f"<td>{'-' if top is None else f'{top:,} {unit}'}</td></tr>")
    return rows


# (chart title, [series names], unit shown): the charts of a stress test, from its stored series
STRESS_CHARTS = (
    ("Work done each second (stability)", ("cpu_work", "gpu_work"), "%"),
    ("Power", ("tel_cpu_package_power", "tel_gpu0_power"), "W"),
    ("Temperature", ("tel_cpu_temperature_hottest", "tel_gpu0_temperature"), "\u00b0C"),
    ("Clock", ("tel_cpu_core_clock_avg", "tel_cpu_clock_avg", "tel_gpu0_sm_clock"), "MHz"),
    ("Load", ("tel_cpu_load", "tel_gpu0_load"), "%"),
)
SERIES_NAMES = {"cpu_work": "CPU", "gpu_work": "GPU", "tel_cpu_package_power": "CPU", "tel_gpu0_power": "GPU",
                "tel_cpu_temperature_hottest": "CPU (hottest core)", "tel_gpu0_temperature": "GPU",
                "tel_cpu_core_clock_avg": "CPU", "tel_cpu_clock_avg": "CPU", "tel_gpu0_sm_clock": "GPU",
                "tel_cpu_load": "CPU", "tel_gpu0_load": "GPU"}


def _safe_svg(svg: str) -> str:
    # inline SVG needs no xmlns in HTML; no ":" may reach the page (see esc)
    return svg.replace(' xmlns="http://www.w3.org/2000/svg"', "").replace(":", "&#58;")


def _stress_section(export: Mapping) -> str:
    """Per stress test: stable or not (big), stability and wrong answers per part, power / temperature / clock
    numbers, and a chart over time for each reading of the last measured run."""
    from app.charts import line_chart
    session_is_test = _truthy((_mapping(export.get("session")) or {}).get("is_test"))
    by_test: dict = {}
    for run in export.get("runs") or []:
        m = _mapping(run) or {}
        if m.get("benchmark_type") != "stress":
            continue
        by_test.setdefault(str(m.get("benchmark_name") or "-"), [])
        if (m.get("status") == "completed" and not _truthy(m.get("is_warmup"))
                and (session_is_test or not _truthy(m.get("is_test")))):
            by_test[str(m.get("benchmark_name") or "-")].append(m)
        elif m.get("status") not in ("completed",) and not _truthy(m.get("is_warmup")):
            by_test[str(m.get("benchmark_name") or "-")].append(m)
    if not by_test:
        return ""
    metrics = _mapping(export.get("metric_summaries")) or {}
    blocks = []
    for name, runs in sorted(by_test.items()):
        title = esc({"stress_cpu": "CPU stress test", "stress_gpu": "GPU stress test",
                     "stress_cpu_gpu": "CPU + GPU stress test"}.get(name, name))
        if not runs:
            blocks.append(f'<div class="game"><h3>{title}</h3><p>No run.</p></div>')
            continue
        last = runs[-1]
        if last.get("status") != "completed":
            blocks.append(f'<div class="game"><h3>{title}</h3><div class="tiles"><div class="tile bad">'
                          '<div class="label">Result</div><div class="value">NOT stable</div></div></div>'
                          f'<p>The test did not finish: {_text(last.get("note"))}.</p></div>')
            continue
        m = _mapping(last.get("metrics")) or {}

        def value(key):
            return (_mapping(m.get(key)) or {}).get("value")

        tiles, unstable = [], False
        minutes = value("stress_minutes")
        judged = minutes is None or minutes >= 3      # a shorter run is mostly the warm-up
        for part in ("cpu", "gpu"):
            score, errors = value(f"{part}_stability"), value(f"{part}_errors")
            if score is None and errors is None:
                continue
            bad = bool(errors) or (judged and score is not None and round(score) < 97)
            unstable = unstable or bad
            tiles.append((f"{part.upper()} stability", f"{_whole(score)} <span>%</span>" if _whole(score) is not None
                          else "<span>not measured</span>", bad))
            tiles.append((f"{part.upper()} wrong answers", f"{_whole(errors) or 0}", bool(errors)))
        verdict = ('<div class="tile {}"><div class="label">Result</div><div class="value">{}</div></div>'
                   .format("bad" if unstable else "good" if judged else "",
                           "NOT stable" if unstable else "Stable" if judged else "Too short to judge"))
        tile_html = verdict + "".join(
            f'<div class="tile{" bad" if bad else ""}"><div class="label">{label}</div>'
            f'<div class="value">{text}</div></div>' for label, text, bad in tiles)
        series = _mapping(last.get("series")) or {}
        charts = []
        for chart_title, names, unit in STRESS_CHARTS:
            for s_name in names:
                s = _mapping(series.get(s_name))
                points = (s or {}).get("points") or []
                if len(points) < 2:
                    continue
                if s_name == "tel_cpu_clock_avg" and "tel_cpu_core_clock_avg" in series:
                    continue
                svg = line_chart(f"{chart_title}: {SERIES_NAMES.get(s_name, s_name)}", [p[0] for p in points],
                                 [p[1] for p in points], x_unit="s", y_unit=unit)
                if svg:
                    charts.append(f'<div class="chart">{_safe_svg(svg)}</div>')
        rows = _readings_rows(_mapping(metrics.get(name)) or {})
        blocks.append(
            f'<div class="game"><h3>{title}</h3><div class="tiles">{tile_html}</div>'
            f'<p class="muted">{_text(last.get("note"))}. Stable means: no wrong answer and the slowest 30 seconds '
            "did at least 97 % of the work of the fastest 30 seconds (no throttling); the warm-up at the start "
            "(the first minute of a run of 3 minutes or more) is left out."
            + (" With both together a laptop shares one power limit between them: when the GPU takes more power "
               "the CPU slows down, and that shows here as lower CPU stability." if name == "stress_cpu_gpu" else "")
            + "</p>"
            "<h4>Power, temperature and clocks</h4>"
            '<div class="table-wrap"><table><thead><tr><th>Reading</th><th>Average</th><th>Highest</th></tr>'
            "</thead><tbody>" + "".join(rows) + "</tbody></table></div>"
            + ("<h4>Over time</h4>" + "".join(charts) if charts else
               "<p class=\"muted\">No readings over time were stored for this run.</p>")
            + "</div>")
    return "<section><h2>Stress tests</h2>" + "".join(blocks) + "</section>"


def _game_section(export: Mapping) -> str:
    """Per game: big whole numbers (average, 1% and 0.1% low, min, max), the settings the game used and how the
    CPU and GPU behaved during the benchmark."""
    games = _game_runs(export)
    if not games:
        return ""
    summaries = _mapping(export.get("summaries")) or {}
    metrics = _mapping(export.get("metric_summaries")) or {}
    used = (_mapping(export.get("manifest")) or {}).get("benchmarks") or []
    titles = {str(b.get("name")): str(b.get("display_name")).split(" (")[0] for b in used
              if isinstance(b, Mapping) and b.get("display_name")}
    blocks = []
    for name, runs in sorted(games.items()):
        title = esc(titles.get(name, name))
        if not runs:
            blocks.append(f'<div class="game"><h3 dir="auto">{title}</h3>'
                          "<p>No completed run: there are no results to show.</p></div>")
            continue
        stats = _mapping((_mapping(summaries.get(name)) or {}).get("stats")) or {}
        extra = _mapping(metrics.get(name)) or {}

        def mean_of(key):
            return (_mapping(extra.get(key)) or {}).get("mean")

        def lowest(key):
            return (_mapping(extra.get(key)) or {}).get("min")

        def highest(key):
            return (_mapping(extra.get(key)) or {}).get("max")

        tiles = [("Average", stats.get("mean")), ("1% low", mean_of("low_1pct_fps")),
                 ("0.1% low", mean_of("low_01pct_fps")), ("Lowest", lowest("min_fps")),
                 ("Highest", highest("max_fps"))]
        rendered = _whole(mean_of("rendered_fps"))
        if rendered is not None:
            tiles.append(("Rendered (no frame gen)", mean_of("rendered_fps")))
        tile_html = "".join(
            f'<div class="tile"><div class="label">{label}</div><div class="value">'
            + (f"{_whole(value):,} <span>FPS</span>" if _whole(value) is not None else '<span>not measured</span>')
            + "</div></div>"
            for label, value in tiles)
        count = len(runs)
        runs_text = f"{count} measured run" + ("" if count == 1 else "s") + (
            " (numbers are the average of the runs; lowest and highest over all of them)" if count > 1 else "")

        parts = _note_parts(runs[-1].get("note"))
        if parts.get("Settings used"):
            settings_rows = "".join(
                "<tr><th>{}</th><td>{}</td></tr>".format(*(esc(x) for x in item.partition(": ")[::2]))
                for item in parts["Settings used"].split(", "))
            settings_html = ('<table class="kv">' + settings_rows + "</table>")
        else:
            settings_html = "<p>The game did not report its settings for this run.</p>"
        if parts.get("Forced"):
            settings_html += f'<p class="muted">Set by the app before the run: {esc(parts["Forced"])}.</p>'

        rows = _readings_rows(extra)
        span = parts.get("Readings") or "the whole run (game start to close)"
        build = runs[-1].get("benchmark_version")
        if build:
            runs_text += f". Game build {build} (Steam's build number)"
        blocks.append(
            f'<div class="game"><h3 dir="auto">{title}</h3>'
            f'<div class="tiles">{tile_html}</div>'
            f'<p class="muted">{esc(runs_text)}.</p>'
            + _proof_html(parts.get("Proof screenshot"))
            + "<h4>Settings used</h4>" + settings_html
            + "<h4>During the benchmark</h4>"
            f'<p class="muted">Readings over {esc(span)}.</p>'
            '<div class="table-wrap"><table><thead><tr><th>Reading</th><th>Average</th><th>Highest</th></tr>'
            "</thead><tbody>" + "".join(rows) + "</tbody></table></div></div>")
    return "<section><h2>Game results</h2>" + "".join(blocks) + "</section>"


BLENDER_TESTS = ("blender_benchmark", "blender_benchmark_gpu")
BLENDER_SCENES = ("monster", "junkshop", "classroom")
BLENDER_CHARTS = tuple(c for c in STRESS_CHARTS if c[1][0] != "cpu_work")


def _series_charts(run: Mapping, specs) -> list:
    from app.charts import line_chart
    series = _mapping(run.get("series")) or {}
    charts = []
    for chart_title, names, unit in specs:
        for s_name in names:
            points = (_mapping(series.get(s_name)) or {}).get("points") or []
            if len(points) < 2:
                continue
            if s_name == "tel_cpu_clock_avg" and "tel_cpu_core_clock_avg" in series:
                continue
            svg = line_chart(f"{chart_title}: {SERIES_NAMES.get(s_name, s_name)}", [p[0] for p in points],
                             [p[1] for p in points], x_unit="s", y_unit=unit)
            if svg:
                charts.append(f'<div class="chart">{_safe_svg(svg)}</div>')
    return charts


def _blender_section(export: Mapping) -> str:
    """Per Blender test: the score (samples per minute of the three scenes added together, as Blender Open Data
    scores it), each scene, what ran (Blender, device), how the PC behaved and charts over time."""
    session_is_test = _truthy((_mapping(export.get("session")) or {}).get("is_test"))
    by_test: dict = {}
    for run in export.get("runs") or []:
        m = _mapping(run) or {}
        name = str(m.get("benchmark_name") or "")
        if name not in BLENDER_TESTS or _truthy(m.get("is_warmup")):
            continue
        by_test.setdefault(name, [])
        if m.get("status") != "completed" or session_is_test or not _truthy(m.get("is_test")):
            by_test[name].append(m)
    if not by_test:
        return ""
    summaries = _mapping(export.get("summaries")) or {}
    metrics = _mapping(export.get("metric_summaries")) or {}
    used = (_mapping(export.get("manifest")) or {}).get("benchmarks") or []
    titles = {str(b.get("name")): str(b.get("display_name")) for b in used
              if isinstance(b, Mapping) and b.get("display_name")}
    blocks = []
    for name in BLENDER_TESTS:
        if name not in by_test:
            continue
        title = esc(titles.get(name, "Blender Benchmark (" + ("GPU" if name.endswith("gpu") else "CPU") + ")"))
        done = [r for r in by_test[name] if r.get("status") == "completed"]
        if not done:
            last = by_test[name][-1] if by_test[name] else {}
            blocks.append(f'<div class="game"><h3>{title}</h3><div class="tiles"><div class="tile bad">'
                          '<div class="label">Result</div><div class="value">No score</div></div></div>'
                          f'<p>The test did not finish: {_text(last.get("note"))}.</p></div>')
            continue
        stats = _mapping((_mapping(summaries.get(name)) or {}).get("stats")) or {}
        extra = _mapping(metrics.get(name)) or {}

        def mean_of(key):
            return (_mapping(extra.get(key)) or {}).get("mean")

        score = _whole(stats.get("mean"))
        tiles = ['<div class="tile good"><div class="label">Score</div><div class="value">'
                 + (f"{score:,} <span>samples/min</span>" if score is not None else "<span>-</span>")
                 + "</div></div>"]
        rows = []
        for scene in BLENDER_SCENES:
            spm = _whole(mean_of(f"samples_per_minute@{scene}"))
            if spm is None:
                continue
            tiles.append(f'<div class="tile"><div class="label">{scene}</div><div class="value">{spm:,} '
                         "<span>samples/min</span></div></div>")
            render, memory = mean_of(f"render_time_s@{scene}"), mean_of(f"peak_memory_mb@{scene}")
            rows.append(f"<tr><td>{scene}</td><td>{spm:,}</td>"
                        f"<td>{'-' if render is None else f'{render:.1f} s'}</td>"
                        f"<td>{'-' if memory is None else f'{_whole(memory):,} MB'}</td></tr>")
        count = len(done)
        details = {}
        for part in str(done[-1].get("note") or "").split("; "):
            key, sep, value = part.partition(": ")
            if sep:
                details[key] = value
        details.setdefault("Blender", _text(done[-1].get("benchmark_version")))
        details.setdefault("Device", "not recorded by this run (older app version)")
        details.setdefault("Scenes", ", ".join(BLENDER_SCENES))
        details["Measured runs"] = str(count) + (" (numbers are their average)" if count > 1 else "")
        kv = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in details.items())
        charts = _series_charts(done[-1], BLENDER_CHARTS)
        blocks.append(
            f'<div class="game"><h3>{title}</h3><div class="tiles">{"".join(tiles)}</div>'
            '<p class="muted">Score = samples per minute of the three scenes added together, the same score '
            "Blender Open Data shows (opendata.blender.org/benchmarks): higher is faster. Each scene renders for "
            "about 30 seconds. Nothing was uploaded.</p>"
            "<h4>Scenes</h4>"
            '<div class="table-wrap"><table><thead><tr><th>Scene</th><th>Samples per minute</th>'
            "<th>Render time</th><th>Peak memory</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
            "<h4>What ran</h4>" + f'<table class="kv">{kv}</table>'
            "<h4>During the test</h4>"
            '<div class="table-wrap"><table><thead><tr><th>Reading</th><th>Average</th><th>Highest</th></tr>'
            "</thead><tbody>" + "".join(_readings_rows(extra)) + "</tbody></table></div>"
            + ("<h4>Over time (last run)</h4>" + "".join(charts) if charts else
               '<p class="muted">No readings over time were stored for this run.</p>')
            + "</div>")
    return "<section><h2>Blender Benchmark</h2>" + "".join(blocks) + "</section>"


VRAY_TESTS = (("vray_benchmark", "V-Ray (CPU)", "vsamples"), ("vray_benchmark_gpu", "V-Ray GPU, CUDA", "vpaths"),
              ("vray_benchmark_rtx", "V-Ray GPU, RTX", "vpaths"))


def _vray_section(export: Mapping) -> str:
    """V-Ray Benchmark: one tile per engine with its own unit (scores of different engines are not comparable), what
    ran (version, devices), how the PC behaved and charts over time."""
    return _tiles_section(
        export, "V-Ray Benchmark", VRAY_TESTS,
        "V-Ray Benchmark's own scores: vsamples for V-Ray on the CPU, vpaths for V-Ray GPU "
        "(CUDA and RTX). Higher is faster; scores of different engines (CPU, CUDA, RTX) cannot be compared "
        "with each other, only with the same test on benchmark.chaos.com. Each test renders for 1 minute. Nothing "
        "was uploaded.")


CINEBENCH_TESTS = (("cinebench_2026", "Multiple Threads", "pts"), ("cinebench_2026_single", "Single Thread", "pts"))


def _cinebench_section(export: Mapping) -> str:
    return _tiles_section(
        export, "Cinebench 2026", CINEBENCH_TESTS,
        "Cinebench 2026's own points (Maxon). Higher is faster. Multiple Threads uses every CPU thread, Single "
        "Thread one; they cannot be compared with each other, nor with Cinebench 2024 or R23. Each test runs for "
        "Maxon's default minimum time. Nothing was uploaded.")


PASSMARK_TESTS = (("passmark_cpu", "CPU Mark", "points"), ("passmark_2d", "2D Graphics Mark", "points"),
                  ("passmark_3d", "3D Graphics Mark", "points"), ("passmark_memory", "Memory Mark", "points"))


def _passmark_section(export: Mapping) -> str:
    return _tiles_section(
        export, "PassMark PerformanceTest", PASSMARK_TESTS,
        "PassMark PerformanceTest's own marks. Higher is faster. Each mark is PassMark's summary of its group of "
        "tests (CPU, 2D graphics, 3D graphics, memory) and can be compared only with the same mark of the same "
        "PerformanceTest version on passmark.com. PassMark's free version may send the results to PassMark.")


def _pugetbench_section(export: Mapping) -> str:
    from app.pro.pugetbench import TESTS
    return _tiles_section(
        export, "PugetBench for Creators", TESTS,
        "PugetBench's Overall Score for each application, TYPED IN BY YOU from PugetBench's results screen (a "
        "manual entry: the app did not read it). Higher is faster. Scores can be compared only with the same "
        "application, preset and PugetBench version. PugetBench's free version may upload its results to Puget "
        "Systems; command-line runs need Puget's paid license, so the app does not run it.", readings=False)


def _tiles_section(export: Mapping, heading: str, tests, explanation: str, readings: bool = True) -> str:
    """A tool with several tests, each with its own unit: one tile per test, what ran, how the PC behaved and
    charts over time."""
    session_is_test = _truthy((_mapping(export.get("session")) or {}).get("is_test"))
    names = [t[0] for t in tests]
    by_test: dict = {}
    for run in export.get("runs") or []:
        m = _mapping(run) or {}
        name = str(m.get("benchmark_name") or "")
        if name not in names or _truthy(m.get("is_warmup")):
            continue
        if m.get("status") != "completed" or session_is_test or not _truthy(m.get("is_test")):
            by_test.setdefault(name, []).append(m)
    if not by_test:
        return ""
    summaries = _mapping(export.get("summaries")) or {}
    metrics = _mapping(export.get("metric_summaries")) or {}
    tiles, blocks = [], []
    for name, engine, unit in tests:
        runs = by_test.get(name)
        if not runs:
            continue
        done = [r for r in runs if r.get("status") == "completed"]
        if not done:
            tiles.append(f'<div class="tile bad"><div class="label">{esc(engine)}</div>'
                         '<div class="value"><span>no score</span></div></div>')
            blocks.append(f"<h3>{esc(engine)}</h3><p>The test did not finish: {_text(runs[-1].get('note'))}.</p>")
            continue
        score = _whole((_mapping((_mapping(summaries.get(name)) or {}).get("stats")) or {}).get("mean"))
        unit = str(done[-1].get("unit") or unit)          # the unit the program printed
        tiles.append(f'<div class="tile good"><div class="label">{esc(engine)}</div><div class="value">'
                     + (f"{score:,} <span>{unit}</span>" if score is not None else "<span>-</span>") + "</div></div>")
        details = {}
        for part in str(done[-1].get("note") or "").split("; "):
            key, sep, value = part.partition(": ")
            if sep:
                details[key] = value
        details["Measured runs"] = str(len(done)) + (" (the score is their average)" if len(done) > 1 else "")
        retried = sum(1 for r in runs if str(r.get("note") or "").startswith("Retry after a failed try"))
        extra = sum(1 for r in runs if str(r.get("note") or "").startswith("Extra run:"))
        if retried:
            details["Run again after a failure"] = f"{retried} (the failed tries are in the runs table, not in the score)"
        if extra:
            details["Extra runs"] = f"{extra} (the scores varied a lot, so more runs were added)"
        kv = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in details.items())
        if not readings:            # typed in by the owner: no readings were taken during the test
            blocks.append(f"<h3>{esc(engine)}</h3>" + f'<table class="kv">{kv}</table>')
            continue
        charts = _series_charts(done[-1], BLENDER_CHARTS)
        blocks.append(
            f"<h3>{esc(engine)}</h3>" + f'<table class="kv">{kv}</table>'
            "<h4>During the test</h4>"
            '<div class="table-wrap"><table><thead><tr><th>Reading</th><th>Average</th><th>Highest</th></tr>'
            "</thead><tbody>" + "".join(_readings_rows(_mapping(metrics.get(name)) or {})) + "</tbody></table></div>"
            + ("<h4>Over time (last run)</h4>" + "".join(charts) if charts else
               '<p class="muted">No readings over time were stored for this run.</p>'))
    return (f'<section><h2>{esc(heading)}</h2><div class="game"><div class="tiles">' + "".join(tiles) + "</div>"
            f'<p class="muted">{esc(explanation)}</p>' + "".join(blocks) + "</div></section>")


def _session_name(session: Any) -> str:
    mapping = _mapping(session)
    if mapping is not None:
        name = mapping.get("name")
        if isinstance(name, str) and name:
            return esc(name)
    return esc("Untitled session")


def _header_section(export: Mapping) -> str:
    session = _mapping(export.get("session")) or {}
    name = _session_name(session)
    session_id = _text(session.get("id"), "-")
    created_at = _text(session.get("created_at"), "-")

    parts = [
        "<section class=\"header\">",
        f'<h1 dir="auto">{name}</h1>',
        f"<p>Session #{session_id} - created {created_at}</p>",
    ]

    if _truthy(session.get("is_test")):
        parts.append('<p class="badge test">TEST DATA - not real results</p>')

    if _truthy(session.get("is_manual")):
        parts.append('<p class="badge manual">Contains manually entered results</p>')

    parts.append("</section>")
    return "".join(parts)


def _each_run(export: Mapping, name: Any) -> str:
    """Every run's own result for one benchmark, in run order: the warm-up first (not counted), then each
    measured run, so the summary shows each result before the mean."""
    runs = export.get("runs") if isinstance(export.get("runs"), list) else []
    own = [r for r in runs if isinstance(r, Mapping) and r.get("benchmark_name") == name
           and not _truthy(r.get("is_test"))]
    own.sort(key=lambda r: (not _truthy(r.get("is_warmup")), r.get("run_index") or 0))
    parts = []
    for r in own:
        label = "Warm-up" if _truthy(r.get("is_warmup")) else f"Run {r.get('run_index')}"
        score = r.get("score")
        ok = isinstance(score, (int, float)) and not isinstance(score, bool)
        value = (_format_number(score) + (f" {esc(r.get('unit'))}" if r.get("unit") else "")) if ok \
            else esc(r.get("status") or "no result")
        extra = " (not counted)" if _truthy(r.get("is_warmup")) else ""
        parts.append(f"{label}: {value}{extra}")
    return "<br>".join(parts) or "-"


def _summary_section(export: Mapping) -> str:
    summaries = _mapping(export.get("summaries"))
    if summaries is None or not summaries:
        return (
            "<section>"
            "<h2>Summary</h2>"
            "<p>No results recorded.</p>"
            "</section>"
        )

    rows = []
    for name in sorted(summaries.keys(), key=lambda item: str(item)):
        data = _mapping(summaries.get(name)) or {}
        stats = _mapping(data.get("stats")) or {}

        included = data.get("included")
        runs_used = str(included) if isinstance(included, int) and not isinstance(included, bool) else "-"
        if _truthy(data.get("mixed_sources")):
            runs_used += " (mixed manual/real)"

        mean = _format_number(stats.get("mean"))
        median = _format_number(stats.get("median"))
        minimum = _format_number(stats.get("min"))
        maximum = _format_number(stats.get("max"))
        stdev = _format_number(stats.get("stdev"))
        cv = _format_percent(stats.get("cv_percent"))
        excluded = _format_excluded(data.get("excluded"))

        rows.append(
            "<tr>"
            f'<td dir="auto">{esc(name)}</td>'
            f"<td>{runs_used}</td>"
            f'<td class="each-run">{_each_run(export, name)}</td>'
            f"<td>{mean}</td>"
            f"<td>{median}</td>"
            f"<td>{minimum}</td>"
            f"<td>{maximum}</td>"
            f"<td>{stdev}</td>"
            f"<td>{cv}</td>"
            f"<td>{excluded}</td>"
            "</tr>"
        )

    table = (
        "<table>"
        "<thead>"
        "<tr>"
        "<th>Benchmark</th>"
        "<th>Runs used</th>"
        "<th>Each run</th>"
        "<th>Mean</th>"
        "<th>Median</th>"
        "<th>Min</th>"
        "<th>Max</th>"
        "<th>Std dev</th>"
        "<th>CV %</th>"
        "<th>Excluded</th>"
        "</tr>"
        "</thead>"
        "<tbody>"
        + "".join(rows)
        + "</tbody>"
        "</table>"
    )

    return (
        "<section>"
        "<h2>Summary</h2>"
        '<div class="table-wrap">'
        + table
        + "</div>"
        "</section>"
    )


def _runs_section(export: Mapping) -> str:
    runs = export.get("runs")
    if not isinstance(runs, list) or not runs:
        return (
            "<section>"
            "<h2>Runs</h2>"
            "<p>No runs recorded.</p>"
            "</section>"
        )

    rows = []
    for run in runs:
        mapping = _mapping(run) or {}

        benchmark = _text(mapping.get("benchmark_name"), "-")
        run_index = _text(mapping.get("run_index"), "-")
        status = mapping.get("status")
        status_text = _text(status, "-")
        score = mapping.get("score")
        unit = _text(mapping.get("unit"), "-")
        time_text = _text(mapping.get("timestamp"), "-")
        note = _text(mapping.get("note"), "-")

        if status == "completed":
            score_text = _format_number(score)
        else:
            score_text = "-"

        flags = []
        if _truthy(mapping.get("is_warmup")):
            flags.append("warmup")
        if _truthy(mapping.get("is_test")):
            flags.append("test")
        if _truthy(mapping.get("is_manual")):
            flags.append("manual")
        flags_text = ", ".join(flags) if flags else "-"

        rows.append(
            "<tr>"
            f"<td>{benchmark}</td>"
            f"<td>{run_index}</td>"
            f"<td>{status_text}</td>"
            f"<td>{score_text}</td>"
            f"<td>{unit}</td>"
            f"<td>{flags_text}</td>"
            f"<td>{time_text}</td>"
            f'<td class="note" dir="auto">{note}</td>'
            "</tr>"
        )

    table = (
        "<table>"
        "<thead>"
        "<tr>"
        "<th>Benchmark</th>"
        "<th>Run</th>"
        "<th>Status</th>"
        "<th>Score</th>"
        "<th>Unit</th>"
        "<th>Flags</th>"
        "<th>Time</th>"
        "<th>Note</th>"
        "</tr>"
        "</thead>"
        "<tbody>"
        + "".join(rows)
        + "</tbody>"
        "</table>"
    )

    return (
        "<section>"
        "<h2>Runs</h2>"
        '<div class="table-wrap">'
        + table
        + "</div>"
        "</section>"
    )


def _charts_section(export: Mapping) -> str:
    """One bar per measured run of each test (completed, not warm-up; test runs only in a test session)."""
    from app.charts import bar_chart
    session_is_test = _truthy((_mapping(export.get("session")) or {}).get("is_test"))
    by_test: dict = {}
    for run in export.get("runs") or []:
        m = _mapping(run) or {}
        if (m.get("status") != "completed" or _truthy(m.get("is_warmup"))
                or (_truthy(m.get("is_test")) and not session_is_test)):
            continue
        by_test.setdefault(str(m.get("benchmark_name") or "-"), []).append(m)
    used = (_mapping(export.get("manifest")) or {}).get("benchmarks") or []
    titles = {str(b.get("name")): str(b.get("display_name")) for b in used
              if isinstance(b, Mapping) and b.get("display_name")}
    charts = []
    for name, runs in sorted(by_test.items()):
        if len(runs) < 2:
            continue                      # one bar says nothing the table does not
        svg = bar_chart(f"{titles.get(name, name)}, each measured run", [f"Run {_text(r.get('run_index'), str(i + 1))}"
                                                        for i, r in enumerate(runs)],
                        [r.get("score") for r in runs], unit=_text(runs[0].get("unit"), ""))
        if svg:
            # inline SVG needs no xmlns in HTML; no ":" may reach the page (see esc)
            svg = svg.replace(' xmlns="http://www.w3.org/2000/svg"', "").replace(":", "&#58;")
            charts.append(f'<div class="chart">{svg}</div>')
    if not charts:
        return ""
    return "<section><h2>Charts</h2>" + "".join(charts) + "</section>"


def _metric_group(name: str) -> str:
    return "Telemetry" if str(name).startswith("tel_") else "Run"


def _metrics_section(export: Mapping) -> str:
    """Extra numbers per benchmark (frame capture, telemetry) over the same runs as the statistics."""
    summaries = _mapping(export.get("metric_summaries"))
    if not summaries:
        return ""
    rows = []
    for bench in sorted(summaries, key=str):
        metrics = _mapping(summaries.get(bench)) or {}
        for name in sorted(metrics, key=lambda n: (_metric_group(n) == "Telemetry", str(n))):
            m = _mapping(metrics.get(name)) or {}
            if _truthy(m.get("mixed_units")):
                numbers = '<td colspan="3">units differ between runs; not summarized</td>'
            else:
                numbers = "".join(f"<td>{_format_number(m.get(k))}</td>" for k in ("mean", "min", "max"))
            rows.append(
                "<tr>"
                f"<td>{_text(bench)}</td>"
                f"<td>{_metric_group(name)}</td>"
                f"<td>{_text(name)}</td>"
                f"{numbers}"
                f"<td>{_text(m.get('unit'))}</td>"
                f"<td>{_text(m.get('count'))}</td>"
                "</tr>"
            )
    if not rows:
        return ""
    return (
        "<section>"
        "<h2>Extra metrics</h2>"
        "<p>Frame capture numbers and telemetry (tel_*) recorded with each run, over the same runs as the "
        "statistics. A reading a source could not give is absent, never 0.</p>"
        '<div class="table-wrap"><table>'
        "<thead><tr><th>Benchmark</th><th>Kind</th><th>Metric</th><th>Mean</th><th>Min</th><th>Max</th>"
        "<th>Unit</th><th>Runs</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody>"
        "</table></div>"
        "</section>"
    )


def _environment_section(export: Mapping) -> str:
    manifest = _mapping(export.get("manifest"))
    environment = _mapping(manifest.get("environment")) if manifest is not None else None

    if environment is None:
        return (
            "<section>"
            "<h2>Environment</h2>"
            "<p>Environment not recorded for this session.</p>"
            "</section>"
        )

    rows = []
    for field, label in ENVIRONMENT_FIELDS:
        entry = _mapping(environment.get(field))
        if entry is None:
            value_text = "unavailable"
            status_text = "unavailable"
        else:
            value = entry.get("value")
            status = entry.get("status")

            if status == "unavailable" or value is None:
                value_text = "unavailable"
            else:
                value_text = _format_environment_value(value)

            if status is None:
                status_text = "unavailable"
            else:
                status_text = esc(status)

        rows.append(
            "<tr>"
            f"<td>{esc(label)}</td>"
            f"<td>{value_text}</td>"
            f"<td>{status_text}</td>"
            "</tr>"
        )

    table = (
        "<table>"
        "<thead>"
        "<tr>"
        "<th>Field</th>"
        "<th>Value</th>"
        "<th>Status</th>"
        "</tr>"
        "</thead>"
        "<tbody>"
        + "".join(rows)
        + "</tbody>"
        "</table>"
    )

    return (
        "<section>"
        "<h2>Environment</h2>"
        '<div class="table-wrap">'
        + table
        + "</div>"
        "</section>"
    )


def _conditions_section(export: Mapping) -> str:
    """What the PC was doing right before the run (power, power plan, background load), read by the app."""
    manifest = _mapping(export.get("manifest")) or {}
    conditions = _mapping(manifest.get("conditions")) or {}
    items = [m for m in (_mapping(i) for i in conditions.get("items") or []) if m]
    if not items:
        return ""
    rows = "".join(
        f'<tr><td>{esc(i.get("name"))}</td><td>{esc(i.get("value"))}</td>'
        f'<td>{"OK" if _truthy(i.get("ok")) else "Check: " + esc(i.get("advice") or "may lower the scores")}</td></tr>'
        for i in items)
    bad = not all(_truthy(i.get("ok")) for i in items)
    return ("<section><h2>PC before the run</h2>"
            + (f'<p class="muted">Read at {esc(conditions.get("read_at"))}.'
               + (" Something here may have made the scores lower or uneven." if bad else "") + "</p>")
            + '<div class="table-wrap"><table><thead><tr><th>Check</th><th>Value</th><th>Result</th></tr></thead>'
            "<tbody>" + rows + "</tbody></table></div></section>")


def _earlier_section(export: Mapping) -> str:
    """This session's score next to the same test on the same PC before (newest earlier session first)."""
    rows = [m for m in (_mapping(r) for r in export.get("earlier") or []) if m]
    if not rows:
        return ""
    body = []
    for r in rows:
        unit = esc(r.get("unit") or "")
        earlier = [m for m in (_mapping(e) for e in r.get("earlier") or []) if m]
        cmp = _mapping(r.get("compare")) or {}
        issues = [i for i in (_mapping(x) for x in cmp.get("issues") or []) if i]
        blocked = cmp.get("status") == "not_comparable"
        delta = cmp.get("delta_percent")
        change = "-" if blocked or not isinstance(delta, (int, float)) else f"{delta:+.1f}%"
        why = "; ".join(str(i.get("message")) for i in issues if i.get("code") not in ("few_runs", "test_data"))
        history = ", ".join(f"{_format_number(e.get('mean'))} ({esc(str(e.get('created_at') or '')[:10])})"
                            for e in earlier)
        body.append(
            f"<tr><td>{esc(r.get('benchmark'))}</td><td>{_format_number(r.get('now'))} {unit}</td>"
            f"<td>{_format_number(earlier[0].get('mean'))} {unit}<br><span class=\"muted\">"
            f"{esc(earlier[0].get('name'))}</span></td>"
            f"<td>{'not comparable' if blocked else change}</td>"
            f'<td class="note">{esc(why) if why else "-"}</td><td>{history}</td></tr>')
    return ("<section><h2>Compared with earlier runs on this PC</h2>"
            '<p class="muted">The same test on the same PC (same CPU, GPU, RAM and OS family) in earlier sessions. '
            "Change is this run against the newest earlier one. For scores, higher is faster; for times (ms, s) "
            "lower is faster. A different major version, architecture or runtime is not comparable.</p>"
            '<div class="table-wrap"><table><thead><tr><th>Test</th><th>Now</th><th>Before</th><th>Change</th>'
            "<th>Notes</th><th>Earlier sessions (newest first)</th></tr></thead><tbody>"
            + "".join(body) + "</tbody></table></div></section>")


def _reproducibility_section(export: Mapping) -> str:
    manifest = _mapping(export.get("manifest")) or {}
    run_settings = _mapping(manifest.get("run_settings")) or {}
    comparability = _mapping(manifest.get("comparability")) or {}
    app = _mapping(export.get("app")) or {}

    repetitions = _text(run_settings.get("repetitions"), "-")
    warmup_runs = _text(run_settings.get("warmup_runs"), "-")

    runtimes = comparability.get("runtimes")
    if isinstance(runtimes, (list, tuple)) and runtimes:
        runtimes_text = ", ".join(esc(item) for item in runtimes)
    else:
        runtimes_text = "-"

    any_emulated = comparability.get("any_emulated")
    if any_emulated is None:
        emulated_text = "-"
    else:
        emulated_text = "yes" if _truthy(any_emulated) else "no"

    app_version = _text(app.get("version"), "-")
    git_commit = _text(app.get("git_commit"), "-")
    exported_at = _text(export.get("exported_at"), "-")

    rows = (
        f"<tr><td>Repetitions</td><td>{repetitions}</td></tr>"
        f"<tr><td>Warmup runs</td><td>{warmup_runs}</td></tr>"
        f"<tr><td>Runtimes</td><td>{runtimes_text}</td></tr>"
        f"<tr><td>Emulated</td><td>{emulated_text}</td></tr>"
        f"<tr><td>App version</td><td>{app_version}</td></tr>"
        f"<tr><td>Git commit</td><td>{git_commit}</td></tr>"
        f"<tr><td>Exported at</td><td>{exported_at}</td></tr>"
    )

    table = (
        "<table>"
        "<thead>"
        "<tr>"
        "<th>Setting</th>"
        "<th>Value</th>"
        "</tr>"
        "</thead>"
        "<tbody>"
        + rows
        + "</tbody>"
        "</table>"
    )

    return (
        "<section>"
        "<h2>Reproducibility</h2>"
        '<div class="table-wrap">'
        + table
        + "</div>"
        "</section>"
    )


def _style_block() -> str:
    return (
        "<style>"
        ".chart{max-width:760px;margin:12px 0;--chart-bar:var(--accent);--chart-line:var(--accent);"
        "--chart-text:var(--muted);--chart-grid:var(--line);}"
        ":root{"
        "--bg:#f6f7fb;"
        "--panel:#ffffff;"
        "--text:#172033;"
        "--muted:#5b6478;"
        "--line:#d8dde8;"
        "--accent:#2456d6;"
        "--test:#b42318;"
        "--manual:#7a5af8;"
        "}"
        "@media (prefers-color-scheme: dark){"
        ":root{"
        "--bg:#10141c;"
        "--panel:#171d29;"
        "--text:#eef2fb;"
        "--muted:#a7b0c5;"
        "--line:#2b3447;"
        "--accent:#7aa2ff;"
        "--test:#ff8a80;"
        "--manual:#c4b5fd;"
        "}"
        "}"
        "body{"
        "margin:0;"
        "padding:24px;"
        "background:var(--bg);"
        "color:var(--text);"
        "font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;"
        "line-height:1.45;"
        "}"
        "main{"
        "max-width:1100px;"
        "margin:0 auto;"
        "}"
        "section{"
        "background:var(--panel);"
        "border:1px solid var(--line);"
        "border-radius:12px;"
        "padding:18px 20px;"
        "margin:0 0 18px;"
        "}"
        "h1,h2{"
        "margin:0 0 12px;"
        "line-height:1.2;"
        "}"
        "h1{"
        "font-size:1.7rem;"
        "}"
        "h2{"
        "font-size:1.2rem;"
        "color:var(--accent);"
        "}"
        "p{"
        "margin:8px 0;"
        "}"
        ".badge{"
        "display:inline-block;"
        "padding:4px 10px;"
        "border-radius:999px;"
        "font-weight:600;"
        "font-size:0.9rem;"
        "}"
        ".badge.test{"
        "color:var(--test);"
        "border:1px solid var(--test);"
        "}"
        ".badge.manual{"
        "color:var(--manual);"
        "border:1px solid var(--manual);"
        "}"
        ".table-wrap{"
        "overflow-x:auto;"
        "}"
        "table{"
        "width:100%;"
        "border-collapse:collapse;"
        "min-width:720px;"
        "}"
        "th,td{"
        "text-align:left;"
        "padding:9px 10px;"
        "border-bottom:1px solid var(--line);"
        "white-space:nowrap;"
        "}"
        "th{"
        "color:var(--muted);"
        "font-size:0.92rem;"
        "}"
        "tbody tr:last-child td{"
        "border-bottom:none;"
        "}"
        "td.note{white-space:normal;min-width:220px;}"
        ".tiles{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0;}"
        ".tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 16px;"
        "min-width:110px;}"
        ".tile .label{color:var(--muted);font-size:13px;}"
        ".tile .value{font-size:30px;font-weight:700;}"
        ".tile .value span{font-size:14px;font-weight:400;color:var(--muted);}"
        ".muted{color:var(--muted);}"
        ".tile.good .value{color:#1a7f37;}"
        ".tile.bad .value{color:var(--test);}"
        "table.kv th{text-align:left;padding-right:16px;}"
        "img.proof{max-width:100%;height:auto;border-radius:6px;}"
        "@media (max-width:600px){body{padding:16px;}section{padding:14px;}}"
        ".footer{"
        "color:var(--muted);"
        "font-size:0.95rem;"
        "}"
        "</style>"
    )


def render_session_report(export: Mapping) -> str:
    """Render a complete offline HTML5 report for one benchmark session."""
    export_mapping = _mapping(export) or {}
    title_name = _session_name(export_mapping.get("session"))

    document = (
        "<!doctype html>"
        '<html lang="en">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Benchmark report: {title_name}</title>"
        + _style_block()
        + "</head>"
        "<body>"
        "<main>"
        + _header_section(export_mapping)
        + _conditions_section(export_mapping)
        + _game_section(export_mapping)
        + _stress_section(export_mapping)
        + _blender_section(export_mapping)
        + _vray_section(export_mapping)
        + _cinebench_section(export_mapping)
        + _passmark_section(export_mapping)
        + _pugetbench_section(export_mapping)
        + _earlier_section(export_mapping)
        + _summary_section(export_mapping)
        + _charts_section(export_mapping)
        + _metrics_section(export_mapping)
        + _runs_section(export_mapping)
        + _environment_section(export_mapping)
        + _reproducibility_section(export_mapping)
        + (
            "<section>"
            '<p class="footer">Generated by PC Benchmark Manager. '
            "Statistics exclude warmup, failed and test runs unless the session itself is a test session.</p>"
            "</section>"
        )
        + "</main>"
        "</body>"
        "</html>"
    )

    # nothing in the report loads anything: every "src=" is broken, then only the app's own proof screenshots
    # (written by _proof_html, base64 only; any text from a run is escaped, so it has no raw quote) get theirs back
    document = document.replace("src=", "src&#61;")
    return PROOF_ATTR.sub(lambda m: f'{m.group(1)}src="data:image/png;base64,{m.group(2)}"', document)
