"""Trust fix 3b (owner, 2026-11-12): before each test, wait while the CPU is above 90 C or the GPU above 80 C, at
most 3 minutes, deciding on the lowest of a few readings."""

import threading

from app.environment import conditions as C


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Stop:
    """An Event whose wait() moves the fake clock instead of sleeping."""
    def __init__(self, clock, stop_at=None):
        self.clock, self.stop_at, self.event = clock, stop_at, threading.Event()

    def wait(self, seconds):
        self.clock.t += seconds
        if self.stop_at is not None and self.clock.t >= self.stop_at:
            self.event.set()
        return self.event.is_set()

    def is_set(self):
        return self.event.is_set()


def reader(clock, cpu_at):
    return lambda: {"cpu_temperature_hottest": (cpu_at(clock.t), "C"), "gpu0_temperature": (60.0, "C")}


def test_no_wait_when_cool_or_no_reading():
    clock = Clock()
    assert C.cool_down(Stop(clock), read=reader(clock, lambda t: 70.0), clock=clock) == ""
    assert C.cool_down(Stop(clock), read=lambda: {}, clock=clock) == ""
    assert clock.t == 0.0                                   # one reading under the limits: no time spent


def test_one_hot_reading_is_not_a_hot_pc():
    clock, said = Clock(), []
    spikes = iter([95.0, 57.0, 56.0])
    assert C.cool_down(Stop(clock), said.append, read=lambda: {"cpu_temperature_hottest": (next(spikes), "C")},
                       clock=clock) == ""
    assert said == []


def test_waits_until_cool_and_says_so_in_the_report():
    clock, said, into = Clock(), [], {"items": []}
    text = C.cool_down(Stop(clock), said.append, read=reader(clock, lambda t: 96.0 if t < 30 else 85.0),
                       clock=clock, into=into)
    assert text.startswith("waited ") and "CPU 96 -> 85 C" in text
    assert any(s.startswith("Cooling: CPU 96 C") for s in said)
    assert into["items"][-1]["name"] == "Cooling wait" and into["items"][-1]["ok"]


def test_gives_up_after_three_minutes_and_goes_on():
    clock, into = Clock(), {}
    text = C.cool_down(Stop(clock), read=reader(clock, lambda t: 95.0), clock=clock, into=into)
    assert "still above 90 C CPU" in text and C.COOL_MAX_SECONDS <= clock.t <= C.COOL_MAX_SECONDS + 10
    assert into["items"][-1]["ok"] is False


def test_stop_or_skip_ends_the_wait():
    clock = Clock()
    assert C.cool_down(Stop(clock, stop_at=20), read=reader(clock, lambda t: 95.0), clock=clock) == ""
    assert clock.t < 30
