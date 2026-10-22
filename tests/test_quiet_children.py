from app import quiet_children as q


def test_commands_start_without_a_window_when_the_app_has_no_console():
    # Windows ARM, 2026-10-22: pythonw.exe; every command of the scan opened a black window
    seen = []

    class FakePopen:
        def __init__(self, args, creationflags=0, **kwargs):
            seen.append(creationflags)
    assert q.hide_console_windows(FakePopen, windows=True, console=lambda: False)
    FakePopen(["powershell"])
    FakePopen(["x"], creationflags=q.CREATE_NEW_CONSOLE)          # asked for its own console: kept
    FakePopen(["y"], creationflags=0x200)                          # other flags stay
    assert seen == [q.CREATE_NO_WINDOW, q.CREATE_NEW_CONSOLE, 0x200 | q.CREATE_NO_WINDOW]
    assert not q.hide_console_windows(FakePopen, windows=True, console=lambda: False)   # only once


def test_nothing_changes_with_a_console_or_off_windows():
    class FakePopen:
        def __init__(self, *a, **k):
            pass
    assert not q.hide_console_windows(FakePopen, windows=True, console=lambda: True)
    assert not q.hide_console_windows(FakePopen, windows=False, console=lambda: False)
    assert not getattr(FakePopen, "_quiet_children", False)
