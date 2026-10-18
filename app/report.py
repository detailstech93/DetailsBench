"""Render a standalone HTML benchmark session report.

One file: inline CSS, no scripts, no external resources; every input value is
escaped by esc(). Drafted by the local agent, reviewed.
"""

import html
import math
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
        if sep and key in ("Settings used", "Forced", "Readings"):
            out[key] = value
    return out


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

        rows = []
        for label, keys, unit in GAME_READINGS:
            key = next((k for k in keys if f"tel_{k}_avg" in extra), None)
            avg = _whole(mean_of(f"tel_{key}_avg")) if key else None
            top = _whole(highest(f"tel_{key}_max")) if key else None
            if avg is None and top is None:
                rows.append(f'<tr><td>{label}</td><td colspan="2" class="muted">not measured (no reading '
                            "in this run)</td></tr>")
                continue
            rows.append(f"<tr><td>{label}</td>"
                        f"<td>{'-' if avg is None else f'{avg:,} {unit}'}</td>"
                        f"<td>{'-' if top is None else f'{top:,} {unit}'}</td></tr>")
        span = parts.get("Readings") or "the whole run (game start to close)"
        blocks.append(
            f'<div class="game"><h3 dir="auto">{title}</h3>'
            f'<div class="tiles">{tile_html}</div>'
            f'<p class="muted">{esc(runs_text)}.</p>'
            "<h4>Settings used</h4>" + settings_html
            + "<h4>During the benchmark</h4>"
            f'<p class="muted">Readings over {esc(span)}.</p>'
            '<div class="table-wrap"><table><thead><tr><th>Reading</th><th>Average</th><th>Highest</th></tr>'
            "</thead><tbody>" + "".join(rows) + "</tbody></table></div></div>")
    return "<section><h2>Game results</h2>" + "".join(blocks) + "</section>"


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
        "table.kv th{text-align:left;padding-right:16px;}"
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
        + _game_section(export_mapping)
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

    return document.replace("src=", "src&#61;")
