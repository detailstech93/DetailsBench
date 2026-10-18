# Benchmark Automation Matrix

## Real Test Results on This Machine (Windows 11)

### Scan Performance
- **Total scan time**: 6.57 seconds (real execution, no mocks)
- **Benchmark discovery time**: < 1.0 second (dynamic registry, StartApps, Appx, Start Menu .lnk, and filesystem pattern discovery)
- **System capabilities check**: ~5.5 seconds (WSL check, DirectX 12 check, Vulkan check, CPU hyperthreading check)

### Benchmark Status Summary

| Benchmark | Type | Status | Version | Arch | Executable / Target Path | Detection Method | Reason / Notes |
|-----------|------|--------|---------|------|--------------------------|------------------|----------------|
| **geekbench_6** | CPU | READY_MANUAL | 6.7.1.0 | x64 | `C:\Program Files (x86)\Geekbench 6\Geekbench 6.exe` | Start Menu, filesystem pattern, registry | Windows GUI version requires manual execution; automation/CLI requires a Pro license; not verified for this version; License: not verified |
| **geekbench_7** | CPU | READY_MANUAL | 7.1.0.0 | x64 | `C:\Program Files\Geekbench 7\Geekbench 7.exe` | Start Menu, filesystem pattern, registry | Windows GUI version requires manual execution; automation/CLI requires a Pro license; not verified for this version; License: not verified |
| **cinebench** | CPU | READY_MANUAL | 2026.1.3.0 | X64 | *no exe path* (Store AppID: `MAXONComputerGmbH.Cinebench_rsne5bsk8s7tj!Cinebench`) | Appx, Start Menu | Store package version 2026.1.3.0, architecture X64; launch it from the Start menu |
| **d3dmark** | GPU | NOT_INSTALLED | None | None | *no exe path* | not_found | 3DMark not found on this system |
| **glmark2** | GPU | UNSUPPORTED | None | None | *no exe path* | wsl_check | glmark2 is not verified for native Windows; it can only be used through WSL, which this app does not launch. The reason notes whether WSL is present. |
| **speedometer** | Web | READY_MANUAL | 3.1 | None | *no exe path* | browser_detection | Compatible browsers detected: Chrome 155.0.8059.40, Edge 155.0.4283.45 (manual execution required) |
| **jetstream** | Web | READY_MANUAL | 3.0 | None | *no exe path* | browser_detection | Compatible browsers detected: Chrome 155.0.8059.40, Edge 155.0.4283.45 (manual execution required) |


### Automated runs (Phases 5-12, listed under Run Benchmarks > Automated runs)

| Workload | Type | How it runs | Verified here | Evidence (2026-10-18) |
|----------|------|-------------|---------------|-----------------------|
| **sevenzip_benchmark** | CPU | `7z b` (tool spec `data/tools/sevenzip_benchmark.json`) | yes | raw output: Tot 173,712 MIPS; `scripts/verify_resume.py` 173,594 / 171,550 MIPS |
| **sevenzip_fm_benchmark** | CPU | simulation script: 7zFM, Alt+T, B, read the Benchmark window | yes | `scripts/verify_simulation.py`: 170.013 and 169.841 GIPS, 10 passes, 54 s |
| **diskspd** / **fio** | Storage | DiskSpd 2.3 / fio 3.43, SEQ1M Q8T1 and RND4K Q32T1 read/write | yes | `scripts/verify_storage.py`: DiskSpd 6,735 MiB/s seq read; fio 6,687 MiB/s |
| **llama_benchy** | AI | llama-benchy 0.4.0 against LM Studio's local server | yes | `scripts/verify_llama_benchy.py`: gemma-4-12b-qat tg 91.6 tok/s (c1 d0), gpt2 tokenizer fallback |
| **mlperf_client** | AI | MLPerf Client 2.0.1, phi4mini llama.cpp CUDA | yes | `scripts/verify_mlperf_client.py`: 219.9 tok/s, Results Verified false |
| **cyberpunk2077_builtin_benchmark** | Game | simulation script: Steam launch, user starts the benchmark, result file read | **no** | not run: starting the game unattended was not allowed; result file layout assumed |
| **selftest_python** | test | Python one-liner | yes (tests) | unavailable in the packaged app (needs Python) |

Game frame capture (PresentMon, Frame capture tab) and forced settings are described in README.md.

---

## What Was Verified on This Machine

1. **Geekbench Dynamic Major Detection & Generality**:
   - Dynamic regex matching `^Geekbench (\d+)$` discovers any major version (e.g. 6, 7, 8) dynamically without hardcoded entry names.
   - Merged discovery methods:
     - Registry Uninstall keys (HKLM WOW6432Node: `Geekbench 6`, `Geekbench 7`)
     - Filesystem pattern matching in `C:\Program Files (x86)` and `C:\Program Files`
     - Start Menu shortcuts in `%APPDATA%\Microsoft\Windows\Start Menu\Programs`
     - `Get-StartApps` known folder GUID AppIDs
   - Version metadata read statically from PE resources via `pefile` (normalizing `"6,7,1,0"` -> `"6.7.1.0"` and `"7,1,0,0"` -> `"7.1.0.0"`).
   - Executable architecture determined directly from PE header Machine field (`0x8664` -> `x64`), not from folder names.
   - Identified the main GUI executables (`Geekbench 6.exe` and `Geekbench 7.exe`).
   - Binaries are NEVER executed to read versions or help.
   - Excludes other Primate Labs products such as "Geekbench AI" or "Geekbench ML".
   - Flags any major version not in `KNOWN_GEEKBENCH_MAJORS` (`{4, 5, 6, 7}`) as `"newly seen version: score comparability with other majors unknown"`.
   - Blocks score comparisons across different majors by default (`can_compare_geekbench_scores()`, `assert_can_compare_benchmarks()`).
   - If multiple installs of the same major exist, the highest version is reported as primary and secondary installs are listed in `additional_installs`.

2. **Cinebench Store Package Detection**:
   - Detected via `Get-AppxPackage` package metadata (`MAXONComputerGmbH.Cinebench`).
   - Read Version `2026.1.3.0`, Architecture `X64`, PackageFamilyName `MAXONComputerGmbH.Cinebench_rsne5bsk8s7tj`.
   - Start Menu AppID resolved via `Get-StartApps`: `MAXONComputerGmbH.Cinebench_rsne5bsk8s7tj!Cinebench`.
   - Protected folder inside `WindowsApps` is never inspected or accessed. Executable path is `None`.

3. **Database Additive Migration**:
   - Existing databases (`test_benchmark.db`, `benchmark_manager.db`) opened and read historical sessions and results without data loss.
   - Self-migrating `ALTER TABLE results ADD COLUMN benchmark_version TEXT` added missing column to old databases automatically.
   - Unit tests verify opening legacy schemas created on the fly.

4. **UI Architecture & Library Consolidation**:
   - `BenchmarkWorker` moved into its own class inheriting `QThread` with Qt signals.
   - Background scan invokes `scan()` without blocking UI.
   - UI manages "Scanning..." state, disables Rescan button during scan, and refreshes Library and Run screens upon completion.
   - Library screen consolidates all detected Geekbench majors into a single "Geekbench" card/row displaying each major's status, version, architecture, and executable path.
   - Run screen lists runnable benchmarks with separate launch buttons.
   - Assisted launch requires explicit user confirmation before any execution.

---

## What Could Not Be Verified on This Machine

1. **Geekbench Automation / CLI Support**:
   - Geekbench CLI automation requires a Pro license. The license state on this machine is unknown ("License: not verified"). Therefore, Geekbench 6 and 7 are classified as `READY_MANUAL` (assisted/manual).
2. **Cinebench Automation Mechanism**:
   - The Microsoft Store edition does not expose a documented headless automation API on this machine. Classified as `READY_MANUAL` ("launch it from the Start menu"). Assisted launch triggers `shell:AppsFolder\<AppID>` only after user confirmation.
3. **3DMark and glmark2 Installation**:
   - Genuinely not installed on this system. Classified as `NOT_INSTALLED`.
4. **Geekbench Launch Elevation & UAC Flow**:
   - Geekbench 6 and 7 executables request administrator rights in their manifest. Launching via `subprocess.Popen` fails with WinError 740. The app now retries via `os.startfile(path, "runas")` which triggers a Windows UAC prompt. The app itself remains non-elevated.
   - **Elevation is detected at launch time** (WinError 740 from a normal launch attempt), not hardcoded per application. The same generic mechanism (`_launch_with_elevation_fallback`) works for any benchmark that needs elevation.
   - The result dict includes `elevation_used: True/False` as an observed property.
   - If the user declines the UAC prompt, the status is reported as `cancelled` (neutral, not an error).
   - **UI automation of an elevated window from a non-elevated process is expected to be blocked** (not verified yet). Future automation phases must account for this: a non-elevated process cannot send input to or inspect an elevated window.
   - **Manual verification required**: The UAC prompt runs on the Windows secure desktop and cannot be interacted with automatically. To verify: click "Launch" for Geekbench 6 or 7 in the app, then click "Yes" on the UAC prompt, and confirm the benchmark window appears. Report back if the UAC prompt does not appear or the launch fails.
5. **Elevation Mechanism (ShellExecute / startfile with "runas")**:
   - Verified that `os.startfile(path, "runas")` works on this machine for harmless executables (notepad.exe). The actual Geekbench UAC flow must be verified manually (see above). No system settings, registry keys, or manifests were changed. The application is not elevated as a whole. Only the launched benchmark executable runs elevated when needed.`, 