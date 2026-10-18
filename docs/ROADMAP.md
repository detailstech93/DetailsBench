# Roadmap: from personal tool to a public release

Written 2026-10-18 after a code review of Phases 1-13 (930 tests passing) and a look at well-rated tools.
Goal: a build the channel's viewers can download, run on their own PC, and compare with the numbers shown in
the videos, without the tool ever showing a number it did not measure.

## What the well-rated tools do, and what we take from each

| Tool | Known for | What we take |
|---|---|---|
| 3DMark (UL) | One headline score that reviews quote; online comparison with the same hardware | A single summary score per category, and "compare with the same CPU/GPU" |
| CapFrameX (free, PresentMon based) | Frame-time graph, 1% / 0.1% lows, run comparison, FPS-threshold chart | Frame-time graph and threshold chart in the report (we already have the lows) |
| NVIDIA FrameView / PresentMon | GPU Busy, PC latency | Read the extra PresentMon columns when present |
| OCCT | Stability and thermals under load, clear pass/fail | A stability test with temperatures and throttling flagged |
| Cinebench / Geekbench | Short, repeatable CPU score, single and multi core | A fast "quick check" run under 5 minutes |
| PassMark PerformanceTest | All categories in one run, large results database | A "full system" preset |
| Phoronix Test Suite / OpenBenchmarking.org | Test profiles as files, results kept local unless you upload, side-by-side comparisons | Our tool specs and scripts are already files; add opt-in sharing and a comparison page |
| CrystalDiskMark | Standard SEQ1M / RND4K presets | Already matched by the DiskSpd / fio presets |

Rankings differ between sites and many lists carry affiliate notes. The table lists features, not a ranking.

## Review findings (2026-10-18)

Fixed in this review:
- `dxdiag` wrote its report into the current folder, which is the program folder in the packaged app. It now writes to a temporary folder.
- Removed unused imports and one unused variable (pyflakes is clean apart from intentional re-exports).

Checked, no problem found:
- No `shell=True`.
- Every `subprocess.run` has a timeout.
- SQL built with f-strings uses only constant clauses (values are passed as parameters).
- New database columns have `DEFAULT 0`.

Open, for the phases below:
- `BenchmarkManager.run_benchmark` and its `_run_d3dmark` / `_run_glmark2` are not called anywhere. `_run_d3dmark` passes guessed 3DMark arguments, so remove it or replace it with a verified tool spec (Phase A).
- `app/ui/main_window.py` (1,590 lines) and `app/benchmark_manager.py` (1,574 lines) are the largest files. Split them only when a phase changes them.
- Tests: there are many unused imports in `tests/` (cosmetic).
- The Cyberpunk 2077 script is unverified (it needs a supervised run).
- No run yet on a second PC.

## Phases

Each phase ends the same way as before: tests and real checks with raw output, docs updated, commit and push.

### Phase A: Release hardening (needed before anyone else runs it)
- Remove the dead `run_benchmark` path (guessed 3DMark arguments).
- First-run wizard:
  - where the data is stored;
  - which tools were found;
  - what can be downloaded from official links with a published hash.
- Crash log written to the data folder, and a "copy diagnostics" button that redacts the computer name, user name and IDs.
- Versioned release build: version number in the window title, the report and `build_info.json`; checksums file next to the zip.
- Test on a clean Windows VM and on a second PC (one with AMD and one with NVIDIA if possible), and record the results in AUTOMATION_MATRIX.md.
- Done when a viewer can unzip, run, do one CPU test and get a report on a PC that never had Python.

### Phase B: Quick check and full system presets
- "Quick check" (< 5 min):
  - 7-Zip;
  - one storage preset;
  - one short CPU render or compute test.
- "Full system" (about 30 min): CPU, storage, AI (when a local model server is running), and optionally a game with frame capture.
- A summary score per category, shown next to the raw numbers, never instead of them. The formula is documented, and a category with no valid run has no score.

### Phase C: New tests (one per step, each with a tool spec or script, a parser test and a real check)
- CPU:
  - Cinebench 2024 (command-line mode, if its license allows automated runs);
  - y-cruncher (multi-threaded, has a documented benchmark mode);
  - Blender Benchmark CLI (official, open data format).
- GPU:
  - Unigine Superposition (command-line preset);
  - Blender Benchmark GPU;
  - built-in game benchmarks with frame capture (Shadow of the Tomb Raider and Rise of the Tomb Raider are already installed).
- Memory: a bandwidth / latency test (Intel MLC or an open alternative) for RAM tuning videos.
- Stability and thermals: a 10-30 min sustained load that records clocks, temperatures and power through LibreHardwareMonitor, and flags throttling and score drift between the first and last minutes.
- AI: more llama-benchy models, an image-generation timing (if a local server exposes one), and the MLPerf Client scenarios.
- Each test is shown as "verified on this machine" or "not verified" until it has a real run.

### Phase D: Results viewers can understand
- Report:
  - a frame-time graph and FPS-threshold chart (as CapFrameX does);
  - CPU / GPU temperature and clock graphs over the run;
  - a comparison with the reference system from the video.
- "Compare with my channel's PC": the build ships with the reference results of the PC shown in the videos, as a JSON file with its manifest. The viewer sees the percentage difference per test, with a warning when the settings or versions differ.
- An export image (PNG card) of the summary for sharing in comments or on social media.

### Phase E: Opt-in sharing (only after A-D are stable)
- Results stay local by default, as with Phoronix / OpenBenchmarking.org.
- Optional upload of the export file: no computer name, no user name, no serials, no SteamID; the viewer sees exactly what is sent before sending.
- A simple leaderboard per CPU / GPU from shared results, with invalid and test runs excluded.
- This needs a server and a privacy statement, so decide on it separately.

### Phase F: Publishing
- Arabic and English interface (the channel's audience).
- Code signing for the release build to reduce SmartScreen warnings. It needs a certificate, which is a cost decision.
- A download page with: what the tool does, what it never does (no system changes without asking, no data sent), the checksums, and known limits.
- A short video script: install, Quick check, read the report, compare with the channel PC.

## Order and rough size

| Phase | Size | Depends on |
|---|---|---|
| A Release hardening | 2-3 sessions | - |
| B Presets and summary scores | 1-2 sessions | A |
| C New tests | 1 session per test | A |
| D Results for viewers | 2 sessions | B |
| E Opt-in sharing | 3+ sessions, plus a server | D, a decision |
| F Publishing | 1-2 sessions | A, B, D |

A minimum public release is A, B, two or three tests from C, and the report part of D.

Sources for the tool comparison: helpdeskgeek.com (free benchmark tools 2026), gamingpcguru.com (GPU benchmark tool per job,
Oct 2026), github.com/CXWorld/CapFrameX (README and releases), phoronix.com (OpenBenchmarking comparison news).
