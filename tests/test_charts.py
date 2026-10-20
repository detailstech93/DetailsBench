"""SVG charts for the report (drafted by the local agent, reviewed)."""

import xml.etree.ElementTree as ET

import pytest

from app.charts import bar_chart, line_chart, threshold_chart, time_below_fps


def _count_bars(svg: str) -> int:
    root = ET.fromstring(svg)
    return sum(
        1
        for el in root.iter()
        if el.tag.endswith("rect") and el.attrib.get("class") == "bar"
    )


def test_bar_chart_basic():
    svg = bar_chart(
        "<b>&",
        ["a", "b", "c"],
        [1, 2, 3],
        unit="MIPS",
    )
    root = ET.fromstring(svg)
    assert _count_bars(svg) == 3
    assert root.attrib["aria-label"] == "<b>&"            # escaped in the file, plain after parsing
    title = root.find("{http://www.w3.org/2000/svg}title")
    assert title is not None
    assert title.text == "<b>&"
    assert "&lt;b&gt;&amp;" in svg


def test_bar_chart_all_invalid_returns_empty():
    assert bar_chart("t", ["a", "b"], [None, float("nan")]) == ""


def test_bar_chart_length_mismatch_raises():
    with pytest.raises(ValueError):
        bar_chart("t", ["a"], [1, 2])


def test_bar_chart_skips_none_value():
    svg = bar_chart("t", ["a", "b", "c"], [1, None, 3])
    assert _count_bars(svg) == 2


def test_line_chart_parses_and_has_one_polyline():
    svg = line_chart("t", [0, 1, 2], [0, 1, 2])
    root = ET.fromstring(svg)
    polylines = [el for el in root.iter() if el.tag.endswith("polyline")]
    assert len(polylines) == 1


def test_line_chart_downsamples_10000_points():
    xs = list(range(10000))
    ys = [float(i) for i in range(10000)]
    svg = line_chart("t", xs, ys)
    root = ET.fromstring(svg)
    polyline = next(el for el in root.iter() if el.tag.endswith("polyline"))
    points = polyline.attrib["points"].split()
    assert len(points) <= 2000


def test_line_chart_empty_returns_empty():
    assert line_chart("t", [], []) == ""


def test_time_below_fps_values():
    result = time_below_fps([10, 10, 20, 40], (60, 30))
    assert len(result) == 2
    assert result[0][0] == 60
    assert result[0][1] == pytest.approx(75.0)
    assert result[1][0] == 30
    assert result[1][1] == pytest.approx(50.0)


def test_threshold_chart_empty_returns_empty():
    assert threshold_chart("t", []) == ""


def test_threshold_chart_parses():
    svg = threshold_chart("t", [10, 20, 40])
    ET.fromstring(svg)


def test_line_charts_label_every_round_step_and_load_tops_at_100():
    """Owner, 2026-10-20: more labels on Y (temperature, power) to read closely; a load chart showed 109.89 %."""
    import re
    from app.charts import line_chart, nice_step

    def labels(svg):          # the y axis: the labels with a unit
        return [t for t in re.findall(r'text-anchor="end" fill="var\(--chart-text, #374151\)" font-size="10">([^<]+)<', svg) if " " in t]
    assert labels(line_chart("Load", [0, 1, 2], [5, 99.9, 60], y_unit="%", y_full=100.0)) == \
        [f"{v} %" for v in range(0, 101, 10)]
    assert labels(line_chart("Temp", [0, 1, 2], [40, 66, 85], y_unit="C"))[-1] == "90 C"
    assert len(labels(line_chart("Power", [0, 1, 2], [30, 180, 250], y_unit="W"))) >= 10
    assert nice_step(100) == 10 and nice_step(270) == 25 and nice_step(0) == 1.0
