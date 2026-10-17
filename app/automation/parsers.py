"""Parse benchmark scores from command-line tool output.

Drafted by the local agent, reviewed. A comma is accepted only as a thousands separator (1,234.5);
"12,5" is rejected as ambiguous instead of becoming 125.
"""

import json
import math
import re
from dataclasses import dataclass, field
from typing import Optional

_THOUSANDS_RE = re.compile(r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def _number_text(raw: str) -> Optional[str]:
    """raw without thousands separators, or None when a comma is used any other way."""
    raw = raw.strip()
    if "," not in raw:
        return raw
    if _THOUSANDS_RE.fullmatch(raw):
        return raw.replace(",", "")
    return None


@dataclass
class ParseResult:
    score: Optional[float] = None
    unit: Optional[str] = None
    metrics: dict = field(default_factory=dict)
    error: Optional[str] = None


def _failure(unit: Optional[str], error: str, metrics: Optional[dict] = None) -> ParseResult:
    return ParseResult(
        score=None,
        unit=unit,
        metrics=dict(metrics) if metrics else {},
        error=error,
    )


def _success(unit: Optional[str], score: float, metrics: dict) -> ParseResult:
    return ParseResult(
        score=score,
        unit=unit,
        metrics=metrics,
        error=None,
    )


def _finalize(unit: Optional[str], score: float, metrics: dict) -> ParseResult:
    try:
        is_finite = math.isfinite(score)
    except (TypeError, ValueError):
        return _failure(unit, "score is not finite")

    if not is_finite:
        return _failure(unit, "score is not finite")

    if score <= 0:
        return _failure(unit, "score is not positive: " + str(score))

    return _success(unit, score, metrics)


def _parse_regex(parser: dict, unit: Optional[str], stdout: str) -> ParseResult:
    pattern = parser.get("pattern")
    if not isinstance(pattern, str):
        return _failure(unit, "pattern not found in output")

    try:
        compiled = re.compile(pattern, re.MULTILINE)
    except (TypeError, re.error):
        return _failure(unit, "pattern not found in output")

    last_match = None
    try:
        for match in compiled.finditer(stdout):
            last_match = match
    except re.error:
        return _failure(unit, "pattern not found in output")

    if last_match is None:
        return _failure(unit, "pattern not found in output")

    try:
        score_raw = last_match.group("score")
    except (IndexError, re.error):
        return _failure(unit, "score is not a number: None")

    if score_raw is None:
        return _failure(unit, "score is not a number: None")

    score_text = _number_text(score_raw)
    if score_text is None:
        return _failure(unit, "ambiguous number: " + score_raw)
    try:
        score = float(score_text)
    except (ValueError, TypeError, OverflowError):
        return _failure(unit, "score is not a number: " + score_raw)

    metrics = {}
    try:
        groups = last_match.groupdict()
    except re.error:
        groups = {}

    for name, value in groups.items():
        if name == "score" or value is None:
            continue
        try:
            metrics[name] = float(value)
        except (ValueError, TypeError):
            metrics[name] = value.strip()

    return _finalize(unit, score, metrics)


def _parse_json(parser: dict, unit: Optional[str], stdout: str) -> ParseResult:
    candidate = None
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            candidate = stripped

    if candidate is None:
        return _failure(unit, "no JSON object in output")

    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return _failure(unit, "no JSON object in output")

    path = parser.get("path")
    if not isinstance(path, str):
        return _failure(unit, "path not found: " + repr(path))

    current = data
    for key in path.split("."):
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return _failure(unit, "path not found: " + path)

    value = current
    if isinstance(value, bool):
        return _failure(unit, "score is not a number: " + repr(value))

    if not isinstance(value, (int, float)):
        return _failure(unit, "score is not a number: " + repr(value))

    try:
        score = float(value)
    except (ValueError, TypeError, OverflowError):
        return _failure(unit, "score is not finite")

    return _finalize(unit, score, {})


def _parse_last_number(parser: dict, unit: Optional[str], stdout: str) -> ParseResult:
    matches = re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", stdout)
    if not matches:
        return _failure(unit, "no number in output")

    raw = matches[-1]
    text = _number_text(raw)
    if text is None:
        return _failure(unit, "ambiguous number: " + raw)

    try:
        score = float(text)
    except (ValueError, TypeError, OverflowError):
        return _failure(unit, "score is not a number: " + raw)

    return _finalize(unit, score, {})


def parse_output(parser: dict, stdout: str, stderr: str = "") -> ParseResult:
    if not isinstance(parser, dict):
        return _failure(None, "unknown parser type: None")

    unit = parser.get("unit")

    if not isinstance(stdout, str) or not stdout.strip():
        return _failure(unit, "empty output")

    parser_type = parser.get("type")

    if parser_type == "regex":
        return _parse_regex(parser, unit, stdout)

    if parser_type == "json":
        return _parse_json(parser, unit, stdout)

    if parser_type == "last_number":
        return _parse_last_number(parser, unit, stdout)

    return _failure(unit, "unknown parser type: " + str(parser_type))
