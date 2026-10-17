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
  A second table, **Extra metrics**, compares the frame capture numbers and telemetry (`tel_*`) recorded with the
  runs (`app/metrics_summary.py`), over the same runs as the means. A metric found on one side only, or with a
  different unit on each side, is listed with a note and no delta.
- **Run profiles** (Run Benchmarks tab): built-in "Standard" (3 measured + 1 warmup) and "Quick check" (1 run), plus
  saved profiles (`run_profiles` table). The selected profile's settings are stored in the manifest of the next
  new session.

Command line: `python scripts/verify_compare.py [A B] [--include-test]` compares two sessions with the same rules.

### Reports, Export and Backup

Session History tab: select a session in the sessions table, then:

- **HTML report...**: one standalone file (inline CSS, no scripts, no external links, light/dark) with summary
  statistics, every run (warmup, failed and test runs flagged; a failed run never shows a score), the environment
  from the manifest and the reproducibility settings (`app/report.py`). An **Extra metrics** table lists the mean,
  min and max of each frame capture and telemetry metric over the same runs as the statistics (metrics whose unit
  differs between runs are not summarized). The app asks before opening it.
- **Export JSON...**: the full session (`app/export.py`, format `pc-benchmark-manager.session` v1): session,
  manifest, runs (each with its `metrics`) and per-benchmark statistics, plus `metric_summaries`.
  NaN/infinity are written as `null`.
- **Export CSV...**: one row per run (`app/csv_export.py`), UTF-8 with BOM so Excel shows Arabic names; text cells
  starting with `= + - @` are prefixed with `'` so spreadsheets do not run them as formulas.
- **Back up database**: copies the database to `backups\benchmark_manager_YYYYMMDD_HHMMSS.db` next to it
  (SQLite backup API, integrity-checked, plus a `.sha256` file). Existing backups are never overwritten or deleted.
  To use a backup, point `database_path` in `settings.json` at it; nothing is restored automatically.

Files are written under a temporary `.partial` name and renamed when complete.
Command line: `python scripts/verify_export.py [id] [--include-test] [--out DIR] [--backup]`.

### Automation (Command-Line Tools)

Run Benchmarks tab, section **Automated runs**. Each external command-line benchmark is one small JSON spec in
`data/tools/` (format and rules in `data/tools/README.md`); a broken spec file is skipped and its errors are shown.

- **Run automated...** shows every command, the run count and the worst-case duration, and asks first. Repetitions,
  warmup runs and cooldown come from the selected run profile; the timeout comes from the tool spec. Each plan
  creates a new session with its manifest (a test session when only `selftest_` tools are selected).
- Every run is stored with its status. A run that is skipped, failed, cancelled, timed out or produced no usable
  score (`invalid`) never gets a score, and its run folder gets `diagnostics.json` (output tails and a system snapshot).
- **Emergency stop** (red button, or `Ctrl+Shift+X` while the app window has focus) ends the current run and starts
  no further run. There is no system-wide hotkey. A stop or timeout terminates only the process tree the app itself
  started (terminate, then kill after 5 s); no other process is touched.
- **Screenshot on failure** is off by default because it captures the whole desktop. It is saved only in the run folder.
- Run folders: `automation_runs\` next to the database (stdout, stderr and diagnostics for each run).

Only the framework self-test (`selftest_python`, a 2 s Python loop stored as test data) ships for now. No command
for a real tool is added until it has been checked on a real machine with its raw output.

Design decision (GUI benchmarks such as Geekbench): their windows may run elevated, and Windows UIPI blocks
a normal-integrity process from sending input to an elevated window (not verified on this machine). They stay
assisted: the app launches them, and the user records the result. Tools that need administrator rights
(`requires_elevation: true`) are rejected by the spec validator. The Geekbench command line needs a Pro license and is not used.

Real-machine check (temporary database by default; `--production` stores the self-test as a test session):

```bash
python scripts/verify_automation.py
```

Verified on this machine (2026-10-17): 3 self-test runs stored as `completed` with their raw stdout; the emergency
stop ended a child and its own child within 1.2 s (`cancelled`, 2 runs not started); a 1 s timeout ended both
processes (`timed_out`, no score, diagnostics written).

### Frame Capture (PresentMon)

Run Benchmarks tab, section **Frame capture**. PresentMon records every frame of one game process; the app stores
average FPS as the score and 1% low, 0.1% low and frame-time numbers as extra metrics of the run. Every capture
needs administrator rights, so Windows shows its UAC prompt for each capture after you confirm; declining it
cancels the rest of the plan (nothing is stored as a score).

There is no download button. When you press **Capture frames...** (after picking the game process), the app looks
for PresentMon; if no usable copy is found, it asks whether to download it, and the capture goes on right after a
verified download. "No" downloads nothing and captures nothing.

**Game benchmark (automatic)**: pick a game in **Game (Steam)** and press **Benchmark game...**, or press
**Benchmark...** next to the game in the Steam tab. After one confirmation everything else runs by itself
(`app/frames/game_benchmark.py`):

1. PresentMon starts elevated ONCE, so there is one UAC prompt per benchmark, not one per run. It records only
   the programs in the game's install folder (`--process_name` for every .exe there, because the game's exact exe
   is known only after it starts) and ends by itself when they have exited (`--terminate_on_proc_exit`).
2. The game is launched with `steam://rungameid/<appid>` (same rules as the Steam tab's Launch). A game with an
   adapter may name fixed launch flags; then `steam://run/<appid>//<flags>/` is used, for that launch only (never
   written to the game's Steam launch options). Cyberpunk 2077: `--launcher-skip -skipStartScreen` (no REDlauncher,
   no "press any key" screen). Steam may ask you to allow the arguments; not yet verified on a real launch.
3. The app waits by the clock: **Skip first (s)** (default 30, loading and menus) plus the profile's warmup and
   measured runs of **Seconds** each, times 1.15 plus 20 s for loading. PresentMon keeps its CSV locked while it
   writes (checked on this machine: "Permission denied"), so it cannot be read during the run.
4. Then the app closes the game (`app/frames/game_close.py`): only processes that started inside the game's
   install folder after the launch, remembered by pid and start time. First WM_CLOSE to their windows (like
   clicking X); whatever still runs after 30 s is ended by force. Unsaved progress in the game is lost. Steam is
   never touched. A game running as administrator cannot be ended by this non-elevated app; the status then says
   to close it yourself. The same happens when the benchmark is stopped or fails after the launch.
5. PresentMon ends by itself and writes the whole CSV. The game's process is the started one with the most
   frames. Game time is the sum of its frame times; the first Skip seconds are dropped and the runs are cut as
   consecutive windows. Each window is stored as one run (score = average FPS, plus 1% low and the other frame
   metrics). A window the recording does not reach is stored as `invalid` with the reason and no score. If
   nothing was recorded (UAC declined, the game did not start, no frames), nothing is stored and no session is
   created.
6. With Record telemetry on, samples are cut into the same windows, aligned on the moment the game was asked to
   close (about one second).

PresentMon records nothing inside a Remote Desktop session: run game benchmarks at the computer's own screen.
Not yet verified on a real game (only with fakes in tests/test_game_benchmark.py).

### Forced game settings

Pressing Benchmark (Steam tab) or Benchmark game... for a supported game first opens the settings window, starting
from the last choice; Cancel starts nothing. **Game settings...** opens the same window without a benchmark. It
picks the graphics settings forced on the benchmark: resolution, window mode, VSync, FPS limit, quality preset,
upscaler and DLSS mode, texture quality, dynamic
resolution, Frame Generation (DLSS, multi frame), ray tracing, RT lighting and path tracing. Each one can stay
"keep the game's value". The choice is saved in settings.json (`forced_game_settings`). Supported so far:

| Game | Adapter | Settings file |
|---|---|---|
| Cyberpunk 2077 | `app/games/cyberpunk2077.py` | `%LOCALAPPDATA%/CD Projekt Red/Cyberpunk 2077/UserSettings.json` |
| Borderlands 3 | `app/games/borderlands3.py` | `Documents/My Games/Borderlands 3/Saved/Config/WindowsNoEditor/GameUserSettings.ini` |
| Rise of the Tomb Raider | `app/games/rise_tomb_raider.py` | registry: `HKEY_CURRENT_USER/Software/Crystal Dynamics/Rise of the Tomb Raider/Graphics` |
| Red Dead Redemption 2 | `app/games/red_dead_redemption2.py` | `Documents/Rockstar Games/Red Dead Redemption 2/Settings/system.xml` |
| The Witcher 3 | `app/games/witcher3.py` | `Documents/The Witcher 3/dx12user.settings` |

Borderlands 3 offers resolution, window mode, VSync, frame rate limit, quality preset, resolution scale, dynamic
resolution, anti-aliasing, texture quality and graphics API (DX11/DX12). Only the changed values are rewritten;
every other byte of the file stays as it was.

Rise of the Tomb Raider keeps its settings as DWORD values in the registry. Writing them was allowed by the
user on 2026-10-18, always after the benchmark's confirmation: the backup is a JSON copy of the key's values in
the run folder, only values that differ are written, and the same values are put back after the run. It offers
resolution, fullscreen and exclusive fullscreen, VSync, graphics API (DX11/DX12), DLSS, texture, shadow, ambient
occlusion and detail levels, SSAA, FXAA, SMAA and motion blur. Levels, VSync and DLSS are numbers the game does
not explain in the registry, so only numbers already used by the game are offered.

Red Dead Redemption 2 and The Witcher 3 share `app/games/text_settings.py` (named text values; only the changed
characters are rewritten). Red Dead Redemption 2 offers resolution, window mode, VSync, graphics API
(Vulkan/DX12), texture, shadow, lighting, volumetrics and reflection quality (Low to Ultra, named in the file),
ambient occlusion, TAA, MSAA, FXAA, DLSS, DLSS mode, FSR and motion blur. The Witcher 3 (DirectX 12 mode only)
offers resolution, window mode, VSync, FPS limit, anti-aliasing, DLSS mode, DLSS Frame Generation, ray tracing,
path tracing and HairWorks. Numbers the files do not name are learned like the other games.

Not supported, checked on this computer on 2026-10-18: God of War keeps its settings inside its save file;
Shadow of the Tomb Raider keeps them in a folder named after the Steam account (not touched); Counter-Strike 2 keeps them in a folder named after the Steam account (not touched); DOOM (2016) writes
almost none.

With forced settings, Benchmark game... says so in its confirmation, then (`app/games/settings_file.py`):

1. copies the game's settings file unchanged into the run folder (`game_settings_backup.json`);
2. writes the chosen values (temporary file, then replace) and reads the file back. If a value cannot be
   written (not offered by the game, or its position unknown) or does not read back as asked, the game is not
   started and your file is put back;
3. after the game was closed, reads the file again: the game rewrites it with what it really used. Runs measured
   while the game did not keep a forced value are stored as `invalid`, without a score;
4. copies your own file back.

What was asked, your values before, what changed, what was refused and both checks are stored in the session
manifest (`run_settings.game_settings`, with `verified`).

Lists the game builds at runtime (resolution, DLSS mode, upscaler, preset, window mode, VSync) store the value's
position in a list that is not in the file. The app learns a position each time it reads the file
(`game_settings_indices.json` in the data folder), so the window offers only values seen before: to force another
one, choose it once in the game's menu, close the game and open Game settings... again. Fixed lists (RT lighting,
multi frame generation, texture quality) and on/off settings offer every value at once. Turning the upscaler or frame generation Off works the same way: set Off once in the game.

`scripts/verify_game_settings.py` reads the real file and writes nothing. Checked on this computer on 2026-10-18:
every setting read, keeping the current values plans no change. Writing, then the game keeping the values, is not
yet verified on a real run.

Finding PresentMon, in this order (nothing is downloaded when a usable copy exists):
1. a file you chose with **Select PresentMon...**;
2. an Intel PresentMon installation (`Program Files\Intel\PresentMon\PresentMonConsoleApplication`);
3. a copy the app downloaded earlier (`tools\presentmon\<version>\` in the data folder);
4. otherwise the download offered by **Capture frames...**: the newest official file from GitHub (GameTechDev/PresentMon releases). The app shows
   the file name, version, size, URL and SHA-256 and asks first. The file is kept only when its size and SHA-256 match
   what GitHub publishes and its Authenticode signature is valid and from Intel Corporation; otherwise it is deleted.
   Copies bundled inside other tools (FrameView) are refused. If a file you chose is gone, the app asks you to
   choose it again instead of downloading another one.

Architectures:
- **x64**: official build (2.6.0 checked on this machine: SHA-256 equals the GitHub digest, signature valid, Intel).
- **x86 (32-bit Windows)**: the latest release has no x86 file; the newest release that has one is chosen (2.4.1).
- **ARM64 Windows**: Intel publishes no ARM64 build (GameTechDev/PresentMon#667 closed "not planned"). The x64 file
  is used under Windows x64 emulation and the app says so; this is NOT verified on ARM hardware. A native arm64 file
  is preferred automatically if one is ever published.
- **Linux**: PresentMon is Windows-only. The alternative is MangoHud. Capturing frames with MangoHud is not implemented
  yet, so pressing Capture frames... on Linux says so and downloads nothing. The installer code is ready for it: it builds
  the command for the distribution's package manager (apt-get, dnf, pacman, zypper), shows it, and runs it through
  `pkexec` (the system password prompt) after you confirm. A MangoHud package was checked to exist for Debian on x64 and ARM64 only; other
  combinations are marked as not verified.

Frame capture does not work inside a Remote Desktop session: run it at the machine's own screen (console session).

Real-machine check:

```bash
python scripts/verify_frames.py            # find PresentMon, check its --help lists every flag the app uses
python scripts/verify_frames.py --capture  # plus one 10 s capture of the script's own animated window (UAC prompt)
```

Verified on this machine (2026-10-17, PresentMon 2.6.0 x64): `--help` lists every flag the app uses. At the local
console, a 10 s capture of the script's own Direct3D 11 window gave 590 frames, 59.4 FPS average, median frame time
16.67 ms (60 Hz display), DXGI "Hardware Composed: Independent Flip", 0 dropped and 0 skipped rows; stored as
`completed` in fps with its metrics. Earlier captures from a Remote Desktop session (`rdp-tcp`) exited with code 0
but recorded no frames, so no CSV was written and the runs were stored as `failed` without a score.

### Telemetry

**Record telemetry** (on by default, in both the Automated runs and Frame capture sections) samples once per second
during every run. Each run folder gets `telemetry.csv` (every sample) and `telemetry.json` (sample count, sources that
were not available and why, read errors). Completed runs also store `tel_<reading>_avg` and `tel_<reading>_max` as
extra metrics. A reading that a source cannot give is left out, never stored as 0.

| Source | Readings | Notes |
|---|---|---|
| psutil | CPU load, memory use; on Linux also hottest CPU temperature and live CPU clock | CPU load is computed from the app's own `cpu_times()` deltas. `psutil.cpu_percent()` is not used, because any other caller resets its shared window and it then reports a false 0. On Windows `cpu_freq()` is the static base clock, so it is not recorded. |
| nvidia-smi | per NVIDIA GPU: temperature, SM and memory clocks, power, load | No admin rights needed; ships with the NVIDIA driver (Windows and Linux). `[N/A]` values are left out. |
| LibreHardwareMonitor | hottest CPU temperature, CPU package power, average core clock | Windows only. Read from LibreHardwareMonitor's own local web server (`http://127.0.0.1:8085/data.json`; only a loopback address is accepted). A run with Record telemetry checks for it when it starts and offers to set it up, see below. A copy you run yourself also works when its Options > Remote Web Server is on. |
| RAPL | CPU package power | Linux only, from the kernel's energy counters (`/sys/class/powercap/intel-rapl:N`, also used for AMD). Since kernel 5.10 only root may read them (CVE-2020-8694); then the reason is shown and the reading is absent. CPU temperature and live clock on Linux come from psutil (hwmon). |

HWiNFO is not used: its free version limits the shared-memory interface to 12 hours.

**LibreHardwareMonitor check at run start** (Windows; `app/ui/lhm_setup.py`, `app/telemetry/lhm_provision.py`).
There is no setup button. When a run or capture starts with Record telemetry on, nothing is asked if
LibreHardwareMonitor is already running (any copy). Otherwise ONE question lists everything below (download if this
app has no copy yet, the password, the UAC prompt); on yes the steps run, the app waits up to 30 s until it answers,
then the run starts. On no, or when a step fails, the run still starts, without CPU sensors, and the status line says
why; a "no" is not asked again until the app restarts.

1. Uses the copy it set up before (`<data dir>\tools\librehardwaremonitor\<version>\`), or downloads the newest stable
   release from GitHub (LibreHardwareMonitor/LibreHardwareMonitor, MPL-2.0): `LibreHardwareMonitor.zip`, the
   .NET Framework 4.7.2 build that runs on Windows 10/11 as installed (the `.NET.10` zip would need another runtime).
   The zip is not code-signed, so it is kept only when its size and the SHA-256 GitHub publishes match. It is
   extracted through a temporary folder; no file may land outside it.
2. Writes `LibreHardwareMonitor.config` next to the exe (only while it is not running, because it rewrites that file
   on exit): web server on, port 8085, start minimized to the tray, and Basic authentication with a new random
   password. The password is needed because its server cannot be limited to this PC (checked in its v0.9.6
   `HttpServer.cs`: it listens on every interface) and it can also change fan settings. It stores the password as a
   SHA-256 hash and saves only on exit or sign-out, so while it runs the password it expects is the value in that
   file; telemetry reads it from there on every request.
3. Starts it through the UAC prompt (CPU sensors need administrator rights). LibreHardwareMonitor may then ask to
   install its sensor driver (PawnIO); that is its own prompt. This app never installs a driver.

Windows on ARM: the same .NET Framework build runs there, but its sensor driver is not verified on ARM, so CPU
sensors may be missing (telemetry then says so). Linux: LibreHardwareMonitor does not exist there; psutil (hwmon) and
RAPL cover CPU temperature, clock and package power without a download, and nothing is asked.

```bash
python scripts/verify_telemetry.py
```

Verified on this machine (2026-10-17): psutil and nvidia-smi available (RTX 5090 Laptop GPU, driver 617.42), with 6 samples
in 5 s (for example GPU 44 C, 180 MHz SM, 405 MHz memory, 10.3 W). LibreHardwareMonitor v0.9.6 (run as administrator,
Intel Core Ultra 9 275HX): 420 sensors read; hottest CPU temperature 67-78 C, package power 16-23 W, average core clock
about 3.8 GHz while idle. Its `RawValue` is text such as `17.3 W`, written in the current Windows number format, so a
decimal comma and Arabic-Indic digits are read too; `NaN` readings are skipped. Intel's "Distance to TjMax" sensors are
typed as temperatures but are headroom, so they are left out of the hottest temperature. Linux and ARM: not verified.

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
