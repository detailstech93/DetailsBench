"""The app's own updates: the release check (a fake GitHub), the git update (fake git and pip) and the screens.
Nothing here goes on the internet or runs git."""

import io
import json
import subprocess
import sys
import urllib.error
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from app.paths import load_settings
from app.update import updater

RELEASES = [
    {"tag_name": "v0.3.0-beta.1", "prerelease": True, "draft": False, "html_url": "https://x/b", "body": "beta"},
    {"tag_name": "v0.2.0", "prerelease": False, "draft": False, "html_url": "https://x/r", "body": "new things"},
    {"tag_name": "v9.0.0", "prerelease": False, "draft": True},
    {"tag_name": "nightly", "prerelease": False, "draft": False},
    {"tag_name": "v0.1.0", "prerelease": False, "draft": False},
]
RELEASE = updater.Release("0.2.0", "v0.2.0", "https://x/r")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_versions_sort_with_betas_before_their_final():
    k = updater.version_key
    assert k("v0.2.0") > k("0.1.9") > k("0.1.9-beta.10") > k("0.1.9-beta.2") > k("0.1.9-alpha")
    assert k("1.2") == k("1.2.0") and k("x1") is None and k("v1.2.3; rm -rf") is None


def test_the_newest_release_above_this_version_no_drafts_and_betas_only_when_asked():
    found = updater.newest(RELEASES, "0.1.0")
    assert (found.version, found.tag, found.url, found.beta) == ("0.2.0", "v0.2.0", "https://x/r", False)
    assert updater.newest(RELEASES, "0.1.0", beta=True).tag == "v0.3.0-beta.1"
    assert updater.newest(RELEASES, "0.2.0") is None
    assert updater.newest({"message": "x"}, "0.1.0") is None


def test_the_check_reads_githubs_list_and_says_why_when_it_cannot():
    seen = {}

    def opener(req, timeout):
        seen.update(url=req.full_url, agent=req.get_header("User-agent"))
        return Response(json.dumps(RELEASES).encode())
    result = updater.check(opener, current="0.1.0")
    assert result.release.version == "0.2.0" and "0.2.0 is out" in result.message and not result.error
    assert seen["url"] == updater.RELEASES_URL and seen["agent"] == updater.USER_AGENT
    assert "newest version" in updater.check(opener, current="0.2.0").message

    def private(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    result = updater.check(private)
    assert result.release is None and not result.error and "No published release" in result.message

    def offline(req, timeout):
        raise OSError("no route")
    result = updater.check(offline)
    assert result.error and "no route" in result.message


def test_the_check_at_start_is_daily_can_be_off_and_a_skipped_version_is_not_offered():
    now = datetime(2026, 10, 21, 12, 0)
    assert updater.due({}, now)
    assert not updater.due({updater.CHECK_KEY: False}, now)
    assert not updater.due({updater.LAST_KEY: (now - timedelta(hours=3)).isoformat()}, now)
    assert updater.due({updater.LAST_KEY: (now - timedelta(days=2)).isoformat()}, now)
    assert updater.due({updater.LAST_KEY: "garbage"}, now)
    assert updater.offered(RELEASE, {}) and not updater.offered(RELEASE, {updater.SKIP_KEY: "0.2.0"})


class Git:
    """Fake git + pip: answers by the command's first words."""

    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, command, **kw):
        self.calls.append(command)
        assert kw["timeout"] and kw["cwd"]
        key = " ".join(command[:2]) if command[0] == "git" else "pip"
        code, out = self.answers.get(key, (0, ""))
        if key == "git rev-parse" and not out:
            out = "abc123\n"
        return subprocess.CompletedProcess(command, code, out, "")


def _root(tmp_path):
    (tmp_path / ".git").mkdir()
    return tmp_path


def test_a_clean_git_copy_moves_fast_forward_to_the_tag_and_installs_its_libraries(tmp_path):
    git = Git()
    out = updater.update(RELEASE, root=_root(tmp_path), python="py", run=git)
    assert out.ok and out.previous == "abc123" and "Updated to 0.2.0" in out.message
    assert git.calls[0] == ["git", "status", "--porcelain", "--untracked-files=no"]
    assert ["git", "fetch", "--tags", "origin"] in git.calls
    assert ["git", "merge", "--ff-only", "refs/tags/v0.2.0"] in git.calls
    assert git.calls[-1][:3] == ["py", "-m", "pip"] and git.calls[-1][-1].endswith("requirements.txt")


def test_a_copy_with_changes_or_commits_of_its_own_is_never_changed(tmp_path):
    git = Git({"git status": (0, " M app/x.py\n")})
    out = updater.update(RELEASE, root=_root(tmp_path), run=git)
    assert not out.ok and "changes of its own" in out.message and "app/x.py" in out.message
    assert all(c[1] not in ("fetch", "merge") for c in git.calls)
    git = Git({"git merge": (128, "fatal: Not possible to fast-forward, aborting.")})
    out = updater.update(RELEASE, root=tmp_path, python="py", run=git)
    assert not out.ok and "left as it was" in out.message and not any(c[0] == "py" for c in git.calls)


def test_no_git_copy_or_a_bad_tag_is_refused(tmp_path):
    out = updater.update(RELEASE, root=tmp_path, run=Git())
    assert not out.ok and "https://x/r" in out.message
    out = updater.update(updater.Release("x", "main; rm", "u"), root=_root(tmp_path), run=Git())
    assert not out.ok and "Not a version tag" in out.message


# screens

def _window(busy=False):
    return SimpleNamespace(tests_page=SimpleNamespace(busy=busy))


def _flow(tmp_path, check_result, busy=False, **kw):
    from app.ui.update_ui import UpdateBar, UpdateFlow, UpdatesTab
    bar, tab = UpdateBar(), UpdatesTab()
    flow = UpdateFlow(None, bar, tab, data_dir=tmp_path, check=lambda beta=False: check_result,
                      start_thread=lambda fn: fn(), warn=lambda *a: None, **kw)
    flow.window = _window(busy)
    return flow, bar, tab


def test_a_newer_version_shows_the_bar_and_skip_hides_it_for_good(qapp, tmp_path):
    flow, bar, tab = _flow(tmp_path, updater.Check(RELEASE, "Version 0.2.0 is out."))
    assert flow.at_start()
    assert not bar.isHidden() and "0.2.0" in bar.label.text() and not tab.update_btn.isHidden()
    assert load_settings(tmp_path)[0][updater.LAST_KEY]
    flow.skip()
    assert bar.isHidden() and load_settings(tmp_path)[0][updater.SKIP_KEY] == "0.2.0"
    assert not flow.at_start()                       # checked today already
    flow.check(asked=False)
    assert bar.isHidden()                            # skipped: not offered again by itself
    flow.check(asked=True)
    assert not bar.isHidden()                        # Check now shows it anyway


def test_no_check_and_no_update_while_tests_run_and_update_only_after_yes(qapp, tmp_path, monkeypatch):
    flow, bar, tab = _flow(tmp_path, updater.Check(RELEASE, "out"), busy=True)
    assert not flow.at_start()
    flow.release = RELEASE
    assert not flow.start_update() and "Tests are running" in tab.status.text()
    monkeypatch.setattr(updater, "git_copy", lambda root=None: True)
    done, relaunched = [], []

    def update(release, progress):
        done.append(release)
        return updater.Outcome(True, "Updated", "abc")
    flow, bar, tab = _flow(tmp_path, updater.Check(RELEASE, "out"), ask=lambda *a: False, update=update)
    flow.release = RELEASE
    assert not flow.start_update() and done == []
    flow, bar, tab = _flow(tmp_path, updater.Check(RELEASE, "out"), ask=lambda *a: True, update=update,
                           relaunch=lambda: relaunched.append(1))
    flow.release = RELEASE
    assert flow.start_update() and done == [RELEASE] and relaunched == [1]
    assert load_settings(tmp_path)[0][updater.PREVIOUS_KEY] == "abc"


def test_beta_versions_are_on_by_default_and_the_check_at_start_includes_them(qapp, tmp_path):
    """Owner, 2026-10-24: every version is a beta for now; the box is ticked until someone unticks it."""
    from app.ui.update_ui import UpdateBar, UpdateFlow, UpdatesTab
    asked = []
    bar, tab = UpdateBar(), UpdatesTab()
    flow = UpdateFlow(None, bar, tab, data_dir=tmp_path, check=lambda beta=False: asked.append(beta) or
                      updater.Check(None, "newest"), start_thread=lambda fn: fn(), warn=lambda *a: None)
    flow.window = _window()
    assert tab.beta.isChecked() and updater.BETA_KEY not in load_settings(tmp_path)[0]
    flow.at_start()
    assert asked == [True]
    tab.beta.setChecked(False)                       # unticked: remembered, the default no longer applies
    _, _, tab2 = _flow(tmp_path, updater.Check(None, "x"))
    assert not tab2.beta.isChecked()
