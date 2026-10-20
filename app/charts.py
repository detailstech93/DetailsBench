"""Small charts as inline SVG for the standalone HTML report: no scripts, no external files.

Drafted by the local agent, reviewed (a <title> element was missing).
"""

from __future__ import annotations

import html
import math
from typing import Optional

__all__ = [
    "bar_chart",
    "line_chart",
    "threshold_chart",
    "time_below_fps",
]


def _esc(x) -> str:
    return html.escape(str(x), quote=True)


def _is_drawable(x) -> bool:
    if x is None:
        return False
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(v)


def _n(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        return "0"
    if not math.isfinite(v):
        return "0"
    if v == 0:
        return "0"
    s = f"{v:.2f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _axis(x) -> str:
    """Short axis label: 173457 -> 173k, 2500000 -> 2.5M (the bars carry the exact values)."""
    v = float(x)
    for size, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if abs(v) >= size:
            return _n(round(v / size, 1)) + suffix
    return _n(v)


def _unit_suffix(unit) -> str:
    if unit is None:
        return ""
    s = str(unit)
    return f" {s}" if s else ""


def _svg_open(title: str, width: int, height: int) -> str:
    t = _esc(title)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="{t}" width="100%" viewBox="0 0 {_n(width)} {_n(height)}">'
        f"<title>{t}</title>"
    )


def _svg_title(title: str, width: int) -> str:
    t = _esc(title)
    x = _n(width / 2)
    return (
        f'<text x="{x}" y="18" text-anchor="middle" '
        f'fill="var(--chart-text, #374151)" font-size="14">{t}</text>'
    )


def _grid_lines(
    left: float,
    right: float,
    top: float,
    bottom: float,
    y_min: float,
    y_max: float,
    y_unit: str = "",
) -> str:
    if y_max == y_min:
        y_max = y_min + 1.0

    parts = []
    for i in range(4):
        frac = i / 3.0
        y = bottom - frac * (bottom - top)
        value = y_min + frac * (y_max - y_min)
        label = _axis(value) + _unit_suffix(y_unit)

        parts.append(
            f'<line x1="{_n(left)}" y1="{_n(y)}" x2="{_n(right)}" y2="{_n(y)}" '
            f'stroke="var(--chart-grid, #d1d5db)" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{_n(left - 6)}" y="{_n(y + 4)}" text-anchor="end" '
            f'fill="var(--chart-text, #374151)" font-size="10">{_esc(label)}</text>'
        )
    return "".join(parts)


def bar_chart(
    title: str,
    labels: list,
    values: list,
    unit: str = "",
    width: int = 640,
    height: int = 240,
) -> str:
    if len(labels) != len(values):
        raise ValueError("labels and values must have the same length")

    drawable = []
    for label, value in zip(labels, values):
        if _is_drawable(value):
            drawable.append((label, float(value)))

    if not drawable:
        return ""

    W = int(width)
    H = int(height)
    left = 56
    right = W - 12
    top = 28
    bottom = H - 36
    plot_w = max(1.0, right - left)
    plot_h = max(1.0, bottom - top)

    max_value = max(v for _, v in drawable)
    scale_max = max_value * 1.15 if max_value > 0 else 1.0     # room for the value above the tallest bar

    parts = [_svg_open(title, W, H), _svg_title(title, W)]
    parts.append(_grid_lines(left, right, top, bottom, 0.0, scale_max))

    n = len(drawable)
    band = plot_w / n
    bar_w = max(1.0, band * 0.7)

    for idx, (label, value) in enumerate(drawable):
        center = left + band * (idx + 0.5)
        x = center - bar_w / 2.0
        h = (value / scale_max) * plot_h if value > 0 else 0.0
        y = bottom - h

        parts.append(
            f'<rect class="bar" x="{_n(x)}" y="{_n(y)}" '
            f'width="{_n(bar_w)}" height="{_n(h)}" '
            f'fill="var(--chart-bar, #2563eb)"/>'
        )

        label_text = str(label)
        if len(label_text) > 14:
            label_text = label_text[:13] + "…"
        parts.append(
            f'<text x="{_n(center)}" y="{_n(bottom + 16)}" text-anchor="middle" '
            f'fill="var(--chart-text, #374151)" font-size="10">{_esc(label_text)}</text>'
        )

        value_text = _n(value) + _unit_suffix(unit)
        ty = y - 4
        if ty < top + 10:
            ty = top + 10
        parts.append(
            f'<text x="{_n(center)}" y="{_n(ty)}" text-anchor="middle" '
            f'fill="var(--chart-text, #374151)" font-size="10">{_esc(value_text)}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def line_chart(
    title: str,
    xs: list,
    ys: list,
    x_unit: str = "",
    y_unit: str = "",
    width: int = 640,
    height: int = 240,
    y_full: Optional[float] = None,
) -> str:
    """y_full: the whole of what is measured (e.g. the card's memory): the chart's top, so the line shows the part
    used out of all of it."""
    if len(xs) != len(ys):
        raise ValueError("xs and ys must have the same length")

    points = []
    for x, y in zip(xs, ys):
        if _is_drawable(x) and _is_drawable(y):
            points.append((float(x), float(y)))

    if not points:
        return ""

    if len(points) > 2000:
        k = max(1, math.ceil(len(points) / 2000))
        sampled = points[::k]
        if sampled[-1] != points[-1]:
            if len(sampled) < 2000:
                sampled.append(points[-1])
            else:
                sampled[-1] = points[-1]
        points = sampled

    W = int(width)
    H = int(height)
    left = 56
    right = W - 12
    top = 28
    bottom = H - 36
    plot_w = max(1.0, right - left)
    plot_h = max(1.0, bottom - top)

    xs_vals = [p[0] for p in points]
    ys_vals = [p[1] for p in points]

    x_min = min(xs_vals)
    x_max = max(xs_vals)
    if x_min == x_max:
        x_min -= 0.5
        x_max += 0.5

    if y_full is not None and y_full > 0 and all(y >= 0 for y in ys_vals):
        y_min = 0.0
        y_max = max(y_full, max(ys_vals))
    elif all(y >= 0 for y in ys_vals):
        y_min = 0.0
        y_max = max(ys_vals) * 1.1
        if y_max == 0:
            y_max = 1.0
    else:
        y_min = min(ys_vals)
        y_max = max(ys_vals)
        if y_min == y_max:
            pad = abs(y_min) * 0.1
            if pad == 0:
                pad = 1.0
            y_min -= pad
            y_max += pad
        else:
            pad = (y_max - y_min) * 0.1
            y_min -= pad
            y_max += pad

    def x_to_px(x: float) -> float:
        return left + (x - x_min) / (x_max - x_min) * plot_w

    def y_to_px(y: float) -> float:
        return bottom - (y - y_min) / (y_max - y_min) * plot_h

    parts = [_svg_open(title, W, H), _svg_title(title, W)]
    parts.append(_grid_lines(left, right, top, bottom, y_min, y_max, y_unit))

    pts = " ".join(f"{_n(x_to_px(x))},{_n(y_to_px(y))}" for x, y in points)
    parts.append(
        f'<polyline points="{pts}" fill="none" '
        f'stroke="var(--chart-line, #2563eb)" stroke-width="2"/>'
    )

    x_min_label = _n(x_min) + _unit_suffix(x_unit)
    x_max_label = _n(x_max) + _unit_suffix(x_unit)
    parts.append(
        f'<text x="{_n(left)}" y="{_n(bottom + 16)}" text-anchor="start" '
        f'fill="var(--chart-text, #374151)" font-size="10">{_esc(x_min_label)}</text>'
    )
    parts.append(
        f'<text x="{_n(right)}" y="{_n(bottom + 16)}" text-anchor="end" '
        f'fill="var(--chart-text, #374151)" font-size="10">{_esc(x_max_label)}</text>'
    )

    parts.append("</svg>")
    return "".join(parts)


def time_below_fps(frametimes_ms: list, thresholds_fps) -> list:
    drawable = []
    for ft in frametimes_ms:
        if _is_drawable(ft):
            v = float(ft)
            if v > 0:
                drawable.append(v)

    if not drawable:
        return []

    total = sum(drawable)
    results = []
    for t in thresholds_fps:
        if not _is_drawable(t):
            continue
        t_val = float(t)
        if t_val <= 0:
            continue
        limit = 1000.0 / t_val
        slow_time = sum(ft for ft in drawable if ft > limit)
        percent = 100.0 * slow_time / total
        results.append((t, percent))
    return results


def threshold_chart(
    title: str,
    frametimes_ms: list,
    thresholds_fps: tuple = (30, 60, 90, 120, 144, 165, 240),
    width: int = 640,
    height: int = 240,
) -> str:
    results = time_below_fps(frametimes_ms, thresholds_fps)
    if not results:
        return ""

    W = int(width)
    H = int(height)
    left = 56
    right = W - 12
    top = 28
    bottom = H - 36
    plot_w = max(1.0, right - left)
    plot_h = max(1.0, bottom - top)

    parts = [_svg_open(title, W, H), _svg_title(title, W)]
    parts.append(_grid_lines(left, right, top, bottom, 0.0, 100.0, "%"))

    n = len(results)
    band = plot_w / n
    bar_w = max(1.0, band * 0.7)

    for idx, (threshold, percent) in enumerate(results):
        center = left + band * (idx + 0.5)
        x = center - bar_w / 2.0
        h = (percent / 100.0) * plot_h
        y = bottom - h

        parts.append(
            f'<rect class="bar" x="{_n(x)}" y="{_n(y)}" '
            f'width="{_n(bar_w)}" height="{_n(h)}" '
            f'fill="var(--chart-bar, #2563eb)"/>'
        )

        label = f"<{_n(threshold)} FPS"
        parts.append(
            f'<text x="{_n(center)}" y="{_n(bottom + 16)}" text-anchor="middle" '
            f'fill="var(--chart-text, #374151)" font-size="10">{_esc(label)}</text>'
        )

        value_text = _n(percent) + "%"
        ty = y - 4
        if ty < top + 10:
            ty = top + 10
        parts.append(
            f'<text x="{_n(center)}" y="{_n(ty)}" text-anchor="middle" '
            f'fill="var(--chart-text, #374151)" font-size="10">{_esc(value_text)}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)
