"""Tests for app.automation.parsers."""

from app.automation.parsers import parse_output


def test_regex_last_match_wins():
    parser = {
        "type": "regex",
        "pattern": r"score=(?P<score>\d+)",
        "unit": "ops",
    }
    stdout = "score=1\nscore=2\nscore=3"

    result = parse_output(parser, stdout)

    assert result.error is None
    assert result.score == 3.0
    assert result.unit == "ops"
    assert result.metrics == {}


def test_regex_thousands_separator():
    parser = {
        "type": "regex",
        "pattern": r"score=(?P<score>[\d,]+(?:\.\d+)?)",
    }
    stdout = "score=12,345.6"

    result = parse_output(parser, stdout)

    assert result.error is None
    assert result.score == 12345.6


def test_regex_extra_named_group_into_metrics():
    parser = {
        "type": "regex",
        "pattern": r"score=(?P<score>\d+) latency=(?P<latency>\d+)",
    }
    stdout = "score=10 latency=25"

    result = parse_output(parser, stdout)

    assert result.error is None
    assert result.score == 10.0
    assert result.metrics == {"latency": 25.0}


def test_regex_no_match():
    parser = {
        "type": "regex",
        "pattern": r"score=(?P<score>\d+)",
    }
    stdout = "no score here"

    result = parse_output(parser, stdout)

    assert result.score is None
    assert result.error == "pattern not found in output"


def test_json_nested_path():
    parser = {
        "type": "json",
        "path": "results.bench.score",
    }
    stdout = '{"results": {"bench": {"score": 42}}}'

    result = parse_output(parser, stdout)

    assert result.error is None
    assert result.score == 42.0


def test_json_last_json_line_wins():
    parser = {
        "type": "json",
        "path": "score",
    }
    stdout = '{"score": 1}\n{"score": 2}'

    result = parse_output(parser, stdout)

    assert result.error is None
    assert result.score == 2.0


def test_json_invalid():
    parser = {
        "type": "json",
        "path": "score",
    }
    stdout = '{"score": 1'

    result = parse_output(parser, stdout)

    assert result.score is None
    assert result.error == "no JSON object in output"


def test_json_path_missing():
    parser = {
        "type": "json",
        "path": "missing",
    }
    stdout = '{"score": 1}'

    result = parse_output(parser, stdout)

    assert result.score is None
    assert result.error == "path not found: missing"


def test_json_bool_value_rejected():
    parser = {
        "type": "json",
        "path": "score",
    }
    stdout = '{"score": true}'

    result = parse_output(parser, stdout)

    assert result.score is None
    assert result.error == "score is not a number: True"


def test_last_number_negative_and_decimals_rejected():
    parser = {"type": "last_number"}
    stdout = "value -12.34"

    result = parse_output(parser, stdout)

    assert result.score is None
    assert result.error == "score is not positive: -12.34"


def test_last_number_positive_decimal():
    parser = {"type": "last_number"}
    stdout = "value 12.34"

    result = parse_output(parser, stdout)

    assert result.error is None
    assert result.score == 12.34


def test_zero_score_rejected():
    parser = {
        "type": "regex",
        "pattern": r"score=(?P<score>\d+)",
    }
    stdout = "score=0"

    result = parse_output(parser, stdout)

    assert result.score is None
    assert result.error == "score is not positive: 0.0"


def test_inf_via_regex_is_not_finite():
    parser = {
        "type": "regex",
        "pattern": r"(?P<score>inf)",
    }
    stdout = "inf"

    result = parse_output(parser, stdout)

    assert result.score is None
    assert result.error == "score is not finite"


def test_empty_output():
    parser = {
        "type": "regex",
        "pattern": r"score=(?P<score>\d+)",
    }

    result = parse_output(parser, "")

    assert result.score is None
    assert result.error == "empty output"


def test_whitespace_only_output():
    parser = {
        "type": "regex",
        "pattern": r"score=(?P<score>\d+)",
    }

    result = parse_output(parser, "   \n\t  ")

    assert result.score is None
    assert result.error == "empty output"


def test_unknown_type():
    parser = {"type": "wat"}

    result = parse_output(parser, "1")

    assert result.score is None
    assert result.error == "unknown parser type: wat"


def test_unit_passed_through():
    parser = {
        "type": "regex",
        "pattern": r"score=(?P<score>\d+)",
        "unit": "ms",
    }
    stdout = "score=5"

    result = parse_output(parser, stdout)

    assert result.error is None
    assert result.score == 5.0
    assert result.unit == "ms"


def test_comma_only_as_thousands_separator():
    from app.automation.parsers import parse_output
    rx = {"type": "regex", "pattern": r"S=(?P<score>\S+)"}
    assert parse_output(rx, "S=1,234.5").score == 1234.5
    for out in ("S=12,5", "S=1,23"):
        result = parse_output(rx, out)
        assert result.score is None and result.error.startswith("ambiguous number")
    assert parse_output({"type": "last_number"}, "total 12,5").error == "ambiguous number: 12,5"
    assert parse_output({"type": "last_number"}, "total 12,500").score == 12500.0
