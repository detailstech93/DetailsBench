# Pro benchmarks: what can be automated (research, 2026-10-18)

The owner wants these after the games and the full tool test. Nothing below has been run on a real machine.
Every command is from the vendor's docs or forums and stays `verified: false` until it is checked with raw output.
We never run a tool with `--help` or guessed arguments to find out.

## On the owner's PC (read-only check of the program folders, 2026-10-18)
| Tool | Found | Notes |
|---|---|---|
| Adobe Photoshop 2026, Premiere Pro 2025 | yes | Needed by PugetBench. |
| PugetBench for Creators | no | |
| V-Ray Benchmark | no | `Program Files\Chaos\V-Ray` is V-Ray for 3ds Max 2024, not the benchmark. |
| Blender Benchmark Launcher | no | |
| PassMark PerformanceTest | no | |

## Per tool

### Blender Benchmark (Blender Open Data): best candidate for full automation
- Free. Has an official command-line launcher, `benchmark-launcher-cli`.
- Documented steps, all from the launcher's README and its forum thread:
  1. Download a Blender version: `blender download <version>`.
  2. List and download the scenes: `scenes --blender-version <v> list` and `scenes download --blender-version <v> <scene>`.
  3. Run: `benchmark <scene> --blender-version <v> --device-type CPU|CUDA|OPTIX|HIP|ONEAPI|METAL --json`.
- The result is samples per minute for each scene (`monster`, `junkshop`, `classroom`); higher is better.
- `--submit` uploads to Open Data. We **never** pass it unless the owner opts in.
- Plan:
  - a multi-step workload (download once into the app's tools folder, then run);
  - parse the JSON (`stats.samples_per_minute` is the expected field; confirm from raw output);
  - one run per scene, for CPU and for the GPU (OPTIX on the RTX 5090).
- Known issue: a launcher bug report says the CLI works in batch mode only after it was run once interactively. Check this on the first real run.
- Sources: [devtalk thread](https://devtalk.blender.org/t/blender-benchmark-launcher-cli-and-documentation/11225), [launcher CLI README](https://projects.blender.org/infrastructure/blender-open-data/src/tag/20240822-151453/launcher/etc/cli), [issue 73421](https://projects.blender.org/archive/blender-benchmark-bundle/issues/73421).

### V-Ray Benchmark (Chaos): can be automated, flags still to be confirmed
- Free, but needs a Chaos account to download. It has had a command-line interface since 1.0.7.
- The modes are CPU (`vray`), GPU CUDA and GPU RTX. Secondary sources show `--mode` and `--output <file.json>`.
- The EULA must be accepted. One source pipes `y` into the program; the owner must accept the EULA themself the first time.
- Plan: once installed, read the official CLI page (documentation.chaos.com) and write the spec from it. Scores: vsamples (CPU), vpaths (GPU).
- Sources: [Chaos benchmark page](https://www.chaos.com/vray/benchmark), [iRender guide](https://irendering.net/v-ray-benchmark-and-real-test-with-irender/).

### PassMark PerformanceTest: assisted unless the owner buys a license
- PassMark's FAQ says scripts and automation command-line switches need a **paid license**. The free version can only be run by hand.
- Plan: library entry, Manual (the owner runs it and types the score), as for Cinebench today. Automatic only with a license; then use PassMark's script with `/s` and its text export.
- Sources: [license features](https://passmark.com/support/performancetest_faq/license-features.php), [script forum post](https://forums.passmark.com/performancetest/5401-commandline-script-output-result).

### PugetBench for Photoshop / Premiere Pro: assisted unless licensed
- Runs inside the real Adobe apps (both are installed here).
- The command-line runner, local results and turning off uploads need a **paid PugetBench license**. The free version uploads results and runs from its GUI.
- Plan: Manual / assisted without a license. With one, use the app's "Copy CLI" command, which gives the exact arguments for this version.
- Sources: [user guide](https://www.pugetsystems.com/pugetbench/creators/user-guide/), [2025 release notes](https://www.pugetsystems.com/blog/2025/12/03/puget-bench-for-creators-fall-feature-pass-released/).

## Order proposed
1. Blender (free, CLI, CPU + GPU).
2. V-Ray (free, CLI; needs the owner's Chaos account).
3. PassMark and PugetBench as manual entries now; automatic if the owner gets licenses.

All four also fit the planned one-click install (official source, size shown, owner asked first).
