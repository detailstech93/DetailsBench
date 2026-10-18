import sys
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from app.catalog import INSTALLED, TestEntry
from app.ui.long_question import LongQuestion, screen_room
from app.ui.run_tests import RunTestsDialog


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_a_long_question_fits_the_screen_and_its_buttons_answer(qapp):
    text = "\n".join(f"line {i}" for i in range(400))
    q = LongQuestion(None, "Run", text, [("Yes", True), ("No", False)], default=1)
    assert q.height() <= screen_room(None) and q.buttons[1].isDefault()
    q.buttons[0].click()
    assert q.value is True
    short = LongQuestion(None, "Run", "one line", [("Install", "install"), ("Cancel", None)])
    assert short.height() < 300
    short.buttons[1].click()
    assert short.value is None


def test_the_run_dialog_scrolls_instead_of_hiding_its_buttons(qapp):
    entries = [TestEntry(id=f"t{i}", name=f"Test {i}", group="cpu", mode="automatic", state=INSTALLED,
                         state_reason="", compatible=True, compatibility_reason="", verified=True)
               for i in range(60)]
    profile = SimpleNamespace(repetitions=1, warmup_runs=0, cooldown_seconds=0)
    d = RunTestsDialog(entries, profile, entries[:30])
    assert d.height() <= screen_room(None) and d.scroll.widget() is not None
