"""Shared test setup."""

import gc

import pytest


@pytest.fixture(autouse=True)
def _collect_garbage_on_main_thread():
    """Free each test's leftover objects here, on the main thread.

    Test widgets without a parent can sit in reference cycles (for example a panel whose
    replaced `confirm` lambda refers back to it). Left alone, Python's cyclic GC may run
    later inside a QThread worker and delete those QWidgets there, which crashes Qt
    (access violation). Collecting after every test keeps widget deletion on this thread.
    """
    yield
    gc.collect()


@pytest.fixture(autouse=True)
def _no_real_presentmon_search(monkeypatch):
    """The frame panel looks for PresentMon when it is built (Program Files plus a PowerShell
    signature check). Tests must not depend on this computer, so that search finds nothing here;
    tests of the search itself call it with their own folders and a fake signature check."""
    from app.frames import provision
    monkeypatch.setattr(provision, "find_local",
                        lambda *a, **k: provision.Found(error="not searched in tests"))


@pytest.fixture(autouse=True)
def _screen_never_locked(monkeypatch, request):
    """The runner does not start a screen test on a locked screen (app/automation/awake.py). Tests must not depend
    on this computer being locked: here the screen is never locked, and the run asks the system for nothing.
    tests/test_awake.py checks the real functions with fakes."""
    if request.module.__name__.endswith("test_awake"):
        return
    from contextlib import nullcontext
    from app.automation import awake
    monkeypatch.setattr(awake, "screen_locked", lambda platform=None: False)
    monkeypatch.setattr(awake, "keep_awake", lambda *a, **k: nullcontext(False))


@pytest.fixture(autouse=True)
def _no_real_conditions_check(monkeypatch):
    """A plan reads the PC's power and background load before it asks to start. Tests must not depend on this
    computer: nothing is read here; app/environment/conditions.py is tested with fakes."""
    from app.environment import conditions
    monkeypatch.setattr(conditions, "timed_check", lambda **kw: {"read_at": "test", "items": []})


@pytest.fixture(autouse=True)
def _no_real_plan_files(monkeypatch, tmp_path):
    """Every run from the Tests screen saves its plan file (app/experiment.py) in the data folder: in tests it goes
    to the test's own temporary folder, never this PC's data."""
    from app import experiment
    monkeypatch.setattr(experiment, "folder", lambda data_dir: tmp_path / "plans")


@pytest.fixture(autouse=True)
def _no_reports_folder_window(monkeypatch):
    """The first run asks where the reports go (a folder window). In tests nothing may wait for a click: the window
    is answered with Cancel (the reports stay in the default folder); tests of the question pass their own answer."""
    from app.ui import reports_folder
    monkeypatch.setattr(reports_folder, "pick", lambda *a, **k: "")


@pytest.fixture(autouse=True)
def _not_elevated(monkeypatch):
    """Whether pytest runs as administrator must not change any answer: the app is taken as not elevated;
    tests of the elevated case say so themselves."""
    from app import elevation
    monkeypatch.setattr(elevation, "is_elevated", lambda platform=None: False)


@pytest.fixture(autouse=True)
def _no_real_telemetry(monkeypatch):
    """Runs started from the panels record telemetry by default (nvidia-smi, psutil, a local
    LibreHardwareMonitor server). Tests must not depend on this computer, so no source is used
    here; the sources are tested with fakes in test_telemetry_sources.py."""
    from app.telemetry import sources
    monkeypatch.setattr(sources, "default_sources", lambda platform_name=None: [])


@pytest.fixture(autouse=True)
def _no_real_tool_setup(request, monkeypatch):
    """Starting a run checks for PresentMon and LibreHardwareMonitor and offers downloads. In tests
    LibreHardwareMonitor counts as already running (nothing asked, nothing started) and no release
    list is read from GitHub; tests of those steps pass their own fakes. The tests of these
    functions themselves (test_lhm_provision.py) get the real ones, which they call with fakes."""
    if request.node.module.__name__.endswith("test_lhm_provision"):
        return
    from app.frames import provision
    from app.telemetry import lhm_provision
    monkeypatch.setattr(lhm_provision, "is_running", lambda *a, **k: True)
    monkeypatch.setattr(lhm_provision, "plan_download",
                        lambda *a, **k: lhm_provision.LhmAsset(error="no network in tests"))
    monkeypatch.setattr(provision, "plan_download",
                        lambda *a, **k: provision.AssetChoice(error="no network in tests"))


@pytest.fixture(autouse=True)
def _no_real_tools_folder(monkeypatch):
    """The scan also looks in the app's own tools folder (tools installed from a vendor zip). Tests must not depend
    on what this computer has installed there, so that folder is not scanned in tests."""
    from app import benchmark_manager
    monkeypatch.setattr(benchmark_manager, "app_tools_roots", lambda: [])


@pytest.fixture(autouse=True)
def _not_a_remote_session(monkeypatch):
    """Game captures in a Remote Desktop session are stored as invalid. Tests must not depend on how this computer
    is reached, so it is never a remote session here; the test of that case passes its own check."""
    from app.frames import game_benchmark
    monkeypatch.setattr(game_benchmark, "is_remote_session", lambda: False)
