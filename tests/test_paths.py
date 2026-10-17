"""Tests for app/paths.py: database location, settings and legacy copy.

All paths are temporary; nothing touches the real data folder or project root.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from app.database import Database
from app.paths import (
    APP_DIR_NAME, DB_FILENAME, copy_database, get_data_dir, load_settings,
    resolve_database_path,
)


def _make_legacy_db(path: Path) -> int:
    db = Database(str(path))
    session_id = db.create_session('Old session', is_test=True)
    db.create_result(session_id, 'speedometer', 'web', 1.0, is_test=True)
    return session_id


class TestDataDir:
    def test_windows_uses_localappdata(self):
        assert get_data_dir({'LOCALAPPDATA': r'C:\Users\x\AppData\Local'}) == \
            Path(r'C:\Users\x\AppData\Local') / APP_DIR_NAME

    def test_linux_uses_xdg_data_home(self):
        assert get_data_dir({'XDG_DATA_HOME': '/home/x/.data'}) == \
            Path('/home/x/.data') / APP_DIR_NAME

    def test_fallback_home_local_share(self):
        assert get_data_dir({}) == Path.home() / '.local' / 'share' / APP_DIR_NAME


class TestSettings:
    def test_missing_file(self, tmp_path):
        assert load_settings(tmp_path) == ({}, [])

    def test_malformed_file_is_ignored_with_note(self, tmp_path):
        (tmp_path / 'settings.json').write_text('{not json', encoding='utf-8')
        settings, notes = load_settings(tmp_path)
        assert settings == {}
        assert len(notes) == 1 and 'settings file ignored' in notes[0]

    def test_non_object_is_ignored(self, tmp_path):
        (tmp_path / 'settings.json').write_text('[1, 2]', encoding='utf-8')
        settings, notes = load_settings(tmp_path)
        assert settings == {}
        assert 'not an object' in notes[0]


class TestResolveDatabasePath:
    def test_default_path_created_without_legacy(self, tmp_path):
        data_dir = tmp_path / 'data'
        path, notes = resolve_database_path(data_dir, legacy_path=tmp_path / 'missing.db')
        assert path == data_dir / DB_FILENAME
        assert data_dir.is_dir()
        assert not path.exists()          # Database() creates it later
        assert notes == []

    def test_legacy_copied_once_and_original_kept(self, tmp_path):
        legacy = tmp_path / 'project' / DB_FILENAME
        legacy.parent.mkdir()
        session_id = _make_legacy_db(legacy)
        legacy_bytes = legacy.read_bytes()
        data_dir = tmp_path / 'data'

        path, notes = resolve_database_path(data_dir, legacy_path=legacy)

        assert path == data_dir / DB_FILENAME
        assert any('copied legacy database' in n for n in notes)
        assert legacy.read_bytes() == legacy_bytes      # original untouched
        copied = Database(str(path))
        assert [s['id'] for s in copied.get_sessions(include_test=True)] == [session_id]
        assert not (data_dir / (DB_FILENAME + '.partial')).exists()

        # Second start: target exists, so no second copy overwrites new data
        copied.create_session('New session')
        path2, notes2 = resolve_database_path(data_dir, legacy_path=legacy)
        assert path2 == path
        assert not any('copied' in n for n in notes2)
        assert len(Database(str(path2)).get_sessions(include_test=True)) == 2

    def test_custom_database_path_from_settings(self, tmp_path):
        data_dir = tmp_path / 'data'
        data_dir.mkdir()
        custom = tmp_path / 'elsewhere' / 'my.db'
        (data_dir / 'settings.json').write_text(
            json.dumps({'database_path': str(custom)}), encoding='utf-8')

        path, notes = resolve_database_path(data_dir, legacy_path=tmp_path / 'missing.db')

        assert path == custom
        assert custom.parent.is_dir()
        assert any('database_path from settings' in n for n in notes)

    def test_invalid_database_path_setting_falls_back(self, tmp_path):
        data_dir = tmp_path / 'data'
        data_dir.mkdir()
        (data_dir / 'settings.json').write_text(
            json.dumps({'database_path': 42}), encoding='utf-8')
        path, notes = resolve_database_path(data_dir, legacy_path=tmp_path / 'missing.db')
        assert path == data_dir / DB_FILENAME
        assert any('ignored' in n for n in notes)


class TestCopyDatabase:
    def test_corrupt_source_raises_and_leaves_no_target(self, tmp_path):
        source = tmp_path / 'corrupt.db'
        source.write_bytes(b'this is not a sqlite database' * 100)
        target = tmp_path / 'out' / DB_FILENAME
        target.parent.mkdir()
        with pytest.raises(sqlite3.DatabaseError):
            copy_database(source, target)
        assert not target.exists()
