"""run_elevated with a fake Windows API (no UAC prompt, no real process)."""

import threading

from app.frames.elevated import ERROR_CANCELLED, run_elevated


class FakeApi:
    def __init__(self, start_error=None, finishes_after=None, exit_code=0, can_terminate=True):
        self.start_error, self.finishes_after = start_error, finishes_after
        self.code, self.can_terminate = exit_code, can_terminate
        self.waits, self.calls = 0, []

    def shell_execute(self, file, params, cwd, show):
        self.calls.append(("start", file, params, cwd, show))
        return (None, self.start_error) if self.start_error is not None else ("H", None)

    def wait(self, handle, ms):
        self.waits += 1
        return self.finishes_after is not None and self.waits >= self.finishes_after

    def exit_code(self, handle):
        return self.code

    def terminate(self, handle):
        self.calls.append(("terminate",))
        if self.can_terminate:
            self.finishes_after, self.code = 0, 1
            return True, None
        return False, 5

    def close(self, handle):
        self.calls.append(("close", handle))


def test_exit_code_and_quoted_params():
    api = FakeApi(finishes_after=3, exit_code=0)
    out = run_elevated("C:/pm.exe", ["--output_file", "C:/a b/x.csv"], "C:/w", 10, api=api, poll_seconds=0)
    assert (out.status, out.exit_code, out.terminated) == ("exited", 0, None)
    assert api.calls[0] == ("start", "C:/pm.exe", '--output_file "C:/a b/x.csv"', "C:/w", 7)
    assert api.calls[-1] == ("close", "H")


def test_uac_declined_is_neutral():
    out = run_elevated("pm.exe", [], None, 10, api=FakeApi(start_error=ERROR_CANCELLED))
    assert out.status == "uac_declined" and out.exit_code is None


def test_other_start_error():
    out = run_elevated("pm.exe", [], None, 10, api=FakeApi(start_error=2))
    assert out.status == "start_failed" and "winerror 2" in out.error


def test_timeout_terminates_only_our_handle():
    api = FakeApi()
    out = run_elevated("pm.exe", [], None, 0, api=api, poll_seconds=0)
    assert (out.status, out.terminated, out.exit_code) == ("timed_out", True, 1)
    assert [c[0] for c in api.calls] == ["start", "terminate", "close"]


def test_stop_when_terminate_is_denied():
    stop = threading.Event()
    stop.set()
    api = FakeApi(can_terminate=False)
    out = run_elevated("pm.exe", [], None, 60, stop_event=stop, api=api, poll_seconds=0)
    assert (out.status, out.terminated, out.exit_code) == ("stopped", False, None)
    assert "winerror 5" in out.error and api.calls[-1] == ("close", "H")
