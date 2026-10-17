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
