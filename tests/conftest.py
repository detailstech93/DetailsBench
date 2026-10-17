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
