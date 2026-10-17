"""Tests for app/discovery/store_parsers.py (fake inputs)."""

import types

from app.discovery.store_parsers import parse_epic_manifest, parse_gog_entry


def test_valid_epic_manifest():
    text = (
        '{"DisplayName": "Game", '
        '"AppName": "abc", '
        '"InstallLocation": "C:/game", '
        '"AppVersionString": "1.0"}'
    )

    entry = parse_epic_manifest(text)

    assert entry == {
        "store": "epic",
        "app_id": "abc",
        "name": "Game",
        "install_path": "C:/game",
        "version": "1.0",
    }


def test_epic_incomplete_install_returns_none():
    text = (
        '{"DisplayName": "Game", '
        '"AppName": "abc", '
        '"InstallLocation": "C:/game", '
        '"bIsIncompleteInstall": true}'
    )

    assert parse_epic_manifest(text) is None


def test_epic_invalid_json_returns_none():
    assert parse_epic_manifest("{not json") is None


def test_epic_top_level_json_list_returns_none():
    assert parse_epic_manifest("[]") is None


def test_epic_missing_install_location_returns_none():
    text = '{"DisplayName": "Game", "AppName": "abc"}'

    assert parse_epic_manifest(text) is None


def test_epic_version_missing_is_none():
    text = '{"DisplayName": "Game", "AppName": "abc", "InstallLocation": "C:/game"}'

    entry = parse_epic_manifest(text)

    assert entry is not None
    assert entry["version"] is None


def test_epic_whitespace_is_stripped():
    text = (
        '{"DisplayName": "  Game  ", '
        '"AppName": " abc ", '
        '"InstallLocation": " C:/game ", '
        '"AppVersionString": " 1.0 "}'
    )

    entry = parse_epic_manifest(text)

    assert entry == {
        "store": "epic",
        "app_id": "abc",
        "name": "Game",
        "install_path": "C:/game",
        "version": "1.0",
    }


def test_valid_gog_entry():
    values = {
        "gameName": "Game",
        "path": "C:/game",
        "gameID": "123",
        "ver": "2.0",
    }

    entry = parse_gog_entry("123", values)

    assert entry == {
        "store": "gog",
        "app_id": "123",
        "name": "Game",
        "install_path": "C:/game",
        "version": "2.0",
    }


def test_gog_gameid_missing_falls_back_to_key_name():
    values = {
        "gameName": "Game",
        "path": "C:/game",
    }

    entry = parse_gog_entry("456", values)

    assert entry is not None
    assert entry["app_id"] == "456"


def test_gog_missing_path_returns_none():
    values = {"gameName": "Game"}

    assert parse_gog_entry("123", values) is None


def test_gog_mapping_proxy_type_works():
    values = types.MappingProxyType(
        {
            "gameName": "Game",
            "path": "C:/game",
            "gameID": "123",
        }
    )

    entry = parse_gog_entry("123", values)

    assert entry == {
        "store": "gog",
        "app_id": "123",
        "name": "Game",
        "install_path": "C:/game",
        "version": None,
    }


def test_gog_values_not_mapping_returns_none():
    assert parse_gog_entry("123", ["gameName", "Game"]) is None
