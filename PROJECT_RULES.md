# PROJECT_RULES.md

## Architecture

### Detection is data-driven

**Never hardcode install paths, executable names, or version numbers in detection code.**

Every benchmark suite is described by a `BenchmarkDefinition` record in
`data/benchmark_definitions.py`.  A single generic discovery routine
(`BenchmarkManager._discover_from_definition`) reads those definitions at
runtime.  To add a new suite, append a record -- do not add another
`_detect_*` method.

Each `BenchmarkDefinition` holds:

| Field | Purpose |
|-------|---------|
| `id` | Unique key (e.g. `"geekbench"`, `"cinebench"`) |
| `display_name` | Human-readable name |
| `category` | `"cpu"`, `"gpu"`, `"web"`, `"synthetic"` |
| `official_url` | Official page URL |
| `name_patterns` | Regex list for matching executables, packages, registry names |
| `exclusion_patterns` | Regex list for sibling products that must NOT match |
| `version_source` | Where to read the version: `pe_version_info`, `appx_package`, `registry`, or `web_page` |
| `version_normalization` | Rule: `commas_to_dots` or `passthrough` |
| `major_version_policy` | `separate_per_major`, `single_series`, or `web` |
| `supported_os` | List of supported OSes |
| `supported_architectures` | Default `["unverified"]` |
| `license_note` | Optional license / execution note |
| `discovery_channels` | Which channels to use: `registry`, `filesystem`, `app_paths`, `start_menu`, `appx`, `browser` |
| `benchmark_page_urls` | For web benchmarks: the actual page URL(s) |
| `editions_are_separate` | For Cinebench-style products: editions are separate series |

### Detected item metadata

Every detected item stores:

- **How it was detected** -- registry / filesystem pattern / Appx / Start Menu / App Paths
- **Path or package identity** -- the resolved path or Appx package name
- **Normalized version** -- commas-to-dots, stripped
- **Architecture** -- read from the PE header (`Machine` field) or package metadata, or `"unknown"`
- **`file_exists` flag** -- re-verified on each scan

### Multiple installs of the same suite and major version

Report the highest version as primary; list the others in `additional_installs`.

### New major versions

A new major version that is not in the known-versions list is detected
automatically and labeled:

> newly seen version: score comparability with other majors unknown

Scores are never compared across majors or editions by default.

### Cinebench

Editions are separate series. Read the edition and version from package/file
metadata; do not infer a commercial name beyond what the metadata says.

### Web benchmarks (Speedometer, JetStream)

The benchmark version is a property of the page, not of an installation.
Official URLs live in the definitions data (not in code). Record the page
URL and the version string reported by the page with every result. Treat
browsers separately (version and release channel from file metadata,
architecture from the PE header). Do not hardcode version strings like
`"3.1"` or `"3.0"` in logic.

### PE metadata

Always read PE headers statically (`pefile`). Never launch benchmark
binaries to probe version or architecture.

### Testing

Include mocked tests for:

1. A fake new major version (auto-detected, labeled as newly seen)
2. A sibling product that must be ignored (exclusion patterns)
3. A minor update (version normalization)
4. Two installs of the same major (highest version as primary)
5. A stale leftover folder whose executable no longer exists
6. A Store-only install (Appx, no executable path)

## Environment & Compatibility

- **Python 3.10 compatibility**: All code must run on Python 3.10. Do not use features
  introduced after 3.10 without a compatibility shim.
- **Never read `.venv` or `venv`**: Do not inspect, modify, list, or execute anything
  inside `.venv/` or `venv/`. The virtual environment is managed externally. Running the
  venv interpreter (`python` from within the venv) is allowed.
- **One virtual environment**: `.venv` in the project root is the only supported one
  (commands use `.\.venv\Scripts\python.exe`). Any other venv folder is legacy.
- **If a file read or command fails twice, stop and report**; never retry a third time;
  never loop reading the same files.
- **Never create files with Windows reserved device names** (`nul`, `con`, `prn`, `aux`,
  `com1`–`com9`, `lpt1`–`lpt9`). Never redirect output to `nul` from a Unix-style shell;
  use `$null` or `Out-Null` in PowerShell. Scratch files go to `%TEMP%` or a `tmp/` folder,
  never the project root.

## Execution Safety

- **Never run `geekbench*.exe` or any GUI executable with `--help`**: Benchmark binaries
  are NEVER executed to probe version, help text, or capabilities. Use static PE metadata
  (`pefile`) exclusively.
- **No invented command-line options**: Do not fabricate CLI flags or arguments for any
  benchmark. Only use options that are documented and verified.
- **No checkmarks without raw output**: Every claim of success in documentation or reports must be
  backed by raw, verifiable output (test results, scan logs, compile output). Do not use
  checkmarks or green indicators without showing the actual data.

## Data-Driven Detection

- All benchmark discovery is data-driven via `BenchmarkDefinition` records.
- No suite-specific detection methods beyond the generic `_discover_from_definition()`.
- New suites are added by appending a definition record, not by writing code.

## GUI-Thread Rule

- All benchmark scanning MUST run off the GUI thread via `BenchmarkWorker` (QThread).
- All launch operations (subprocess.Popen, UAC wait, process polling) MUST run off the
  GUI thread via `LaunchWorker` (QThread). The confirmation dialog (`QMessageBox.exec()`)
  is modal on the GUI thread; after user confirmation the worker takes over.
- The scan worker is stored as `self._scan_worker` on `MainWindow`.
- The launch worker is stored as `self._launch_worker` on `MainWindow`.
- `closeEvent` waits for or cancels both workers before the window closes.
- Two concurrent launches are blocked by a guard check in `_on_launch_clicked`.
- `BenchmarkExecutor` handles actual benchmark execution; it is a separate class from
  `BenchmarkWorker` and must not share a class name with it.

## Working Rules

- Answer with evidence: show raw output (commands, test results, file reads) before drawing conclusions.
- Do not invent data, paths, or behavior. If something is unknown, say so.
- Changes are additive unless the user explicitly asks for removal or refactoring.
- Keep state in one place: do not create parallel sources of truth or duplicate configuration.

## System Safety

- Never execute benchmark binaries for probing. Use static PE reads (`pefile`) only.
- Launch operations require explicit user confirmation in the UI.
- **Elevation**: the app stays non-elevated. Elevated launch only through the OS UAC
  prompt after explicit user confirmation. Never bypass or suppress UAC, edit manifests,
  or set compatibility flags. A declined UAC prompt is a neutral "cancelled" outcome.
- Elevation (UAC) is only triggered as a fallback when WinError 740 occurs.
- Process confirmation uses snapshot-based comparison: a before-snapshot of running
  processes is taken, the launch occurs, then an after-snapshot verifies a NEW matching
  process appeared (full image-name match, not substring).

## Data Safety

- Database migrations are additive: use `ALTER TABLE ... ADD COLUMN` with existence checks.
- Old database files remain readable after migration with zero data loss.
- Test data is marked with `is_test` and `is_manual` flags and hidden by default in the UI.

## Testing

- All tests are under `tests/` and run with `python -m pytest tests -v`.
- **Tests use temporary databases** (`tmp_path`, `tempfile.mkdtemp`, or an in-memory
  `:memory:` SQLite DB). Test rows are never written to the real database.
- Default queries exclude `is_test` rows (`WHERE is_test = 0`).
- Channel-boundary tests feed fake registry/filesystem data into real channel functions (clearly labeled as mocks).
- Web benchmark URL verification (`scripts/verify_web_urls.py`) uses real network calls and is NOT part of the pytest suite.
- Test-only packages belong in `requirements-dev.txt`; runtime packages in `requirements.txt`.

## GUI Rule (General)

- The GUI thread must never block on I/O, network calls, or long computations.
- Background work goes through `BenchmarkWorker` (QThread) with Qt signals for UI updates.
- Launch operations (subprocess.Popen, UAC wait, process polling) run in `LaunchWorker`
  (QThread). The confirmation dialog (`QMessageBox.exec()`) is modal on the GUI thread;
  after user confirmation the worker takes over.
- `_launch_worker` is kept referenced on `MainWindow`; `closeEvent` waits for or cancels
  it. Two concurrent launches are blocked by a guard check.
- Web benchmark browser spawns run in `_WebLaunchWorker` (QThread) via the same slot
  infrastructure.

## Known Exceptions

- `winreg` is a Python stdlib module (not `pywin32`). Imported at module level in `app/benchmark_manager.py:19`; used extensively for registry discovery.
- `psutil` is imported inside `app/system_info.py:147` (within `get_ram_size()`) with a fallback to PowerShell if unavailable.
- `pefile` is imported at module level in `app/benchmark_manager.py:23`; it is a required runtime dependency.
- The `ssl` module is imported in `scripts/verify_web_urls.py:15`; the explicit disable of certificate verification has been removed; the default verifying context is used.

## Encoding Rule

- Every text-mode file open, `open()`, `Path.read_text()`, or `Path.write_text()` MUST pass `encoding="utf-8"`.
- This rule applies to all NEW code. Existing files do not need to be rewritten unless they are being edited for another reason.
- Binary I/O (`rb`, `wb`) is unaffected.
