# tests/test_csv_export.py
import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.csv_export import CSV_COLUMNS, runs_to_csv, safe_cell


def test_header_exact():
    text = runs_to_csv([], {})
    expected = ",".join(CSV_COLUMNS) + "\r\n"
    assert text == expected


def test_bool_vs_int():
    assert safe_cell(True) == "1"
    assert safe_cell(False) == "0"
    assert safe_cell(1) == "1"
    assert safe_cell(0) == "0"
    assert safe_cell(-1) == "-1"


def test_nan_inf():
    assert safe_cell(float("nan")) == ""
    assert safe_cell(float("inf")) == ""
    assert safe_cell(float("-inf")) == ""


def test_negative_float_not_guarded():
    assert safe_cell(-5.0) == "-5.0"
    assert safe_cell(-0.1) == "-0.1"


def test_formula_guard_equals():
    assert safe_cell("=cmd") == "'=cmd"


def test_formula_guard_at():
    assert safe_cell("@x") == "'@x"


def test_formula_guard_leading_tab():
    assert safe_cell("\tcmd") == "'\tcmd"


def test_formula_guard_leading_cr():
    assert safe_cell("\rcmd") == "'\rcmd"


def test_formula_guard_plus_minus():
    assert safe_cell("+x") == "'+x"
    assert safe_cell("-x") == "'-x"


def test_float_repr_values():
    assert safe_cell(2500.0) == "2500.0"
    assert safe_cell(0.1) == "0.1"


def test_note_with_comma_and_quote_roundtrips():
    note = 'a,b "c"'
    row = {"session_id": "s1", "note": note}
    session = {"id": "s1", "name": "n"}

    text = runs_to_csv([row], session)
    rows = list(csv.reader(io.StringIO(text, newline="")))

    assert rows[0] == CSV_COLUMNS
    assert rows[1][CSV_COLUMNS.index("note")] == note


def test_note_with_newline_roundtrips():
    note = "line1\nline2"
    row = {"session_id": "s1", "note": note}
    session = {"id": "s1", "name": "n"}

    text = runs_to_csv([row], session)
    rows = list(csv.reader(io.StringIO(text, newline="")))

    assert rows[1][CSV_COLUMNS.index("note")] == note


def test_session_id_fallback_from_session():
    row = {"benchmark_name": "b"}
    session = {"id": "s1", "name": "n"}

    text = runs_to_csv([row], session)
    rows = list(csv.reader(io.StringIO(text, newline="")))

    assert rows[1][CSV_COLUMNS.index("session_id")] == "s1"
    assert rows[1][CSV_COLUMNS.index("session_name")] == "n"

    row_none = {"session_id": None}
    text2 = runs_to_csv([row_none], session)
    rows2 = list(csv.reader(io.StringIO(text2, newline="")))

    assert rows2[1][CSV_COLUMNS.index("session_id")] == "s1"


def test_none_row_skipped():
    text = runs_to_csv([None, {"session_id": "s1"}], {})
    rows = list(csv.reader(io.StringIO(text, newline="")))

    assert len(rows) == 2
    assert rows[1][CSV_COLUMNS.index("session_id")] == "s1"


def test_unicode_arabic_kept():
    row = {"session_id": "s1", "note": "مرحبا"}
    session = {"id": "s1", "name": "اسم"}

    text = runs_to_csv([row], session)
    rows = list(csv.reader(io.StringIO(text, newline="")))

    assert rows[1][CSV_COLUMNS.index("note")] == "مرحبا"
    assert rows[1][CSV_COLUMNS.index("session_name")] == "اسم"


def test_empty_runs_header_only():
    text = runs_to_csv([], {})
    expected = ",".join(CSV_COLUMNS) + "\r\n"
    assert text == expected


def test_missing_keys_produce_empty_cells():
    text = runs_to_csv([{}], {})
    rows = list(csv.reader(io.StringIO(text, newline="")))

    assert len(rows) == 2
    assert rows[1] == [""] * len(CSV_COLUMNS)


def test_extra_keys_ignored():
    row = {"session_id": "s1", "extra": "x"}
    text = runs_to_csv([row], {})
    rows = list(csv.reader(io.StringIO(text, newline="")))

    assert rows[1][CSV_COLUMNS.index("session_id")] == "s1"
    assert len(rows[1]) == len(CSV_COLUMNS)


def test_session_none_does_not_raise():
    text = runs_to_csv([{"session_id": "s1"}], None)
    rows = list(csv.reader(io.StringIO(text, newline="")))

    assert rows[1][CSV_COLUMNS.index("session_id")] == "s1"
    assert rows[1][CSV_COLUMNS.index("session_name")] == ""
