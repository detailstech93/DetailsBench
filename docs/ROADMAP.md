# Roadmap

Updated 2026-10-18 after the code review of Phases 1-13 (930 tests passing) and the owner's decisions:
- No comparison with a reference "channel PC" for now.
- No online sharing or upload.
- Remote start from a second device on the local network.
- Charts in the reports stay.
- **The new user interface comes first.**

The order of work:
1. Phase A: new UI.
2. Phase B: supervised Cyberpunk 2077 run.
3. Phase C: full test of the app, also on a second PC.
4. Then remote control, new tests and the release build.

## Review findings (2026-10-18)

Fixed in commit d1a6210:
- `dxdiag` wrote its report into the current folder (the program folder in the packaged app). It now uses a temporary folder.
- Unused imports and variables removed. pyflakes is clean apart from intentional re-exports.

Checked, no problem found:
- No `shell=True`.
- Every `subprocess.run` has a timeout.
- SQL built with f-strings uses only constant clauses.
- New database columns have `DEFAULT 0`.

Open:
- `BenchmarkManager.run_benchmark` / `_run_d3dmark` / `_run_glmark2` are never called. `_run_d3dmark` passes guessed 3DMark arguments. Removed in Phase A.
- `main_window.py` and `benchmark_manager.py` are about 1,600 lines each. Phase A moves the new screens into their own files instead of growing them.
- The Cyberpunk 2077 script is unverified (Phase B).
- No run on a second PC yet (Phase C).

## What well-rated tools do that we keep in mind

| Tool | Idea we use |
|---|---|
| 3DMark | One clear result per test; "what can this PC run" before anything else |
| CapFrameX | Frame-time graph, 1% / 0.1% lows, FPS-threshold chart, run comparison (charts stay) |
| PassMark PerformanceTest | Run every test, or a chosen group, in one go |
| OCCT | Sustained load with temperatures and throttling shown |
| Phoronix Test Suite | Tests described as files; results stay on the machine; remote start of runs (Phoromatic) |

## Phase A: New user interface (first)

### Goal

Open the app and see this PC. One click shows every test this PC can run. Tick tests, choose how many times, run, get a report.
The current tabs (History, Compare, Steam, frame capture details, profiles) remain under **Advanced**, so nothing is lost.

### Screen 1: This PC (shown at start)

- **Cards:**
  - CPU: name, cores and threads.
  - Memory: GB.
  - GPU(s).
  - Operating system: name, version, build.
  - Architecture: x64 / arm64, and whether the app runs emulated.
  - Virtual machine yes/no.
  - Graphics APIs: DirectX / Vulkan / OpenGL.
- Each value shows its state (detected / not certain / not available), as the Environment Profile already does. Nothing is guessed.
- The specs appear at once from the fast part of the scan. The tool scan runs in the background with a progress line.
- Main button: **Choose tests**. Also: **Copy specs** (text, with the computer name left out).

### Screen 2: Tests

- **One list built from every source we already have:**
  - tool specs (`data/tools`);
  - storage tools;
  - AI tools;
  - simulation scripts (`data/scripts`);
  - Steam built-in benchmarks;
  - the older library entries (Geekbench, Cinebench, 3DMark, web tests).
- **Grouped:** CPU, GPU / Games, Storage, AI, Web, Other.
- **Only compatible tests are shown** (OS family and native architecture of this PC). A "Show tests this PC cannot run" switch lists the rest with the reason ("Windows only", "no arm64 build").
- **Every row:**
  - tick box;
  - name;
  - an installed mark (✔ installed, ⬇ not installed, ⚠ found but not usable, with the reason);
  - how it runs: **Automatic**, **Assisted** (the app starts it, you press start in the program) or **Manual** (you type the score);
  - verified on a real run yes/no;
  - a rough duration when known.
- **Clicking a test:**
  - **Installed** → asks **how many times** (repetitions, warm-up runs, from the selected profile by default) → shows the summary → runs → shows the report.
  - **Not installed** → **Open official download page** (the official URL kept with the test) and **I installed it, check again**. The app downloads nothing on its own here.
- **Several tests at once:**
  - tick any group (**Select all installed**, or **Select group**);
  - one repetitions count for all, with an optional count per test;
  - one plan, one session, one report.
- **Assisted and manual tests in a plan:** the plan stops at that test and asks you (start the program / type the score), then continues. Automatic tests never wait for you.

### Running and the report

- Progress screen:
  - current test;
  - run x of y;
  - time;
  - the existing emergency stop (button and Ctrl+Shift+X).
- The plan journal, resume after a crash and cool-down stay as they are.
- At the end the HTML report opens. It shows:
  - the results table (mean, CV, every run);
  - **charts**: a bar per run, frame-time and FPS-threshold charts for games, temperatures and clocks over the run when telemetry was recorded;
  - the PC specs.
- The charts are drawn as inline SVG inside the HTML file: no scripts, no internet, still one file.

### Data needed (additive, nothing removed)

- A catalog entry per test, with these fields:
  - id, name, group;
  - how it runs;
  - supported OS and architectures;
  - official URL;
  - verified;
  - rough duration;
  - availability (installed / missing / unusable, with the reason).
- New optional fields in tool specs and scripts: `category`, `official_url`, `supported_os`, `supported_architectures`, `expected_minutes`.
- Existing files stay valid without them.

### Steps (each one tested, committed and pushed)

1. **Test catalog** (no UI): one function that lists every test with the fields above, from all sources, filtered by this PC. Unit tests with fake sources.
2. **This PC screen** at start, the old tabs under Advanced.
3. **Tests screen:** list, groups, marks, compatible filter, official download link, "check again".
4. **Run dialog:** repetitions for one test or for a selection / all, then the plan in the existing runner. Assisted and manual tests are asked in order.
5. **Report with charts** (inline SVG) opened at the end.
6. Remove the dead `run_benchmark` path.
7. Real check on this PC: 7-Zip ×3 plus a storage test in one plan, report opened; screenshots kept for the docs.

## Phase B: Cyberpunk 2077 supervised run

- Run the script with the owner present: Steam launch, benchmark, result file.
- Fix the result keys from the real `summary.json`, mark the script verified, and add frame capture.

## Phase C: Full test and second PC

- Every screen and every installed test on this PC, with a written checklist and its results in `AUTOMATION_MATRIX.md`.
- The packaged build on a second PC that never had Python (and an AMD or NVIDIA difference if possible). Fix what breaks.

## Phase D: Remote start from a second device (local network only)

- **Off by default.** When switched on in the app, it starts a small web server inside the app.
  It listens on this PC's local network address on a chosen port, so a phone or laptop on the same network can open it.
  `localhost` alone would only be reachable from the same PC.
- **What the browser page can do:**
  - see the specs and the test list;
  - tick tests and set repetitions;
  - start a plan;
  - watch progress;
  - press emergency stop;
  - open the report.
- **What it cannot do:** run anything that is not in the test list, or send commands or files.
- **Pairing:**
  - The app shows the address and a one-time code (also as a QR code).
  - The page works only with that code.
  - The code changes every time the server is switched on.
- **Security notes:**
  - No internet access is needed or used.
  - Windows may ask to allow the app through the firewall. The owner answers that prompt; the app never changes firewall rules itself.
  - A "this PC only" option binds to 127.0.0.1.
- **Checks:**
  - unit tests for the code check and the allowed actions;
  - a real run started from a phone on the same network.

## Phase E: New tests (one per step: spec or script, parser test, real run)

- CPU: y-cruncher, Blender Benchmark CLI, Cinebench 2024 if its license allows automated runs.
- GPU / games: Unigine Superposition preset, the Tomb Raider built-in benchmarks with frame capture (already installed here).
- Memory: a bandwidth / latency test.
- Stability: a 10-30 min sustained load with temperatures, clocks and power (LibreHardwareMonitor). It flags throttling and the drift between the first and last minutes.
- AI: more llama-benchy models, and the MLPerf Client scenarios.

## Phase F: Release build (local download, no online service)

- Version number in the window, the report and `build_info.json`; a checksums file next to the zip.
- First-run notes:
  - where data is stored;
  - which tools were found;
  - official download links.
- A crash log and a "copy diagnostics" button that leaves out the computer name, user name and IDs.
- Arabic and English interface.
- Code signing only if a certificate is bought (cost decision).

Sources for the tool comparison:
- helpdeskgeek.com, free benchmark tools 2026;
- gamingpcguru.com, GPU benchmark tool per job, October 2026;
- github.com/CXWorld/CapFrameX, README and releases;
- phoronix.com, OpenBenchmarking comparisons.
