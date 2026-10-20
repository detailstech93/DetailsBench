"""Speedometer 3.1 and JetStream 3.0 as automated workloads (owner, 2026-10-20), through app/web/cdp.py.

Each run: a new empty browser profile, the official page with ?startAutomatically (both pages read it: Speedometer
3.1 resources/shared/params.mjs and main.mjs, JetStream 3.0 utils/params.js, read on browserbench.org 2026-10-20),
then the score the page shows when it has finished:
- Speedometer 3.1: when benchmarkClient has results (main.mjs: _hasResults), #result-number holds the score and
  #summary the class "invalid" after an error (it is "valid" from the start, seen in Chrome 150 on 2026-10-20);
  its default 10 iterations, the official score.
- JetStream 3.0: #result-summary gets the class "done" and its .score the overall score (JetStreamDriver.js); a
  loading error turns #status into "error".
The browser window must be visible and the screen unlocked (needs_desktop): a hidden page is slowed down by the
browser. The browser's name and version (DevTools /json/version) are kept in the run's note: scores compare only
with the same browser and version. Nothing is uploaded by these pages.
"""

import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Tuple

from app.automation.workload import RunContext, Workload, WorkloadResult
from app.web import cdp

def _page(test_id: str, title: str) -> dict:
    """The current page of the benchmark (data/benchmark_definitions.py is the one place for its address)."""
    from data.benchmark_definitions import DEFINITIONS_BY_ID, get_default_page_version
    page = get_default_page_version(DEFINITIONS_BY_ID[test_id]) or {}
    version = str(page.get("version") or "")
    return {"id": test_id, "name": f"{title} {version}".strip(), "version": version or None,
            "url": str(page.get("url") or "") + "?startAutomatically"}


SPEEDOMETER = {
    **_page("speedometer", "Speedometer"), "unit": "score",
    "expected": 240, "timeout": 1200,
    # None while it runs; then "ERR:<text>" or the score's text
    # #summary is "valid" from the start (index.html): the run is over when benchmarkClient (main.mjs) has results
    "read": ("(() => { const c = globalThis.benchmarkClient; if (!c || !c._hasResults) return null;"
             " const s = document.getElementById('summary');"
             " if (s && s.className === 'invalid') return 'ERR:the page reported an error';"
             " return (document.getElementById('result-number') || {}).textContent || null; })()"),
}
JETSTREAM = {
    **_page("jetstream", "JetStream"), "unit": "points",
    "expected": 420, "timeout": 1800,
    "read": ("(() => { const st = document.getElementById('status');"
             " if (st && st.classList.contains('error')) return 'ERR:' + st.textContent.trim().slice(0, 200);"
             " const r = document.getElementById('result-summary');"
             " if (!r || !r.classList.contains('done')) return null;"
             " const s = r.querySelector('.score'); return s ? s.textContent : null; })()"),
}


def _quiet(fn: Callable):
    """fn's value, or None while the browser is not ready (no answer yet, the page still loading)."""
    def call():
        try:
            return fn()
        except ConnectionError:         # the browser was closed: no use waiting
            raise
        except (OSError, ValueError, RuntimeError):
            return None
    return call


def parse_score(text) -> Tuple[Optional[float], Optional[str]]:
    if not isinstance(text, str) or not text.strip():
        return None, "no score on the page"
    if text.startswith("ERR:"):
        return None, text[4:].strip() or "the page reported an error"
    try:
        value = float(text.strip().replace(",", ""))
    except ValueError:
        return None, f"the score is not a number: {text.strip()[:40]!r}"
    return (value, None) if value > 0 else (None, f"score {value:g} is not above 0")


class WebBenchmarkWorkload(Workload):
    benchmark_type = "web"
    needs_desktop = True            # a hidden or locked page is slowed down
    warning = ("Opens the official page in a new browser window with an empty profile of its own (not your "
               "browser's profile); leave the window in front and the PC alone while it runs.")

    def __init__(self, spec: dict, browser: Optional[str] = None, finder: Callable = cdp.find_browser,
                 popen=None, connect=None, opener=None, clock=time.monotonic):
        self.spec = None            # not a tool spec (app/ui/automation_panel.py reads attributes then)
        self.page = spec
        self.id, self.display_name = spec["id"], spec["name"]
        self.timeout_seconds = spec["timeout"]
        self.verified = False       # not seen on a real run here yet
        self.browser = browser if browser is not None else finder()
        self._popen, self._connect, self._opener, self.clock = popen, connect, opener, clock
        self._started: Optional[float] = None

    @property
    def progress(self):
        if self._started is None:
            return None
        gone = self.clock() - self._started
        return min(0.97, gone / self.page["expected"]), max(0.0, self.page["expected"] - gone)

    def availability(self) -> Tuple[bool, str]:
        if not self.browser:
            return False, "browser not installed: no Chrome, Edge or Chromium found (the web tests use one of them)"
        return True, self.browser

    def executable(self) -> Tuple[Optional[str], str]:
        return self.browser, "Chrome, Edge or Chromium"

    def version(self) -> Optional[str]:
        return self.page["version"]

    def describe_command(self) -> Optional[str]:
        if not self.browser:
            return None
        return f'"{self.browser}" (new empty profile) {self.page["url"]}'

    def run(self, ctx: RunContext) -> WorkloadResult:
        if not self.browser:
            return WorkloadResult(status="skipped", error=self.availability()[1])
        import subprocess
        popen = self._popen or subprocess.Popen
        stop = ctx.stop_event or threading.Event()
        timeout = float(ctx.timeout_seconds or self.timeout_seconds)
        profile = Path(tempfile.mkdtemp(prefix="pcbm-browser-"))
        command = cdp.browser_command(self.browser, profile, self.page["url"])
        shown = [self.browser, "(new empty profile)", self.page["url"]]
        started = self.clock()
        self._started = started
        proc = ws = None
        try:
            try:
                proc = popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            except OSError as e:
                return WorkloadResult(status="failed", error=f"could not start: {e}", command=shown)
            port = cdp.wait_for(lambda: cdp.read_active_port(profile), 60, stop)
            if port is None:
                return self._end(stop, "the browser did not open its DevTools port within 60 s", shown, started)
            opener = self._opener or urllib.request.urlopen
            browser_name = ""
            try:
                browser_name = str(cdp.http_json(port, "/json/version", opener).get("Browser") or "")
            except (OSError, ValueError):
                pass
            url = cdp.wait_for(_quiet(lambda: cdp.page_socket_url(cdp.http_json(port, "/json/list", opener))), 30,
                               stop)
            if url is None:
                return self._end(stop, "the browser showed no page", shown, started)
            ws = cdp.WebSocket(url, **({"connect": self._connect} if self._connect else {}))
            page = cdp.Page(ws)
            left = max(1.0, timeout - (self.clock() - started))
            text = cdp.wait_for(_quiet(lambda: page.evaluate(self.page["read"])), left, stop, every=2.0)
            if stop.is_set():
                return WorkloadResult(status="cancelled", error="stopped by user", command=shown,
                                      duration_seconds=round(self.clock() - started, 3))
            if text is None:
                return WorkloadResult(status="timed_out", error=f"no score within {timeout:g} s", command=shown,
                                      duration_seconds=round(self.clock() - started, 3))
            score, why = parse_score(text)
            duration = round(self.clock() - started, 3)
            if score is None:
                return WorkloadResult(status="invalid", error=why, command=shown, duration_seconds=duration)
            note = (f"Browser: {browser_name or 'unknown'}; Page: {self.page['url']}; new empty profile; "
                    "the page's default length")
            return WorkloadResult(status="completed", score=score, unit=self.page["unit"], note=note,
                                  metrics={}, command=shown, duration_seconds=duration)
        except (OSError, ValueError, RuntimeError, ConnectionError) as e:
            return WorkloadResult(status="failed", error=f"browser connection: {e}", command=shown,
                                  duration_seconds=round(self.clock() - started, 3))
        finally:
            self._started = None
            if ws is not None:
                ws.close()
            if proc is not None:
                cdp.end_browser(proc)
            cdp.remove_profile(profile)

    def _end(self, stop, why: str, shown, started) -> WorkloadResult:
        status = "cancelled" if stop.is_set() else "failed"
        return WorkloadResult(status=status, error="stopped by user" if stop.is_set() else why, command=shown,
                              duration_seconds=round(self.clock() - started, 3))


def web_workloads() -> list:
    return [WebBenchmarkWorkload(SPEEDOMETER), WebBenchmarkWorkload(JETSTREAM)]
