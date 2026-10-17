# PC Benchmark Manager

A Windows desktop application for discovering, managing, and running CPU, GPU, and Web benchmarks with session tracking and reporting.

## Features (Phase 2 - Benchmark Detection & Execution)

Verification status below is set after running `python -m pytest tests -v` and `python -m compileall app tests` in the project `.venv`. Evidence sources are listed per feature.

### Elevation-Aware Launch (UAC Support)
Verified manually by the user on this machine, 2026-10-16: launches of Geekbench 6 and 7 with UAC prompts worked correctly. Pytest mocks (`test_winerror_740_triggers_elevated_path`, `test_elevated_launch_winerror_1223_is_cancelled`, etc.) cover all OSError paths.
- Executables that request administrator rights (e.g. Geekbench 6/7) are launched via a generic elevation fallback: normal launch is tried first; if WinError 740 occurs, `os.startfile(path, "runas")` is used to trigger a Windows UAC prompt.
- The app itself remains non-elevated. Only the launched benchmark runs elevated when needed.
- If the user declines the UAC prompt, the status is reported as `cancelled` (neutral, not an error).
- The result includes `elevation_used: True/False` as an observed property.
- Process confirmation uses snapshot-based comparison: a before-snapshot of running processes is taken, the launch occurs, then an after-snapshot verifies a NEW matching process appeared (full image-name match, not substring).
- **UI automation of an elevated window from a non-elevated process is expected to be blocked** (not verified yet). Future automation phases must account for this.

### Dynamic Benchmark Discovery (Merged Methods)
Real-machine check: `python scripts/verify_real_scan.py` (run manually; checks machine-independent invariants and prints the scan for comparison). Pytest channel-boundary tests (`TestChannelBoundaryMocks`) feed fake registry/filesystem data into real channel functions.
No hardcoded installation paths or filenames. Discoveries are dynamically merged from:
- **Registry Uninstall keys**: HKLM (64-bit), HKLM (WOW6432Node), and HKCU.
- **Filesystem pattern search**: Scanning installation folders in both `C:\Program Files` and `C:\Program Files (x86)` (and other drive roots discovered via registry hints).
- **App Paths registry key**: HKLM and HKCU.
- **Start Menu shortcuts**: Reading `.lnk` targets in `%APPDATA%` and `%PROGRAMDATA%` Start Menu directories, and resolving `Get-StartApps` AppIDs.
- **Microsoft Store (Appx) packages**: Queried dynamically via PowerShell `Get-AppxPackage` with timeouts (no inspection of protected `WindowsApps` directories).

### Separate Geekbench 6 and Geekbench 7 Entries
Verified on this machine: real scan detected both; versions and architectures match PE metadata. Pytest tests (`TestLaunchGuard`) prove helper binaries are rejected.
- **geekbench_6**: Main GUI executable identified by pattern (`Geekbench 6.exe`), version `6.7.1.0`, architecture `x64`.
- **geekbench_7**: Main GUI executable identified by pattern (`Geekbench 7.exe`), version `7.1.0.0`, architecture `x64`.
- **Static Version & Architecture Inspection**: Executable architecture is determined from the PE header Machine field (`0x8664` -> `x64`), never from folder names. Version numbers are extracted from PE resource `VersionInfo` metadata and normalized (`"6,7,1,0"` -> `"6.7.1.0"`).
- **Zero-Execution Safety**: Benchmark binaries are **NEVER** executed to probe version or help. Only the main GUI executable is launched, and only upon explicit user confirmation with no arguments. Helper binaries (`geekbench_x86_64.exe`, `geekbench_avx2.exe`, `geekbench6.exe`, `geekbench7.exe`) are never launched.
- **Classification**: `READY_MANUAL` (assisted/manual). Automation/CLI requires a Pro license; license state is unknown ("License: not verified").

### Cinebench Microsoft Store Package Detection
Verified on this machine: Appx package detected; version and architecture read from metadata.
- Detected via Appx package metadata (`MAXONComputerGmbH.Cinebench`).
- Read Version `2026.1.3.0` and Architecture `X64`.
- Executable path is `None` (protected `WindowsApps` folder is not inspected).
- **Assisted Launching**: Uses Start Menu AppID `MAXONComputerGmbH.Cinebench_rsne5bsk8s7tj!Cinebench` (`shell:AppsFolder\<AppID>`) only after explicit user confirmation in the UI. Polls process state to verify launch.
- **Classification**: `READY_MANUAL` ("launch it from the Start menu").

### Additive Database Self-Migration
Pytest `test_migration_from_old_schema` and `test_existing_database_compatibility` verify additive migration.
- SQLite database schema supports storing benchmark versions alongside session results.
- Automatic column migration (`ALTER TABLE results ADD COLUMN benchmark_version TEXT`) on database initialization ensures old database files remain readable with zero data loss.

### Responsive UI with Background Worker
Pytest `test_worker_class_structure`, `test_start_scan_scanning_state`, and `test_library_screen_consolidates_geekbench` verify worker threading and UI state. Launch operations run in `LaunchWorker` (QThread) — the GUI thread never blocks on launch, UAC wait, or process polling.
- **Dedicated Worker Class**: `BenchmarkWorker` is a standalone `QThread` subclass with Qt signals (`scan_started`, `scan_finished`, `scan_error`, `progress`).
- **Single Shared Manager**: `MainWindow` maintains one shared `BenchmarkManager` instance.
- **Scanning State**: Status indicator shows progress; "Rescan Benchmarks" button disables into "Scanning..." state during evaluation.
- **Automatic Refresh**: Library and Run screens refresh immediately when the background scan completes.

---

### Generalized Geekbench Discovery & Score Comparability
Pytest channel-boundary and comparability tests (`TestComparability`, `TestLaunchGuard`) verify behavior.
- **Dynamic Pattern Matching**: Discovers any major version matching `^Geekbench (\d+)$` (e.g. 6, 7, 8) via executable name pattern `Geekbench <N>.exe` and `ProductName` metadata, creating entry `geekbench_<N>`.
- **Product Filtering**: Strictly excludes other Primate Labs products such as "Geekbench AI" or "Geekbench ML".
- **Newly Seen Major Handling**: Any major version not in `KNOWN_GEEKBENCH_MAJORS = {4, 5, 6, 7}` is flagged as `"newly seen version: score comparability with other majors unknown"`.
- **Cross-Major Score Isolation**: Cross-major score comparisons are blocked by default via `can_compare_geekbench_scores()` and `assert_can_compare_benchmarks()`.
- **Multiple Installs of Same Major**: If multiple installs of the same major version exist, the highest version is reported as primary and other installs are recorded in `additional_installs`.
- **Consolidated Library Card**: The Library screen displays all detected Geekbench majors grouped in a single "Geekbench" card/row with each major's status, version, architecture, and path.

---

## Benchmark Capability Matrix

| Benchmark | Detection Method | Status on This Machine | Execution Method | Version | Architecture | Reason / Notes |
|-----------|------------------|------------------------|------------------|---------|--------------|----------------|
| **geekbench_6** | Registry, Filesystem, Start Menu | `READY_MANUAL` | Assisted / Manual | 6.7.1.0 | x64 | Windows GUI version requires manual execution; automation/CLI requires a Pro license; not verified for this version; License: not verified |
| **geekbench_7** | Registry, Filesystem, Start Menu | `READY_MANUAL` | Assisted / Manual | 7.1.0.0 | x64 | Windows GUI version requires manual execution; automation/CLI requires a Pro license; not verified for this version; License: not verified |
| **cinebench** | Appx package, Start Menu | `READY_MANUAL` | Assisted / Manual | 2026.1.3.0 | X64 | Store package version 2026.1.3.0, architecture X64; launch it from the Start menu |
| **d3dmark** | Registry, Filesystem, Start Menu | `NOT_INSTALLED` | N/A | None | None | 3DMark not found on this system |
| **glmark2** | WSL check | `UNSUPPORTED` (shown as "Unsupported on this system") | N/A | None | None | glmark2 is not verified for native Windows and this app does not launch WSL programs; the reason says whether WSL is present |
| **speedometer** | Browser detection | `READY_MANUAL` | Assisted / Manual | 3.1 | Browser | Compatible browsers detected: Chrome 155, Edge 155 (manual execution required) | Verified manually by the user on this machine, 2026-10-16 |
| **jetstream** | Browser detection | `READY_MANUAL` | Assisted / Manual | 3.0 | Browser | Compatible browsers detected: Chrome 155, Edge 155 (manual execution required) | Verified manually by the user on this machine, 2026-10-16 |

---

## Requirements

- Windows 10/11
- Python 3.10 (the code must stay 3.10-compatible)
- Windows for real discovery and launching; other systems are unverified

### Installation on a new machine

The project uses one virtual environment, `.venv` in the project root (git-ignored).
A virtual environment cannot be copied between machines: it points at the Python
install of the machine that created it, so create a fresh one after cloning.

```powershell
git clone https://github.com/detailstech93/pc-benchmark-manager.git
cd pc-benchmark-manager
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # runtime + pytest
.\.venv\Scripts\python.exe -m pytest tests -q
```

`requirements.txt` holds the runtime dependencies (PySide6, pefile, psutil);
`requirements-dev.txt` adds pytest on top of them.

## Usage

### Launching the Application

Double-click `run.bat` in the project folder, or from PowerShell in the project folder:

```powershell
.\.venv\Scripts\python.exe scripts\main.py
```

The console window prints the database path and any errors; closing it closes the app.

### Environment Profile

Built in the background scan worker (`app/environment/`) and shown on the Dashboard; Rescan refreshes it.
Every value is labeled `detected`, `unverified` or `unavailable`, with its source in the tooltip.
Real-machine check: `python scripts/verify_environment.py`.

| Field | Windows source | Status on Windows | Linux |
|---|---|---|---|
| OS name / version / build | build number (registry ProductName says "Windows 10" on Windows 11), DisplayVersion, UBR | detected | `/etc/os-release`: unverified |
| Native / app architecture, x64 emulation | `IsWow64Process2`, `sysconfig.get_platform()` | detected (env-var fallback: unverified) | `uname -m`: unverified |
| App runs as administrator | `shell32.IsUserAnAdmin` | detected; UIPI limits on automating elevated windows: unverified | `geteuid() == 0`: unverified |
| CPU / RAM / GPUs | existing `system_info` (all GPUs listed) | detected | partly unavailable |
| DirectX / Vulkan / OpenGL | runtime DLLs in System32 | DirectX detected; Vulkan/OpenGL unverified (loader present does not prove driver support) | not probed |
| WSL / display server | n/a on Windows | detected / unavailable | env vars, `/proc/version`: unverified |
| Virtual machine | no reliable non-invasive method | unavailable | unavailable |

Compatibility (`app/environment/compatibility.py`): each definition lists supported OS families, per-architecture support (`native` / `emulated` / `unsupported` / `unverified`, all `unverified` until tested) and an optional required graphics API. Only proven mismatches make a found benchmark "Unsupported on this system"; missing information gives `unverified`. Emulated results are flagged as not comparable to native ones.

Linux: detection logic is tested with mocked inputs only, unverified on real hardware. ARM64: logic and mocked tests only, unverified on real ARM hardware.

### Detected Tools and Games

Shown in the Benchmark Library tab under "Detected tools and games", refreshed by Rescan, saved per scan in
`discovery_snapshots` (linked to the environment snapshot). Real-machine check: `python scripts/verify_discovery.py`.

Privacy: installed programs are matched against `data/known_software.py` (the benchmark suites, browsers,
PresentMon, HWiNFO, LibreHardwareMonitor, 7-Zip, fio, DiskSpd, MLPerf Client, and the Steam / Epic / GOG clients).
Anything else is dropped at once and never stored, shown or exported.

| Source | Windows | Linux |
|---|---|---|
| Installed programs | Uninstall keys (HKLM 64-bit, HKLM 32-bit, HKCU) | `dpkg-query`, `rpm`, `flatpak`, `snap`, executables on PATH: unverified on real hardware |
| Steam client | Valve registry keys, detected only if `steam.exe` exists (libraries: Phase 3C) | `~/.steam/steam`, `~/.local/share/Steam`: unverified |
| Epic games | `%ProgramData%\Epic\EpicGamesLauncher\Data\Manifests\*.item`: location and field names unverified (no Epic install tested) | n/a |
| GOG games | `HKLM\SOFTWARE\WOW6432Node\GOG.com\Games`: field names unverified (no GOG install tested) | n/a |

Games keep only name, store, install path, app id and version. No game is assumed to have an automatable
benchmark mode; that list will only be added with verified entries.

### Steam

The "Steam" tab lists the client, reachable libraries and installed apps; refreshed by Rescan.
Real-machine check: `python scripts/verify_steam.py`.

- Libraries: both `steamapps/libraryfolders.vdf` and `config/libraryfolders.vdf` are read as hints (sources disagree on
  the location; either can be stale). Unreachable libraries (unplugged drive, offline network share) are reported as notes.
- Games: only `appmanifest_<appid>.acf` files are read (editor temp files such as `appmanifest_X.acf~RF*.TMP` are skipped).
  Kept: appid, name, install folder, install state, build id, size. Never kept: `LastOwner` (account SteamID).
  Never read: `loginusers.vdf`, `ssfn*`, `config.vdf`.
- Each library is scanned as its own step in the background worker, so a slow network library cannot freeze the app.
- Proton, Steam Linux Runtime and Steamworks Redistributables are listed as "tool" and cannot be launched.
- Launching: `steam://rungameid/<appid>` only for an appid from a local manifest, after an explicit confirmation, without
  launch arguments. Reported as `launched` only if a new process starts inside the game's folder within 60 s, otherwise
  `unconfirmed`. Steam settings are never changed and no process is ever closed.
- `data/steam_benchmarks.py`: appids checked against Steam's official store endpoint on 2026-10-17 (3DMark, Cyberpunk 2077,
  Red Dead Redemption 2, Shadow / Rise of the Tomb Raider, Borderlands 3). Their built-in benchmark modes are
  **unverified** until run on a real machine.
- Linux: client folders `~/.steam/steam`, `~/.local/share/Steam`; native vs Proton and the Proton version are
  "unavailable" (not read reliably yet). Results across Windows/Linux or Proton versions must not be compared.
  Linux and Flatpak/Snap Steam: unverified on real hardware.

Verified manually on this machine (2026-10-17): libraries `C:\Program Files (x86)\Steam` and `Z:\SteamLibrary`
(network share), 15 apps found, no SteamID in the results. Launching a game from the tab: not tested yet.

### Results, Sessions and Statistics

- Recording: in **Run Benchmarks**, "Record result..." per ready benchmark. Choose measured or warmup, the status
  (completed, failed, timed_out, cancelled, skipped, invalid, awaiting_manual_input), the score exactly as displayed,
  unit and note. All entries are stored as MANUAL (`is_manual=1`). Only completed runs may have a score: a failure is
  never stored as a number, and the database refuses it.
- Sessions: the first recorded result creates a session; "Start new session" begins the next. Each session gets a
  manifest once (`session_manifests`): app version and git commit, Python version, run settings, the environment
  profile, the benchmarks ready at session start (version, path, architecture, compatibility), the games used, and a
  comparability block (runtimes, emulation).
- Statistics (**Session History** tab, `app/stats.py`): per session and benchmark, runs counted, mean, median, min,
  max, sample standard deviation and CV %. Warmup, not-completed, test and score-less runs are excluded and counted
  by reason; the source column shows MANUAL, real or mixed. Std dev and CV need at least two runs.
- Schema changes are additive (`status`, `is_warmup`, `run_index`, `note` on results); older rows keep their data and
  their status is derived from `passed`.

Check the production database: `python scripts/verify_stats.py` (add `--include-test` to show test data).

### History, Comparison and Run Profiles

- **Session History** search: text in the session name/description, benchmark (a suite such as Geekbench also finds
  its per-major entries), and an inclusive date range (YYYY-MM-DD). The statistics table follows the search.
- **Compare Results** tab (`app/compare.py`): pick session A (baseline) and B; for each benchmark it shows mean A,
  mean B, delta and delta % relative to A, and a status:
  - **not comparable** (red, numbers greyed): no results on a side, different major versions (Geekbench 6 vs 7 use
    different scales), different native architecture, different runtimes (e.g. native vs Proton), emulation;
  - **warning** (orange): version difference or unknown, different OS, fewer than 2 runs, CV above 5 %, mixed
    manual and real runs, manual vs real, test data;
  - **ok** (green).
- **Run profiles** (Run Benchmarks tab): built-in "Standard" (3 measured + 1 warmup) and "Quick check" (1 run), plus
  saved profiles (`run_profiles` table). The selected profile's settings are stored in the manifest of the next
  new session.

Command line: `python scripts/verify_compare.py [A B] [--include-test]` compares two sessions with the same rules.

### Reports, Export and Backup

Session History tab: select a session in the sessions table, then:

- **HTML report...**: one standalone file (inline CSS, no scripts, no external links, light/dark) with summary
  statistics, every run (warmup, failed and test runs flagged; a failed run never shows a score), the environment
  from the manifest and the reproducibility settings (`app/report.py`). The app asks before opening it.
- **Export JSON...**: the full session (`app/export.py`, format `pc-benchmark-manager.session` v1): session,
  manifest, runs and per-benchmark statistics. NaN/infinity are written as `null`.
- **Export CSV...**: one row per run (`app/csv_export.py`), UTF-8 with BOM so Excel shows Arabic names; text cells
  starting with `= + - @` are prefixed with `'` so spreadsheets do not run them as formulas.
- **Back up database**: copies the database to `backups\benchmark_manager_YYYYMMDD_HHMMSS.db` next to it
  (SQLite backup API, integrity-checked, plus a `.sha256` file). Existing backups are never overwritten or deleted.
  To use a backup, point `database_path` in `settings.json` at it; nothing is restored automatically.

Files are written under a temporary `.partial` name and renamed when complete.
Command line: `python scripts/verify_export.py [id] [--include-test] [--out DIR] [--backup]`.

### Database Location

The production database is stored per user, not in the project folder:

- Windows: `%LOCALAPPDATA%\PCBenchmarkManager\benchmark_manager.db`
- Other systems: `$XDG_DATA_HOME/PCBenchmarkManager/` (default `~/.local/share/PCBenchmarkManager/`)

To use another file, create `settings.json` in that folder with `{"database_path": "D:\\path\\to\\my.db"}`.
On first start, an existing `benchmark_manager.db` in the project root is copied there once (SQLite backup API, source opened read-only, integrity-checked); the original is never modified or deleted. The startup console prints the database path in use. Tests only use temporary databases. See `app/paths.py`.

Verified manually on this machine (2026-10-17): the root database was copied, `integrity_check` returned `ok`, the original's SHA256 was unchanged, and a second start did not copy again.

### Running Real Discovery Validation (No Mocks)

These scripts depend on the host machine and are not part of pytest.

```bash
python scripts/verify_real_scan.py
python scripts/verify_geekbench_versions.py
```

### Running Unit and Integration Tests

```bash
# Run all tests
python -m pytest tests -v

# Byte-compile check
python -m compileall app tests
```

---

## Code Locations

- **`BenchmarkWorker`**: [`app/ui/main_window.py`](app/ui/main_window.py#L29-L62) - Standalone `QThread` subclass with `scan_started`, `scan_finished`, `scan_error`, and `progress` signals.
- **`LaunchWorker`**: [`app/ui/main_window.py`](app/ui/main_window.py) - `QThread` for launch operations (Popen, UAC wait, process polling). GUI thread never blocks.
- **`_WebLaunchWorker`**: [`app/ui/main_window.py`](app/ui/main_window.py) - `QThread` for spawning browsers to web benchmark URLs.
- **`MainWindow.start_scan()`**: [`app/ui/main_window.py`](app/ui/main_window.py) - Background scan manager holding `self._scan_worker` reference and managing "Scanning..." UI state.
- **`MainWindow._on_launch_clicked()`**: [`app/ui/main_window.py`](app/ui/main_window.py) - Confirmation dialogs, concurrent-launch guard, and worker dispatch.
- **`MainWindow.closeEvent()`**: [`app/ui/main_window.py`](app/ui/main_window.py) - Waits for or cancels both `_scan_worker` and `_launch_worker`.
- **Process snapshot helpers**: [`app/benchmark_manager.py`](app/benchmark_manager.py) - `_get_process_snapshot()`, `_has_new_process()`, and `_launch_with_elevation_fallback()`.
- **`normalize_version()`**: [`app/benchmark_manager.py`](app/benchmark_manager.py) - Helper handling commas, dots, and mixed separators.
- **PE Metadata Inspection**: [`app/benchmark_manager.py`](app/benchmark_manager.py) - `get_pe_architecture()`, `get_pe_version()`, and `get_pe_product_name()`.
- **Geekbench Dynamic Discovery**: [`app/benchmark_manager.py`](app/benchmark_manager.py) - Dynamic discovery, major grouping, multiple install prioritization, and unknown major flagging.
- **Cinebench Store Package Detection**: [`app/benchmark_manager.py`](app/benchmark_manager.py) - `Get-AppxPackage` query and AppID resolution.
- **Comparability Rules**: [`app/benchmark_manager.py`](app/benchmark_manager.py) - `can_compare_geekbench_scores()` and `assert_can_compare_benchmarks()`.
- **Database Self-Migration**: [`app/database.py`](app/database.py) - Additive column check and migration.
- **Tests**: [`tests/test_benchmark_manager.py`](tests/test_benchmark_manager.py) - Includes `TestProcessSnapshot` (snapshot logic), `TestElevationFallbackMocks` (OSError paths), `TestChannelBoundaryMocks` (fake registry), and `TestChannelIsolation` (failing channel). Line references: `TestProcessSnapshot` at line ~850, `TestElevationFallbackMocks` at line ~700, `TestChannelBoundaryMocks` at line ~280, `TestChannelIsolation` at line ~420.
