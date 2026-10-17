"""Tests for app/steam/vdf.py (pure parser, fake text)."""

from app.steam.vdf import parse_vdf


def test_doubled_backslashes_as_in_the_real_file():
    # The real libraryfolders.vdf stores C:\\Program Files (x86)\\Steam (doubled backslashes)
    text = '"libraryfolders" { "0" { "path" "' + r"C:\\Program Files (x86)\\Steam" + '" } }'
    assert parse_vdf(text)["libraryfolders"]["0"]["path"] == r"C:\Program Files (x86)\Steam"


def test_unknown_escape_keeps_backslash():
    assert parse_vdf('"k" "' + r"a\qb" + '"') == {"k": r"a\qb"}


def test_duplicate_key_last_wins():
    assert parse_vdf('"k" "1" "k" "2"') == {"k": "2"}


def test_libraryfolders_excerpt():
    sample = r'''
"libraryfolders"
{
	"0"
	{
		"path"		"C:\Program Files (x86)\Steam"
		"label"		""
		"apps"
		{
			"228980"		"279049391"
		}
	}
	"1"
	{
		"path"		"Z:\SteamLibrary"
		"apps"
		{
			"730"		"71644882396"
			"292030"		"69761598608"
		}
	}
}
'''

    result = parse_vdf(sample)

    assert result is not None
    assert result["libraryfolders"]["0"]["path"] == r"C:\Program Files (x86)\Steam"
    assert result["libraryfolders"]["0"]["label"] == ""
    assert result["libraryfolders"]["1"]["apps"] == {
        "730": "71644882396",
        "292030": "69761598608",
    }


def test_appmanifest_excerpt():
    text = (
        r'"AppState" { '
        r'"appid" "228980" '
        r'"name" "Steamworks Common Redistributables" '
        r'"StateFlags" "4" '
        r'"installdir" "Steamworks Shared" '
        r'}'
    )

    assert parse_vdf(text) == {
        "AppState": {
            "appid": "228980",
            "name": "Steamworks Common Redistributables",
            "StateFlags": "4",
            "installdir": "Steamworks Shared",
        }
    }


def test_quoted_escapes():
    assert parse_vdf(r'"k" "a\"b"') == {"k": 'a"b'}
    assert parse_vdf(r'"k" "line\nnext"') == {"k": "line\nnext"}


def test_comments_are_ignored_outside_quotes():
    text = """
// comment
"k" "v" // trailing comment
"comment" "a//b"
"""

    assert parse_vdf(text) == {
        "k": "v",
        "comment": "a//b",
    }


def test_unquoted_tokens():
    assert parse_vdf("appid 228980\nname Steamworks") == {
        "appid": "228980",
        "name": "Steamworks",
    }

    assert parse_vdf("root { child value }") == {
        "root": {
            "child": "value",
        }
    }


def test_malformed_text_returns_none():
    assert parse_vdf('"a" { "b" "c"') is None
    assert parse_vdf('"a" { "b" "c" } }') is None
    assert parse_vdf('"a" "b" "c"') is None
    assert parse_vdf('"a" "b') is None
    assert parse_vdf(None) is None


def test_empty_text_and_bom():
    assert parse_vdf("") == {}
    assert parse_vdf("\ufeff") == {}
    assert parse_vdf("\ufeff\"k\" \"v\"") == {"k": "v"}
