# Linux test guide (real PC)

Everything Linux in the app was built and unit-tested on Windows with fakes; none of it has run on a real Linux PC
yet. This page is the one walk-through for that first real run: setup, then quick checks, then tools, then games.
Tick each line and note what happened; when something fails, the "Send" line says which files help.

Best machine for a full pass: x64, an NVIDIA or AMD GPU, Steam with Proton games installed, Ubuntu/Debian, Fedora,
Arch or openSUSE. One pass on an X11 session and one on a Wayland session (KDE Plasma or GNOME) covers both desktops.
An ARM64 machine is worth a second, shorter pass (part 2 only: most tools have no ARM build).

## 0. Before you start

- Note: distribution and version (`cat /etc/os-release`), desktop and session (`echo $XDG_SESSION_TYPE`),
  CPU architecture (`uname -m`), GPU and driver.
- Steam installed from the distribution or Valve's package. **The Flatpak Steam is not supported yet** (the run says
  so); Snap Steam: not checked.
- At the PC itself (not through a remote desktop): frame capture marks remote runs invalid.

## 1. Setup (about 10 min)

```bash
git clone https://github.com/detailstech93/pc-benchmark-manager.git
cd pc-benchmark-manager
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
.venv/bin/python scripts/main.py
```

- [ ] 1.1 Tests: all pass (Windows result for comparison: 1179 passed, 1 skipped). Send: the failures' last lines.
- [ ] 1.2 The window opens. If Qt says "could not load the Qt platform plugin xcb", install `libxcb-cursor0`
      (Debian/Ubuntu) or `xcb-util-cursor` (Fedora/Arch) and start again.
- [ ] 1.3 **This PC** screen: processor, memory, graphics, OS look right; "not certain" / "not available" where the
      app could not read a value. Send: a screenshot of anything wrong.

## 2. Quick tests (about 15 min)

- [ ] 2.1 Tests screen: 7-Zip and fio show **Install** if missing. Press it: the dialog shows the package manager
      command (apt-get / dnf / pacman / zypper), then the password prompt (pkexec). After it: Installed.
      Package names are not checked on every distribution: if the install fails, send the dialog's text.
- [ ] 2.2 Run 7-Zip benchmark 3 times. The run dialog: "PC right now" block (power profile / CPU governor, CPU in use,
      start temperature if psutil reads one). Score stored, report opens.
- [ ] 2.3 Run fio (disk). Score stored.
- [ ] 2.4 Saved plans: "Run a saved plan..." -> last.json: same tests and numbers come back.

## 3. Pro benchmarks

- [ ] 3.1 **Blender Benchmark** (x64 only): Install (launcher + Blender + scenes from Blender's site), then CPU and GPU.
      Own report section. Send: the run folder's output if a score is missing.
- [ ] 3.2 **V-Ray Benchmark** (x64 only): Install, accept the license in the app, run CPU (and CUDA / RTX on NVIDIA).
- [ ] 3.3 **PassMark** (x64 and ARM64; CPU Mark and Memory Mark only): Install (zip from passmark.com, then ncurses 5
      by password prompt), run CPU Mark, then Memory Mark. On Ubuntu 24.04+ libncurses5 has no package: note what
      the app says. Send if "invalid": results_cpu.yml / results_memory.yml and passmark_stdout.txt.
- [ ] 3.4 **y-cruncher** (x64 only): Install (static tar.xz from numberworld.org), accept its license in the app, run
      Pi 1 billion digits. Send if "invalid": ycruncher_log.txt and stdout_ycruncher.txt.
- [ ] 3.5 **HandBrake** (x64 and ARM64): Install (the Big Buck Bunny 1080p clip from download.blender.org, then
      handbrake-cli by password prompt; Debian/Ubuntu and Arch only), run H.264 then H.265. Score in fps; the
      encoded file is deleted after. Send if "invalid": stderr_handbrake.txt.
- [ ] 3.6 **Fixed AI models** (LM Studio for Linux installed and started once): Tests screen -> llama-benchy:
      Qwen3.5 4B -> Install (lms get), Run. The app loads its own copy and unloads it after. Send if it fails:
      lms_load.txt and stderr.txt. (MLPerf Client is Windows only.)
- [ ] 3.7 **Remote mode**: Tests screen -> Remote: OFF... -> switch on; from a phone on the same network open the
      address shown, type the code, start 7-Zip, watch it, open the report. If the page does not open, check the
      distribution's firewall (ufw/firewalld) for port 8737; the app changes nothing there.
- Not on Linux: Cinebench (no Linux build), PassMark 2D/3D, PugetBench, 7-Zip's window test.

## 4. Games with MangoHud (Steam, Proton)

Frame capture screen -> **Install MangoHud** if missing (password prompt). The app starts Steam itself with MangoHud
(asks once); Steam may already be open (the app asks it to exit and starts it again).

- [ ] 4.1 **Cyberpunk 2077** (its own benchmark by launch option): Benchmark game. Stored FPS; the run folder has
      MangoHud.conf and mangohud_logs/*.csv. Send if "MangoHud wrote no frame log": capture_log.txt and the
      mangohud_logs folder listing.
- [ ] 4.2 Two games in a row: the second must not restart Steam.
- [ ] 4.3 **Forced settings in the Proton prefix**: start Cyberpunk once (creates its prefix), Game settings... ->
      Ray tracing Off -> Benchmark. The question names compatdata/1091500/pfx; after the run the game's own settings
      are back. Rise of the Tomb Raider: settings are "declared", not forced (registry game).
- [ ] 4.4 **Earlier runs**: run Cyberpunk again with the same settings: the report compares; change DLSS / RT in the
      game and run again: "not comparable" naming the setting.

## 5. Menu steps, keys and routes (X11 session first)

Game in **borderless window or windowed**. Do not touch mouse or keyboard while it runs.

- [ ] 5.1 **Shadow of the Tomb Raider** (menu steps): Benchmark. First time: Install Tesseract (password prompt).
      Watch: Options -> Graphics -> benchmark by itself; the runs count from the benchmark start.
      Send if it stops: step_N_not_found.png and screen_log.txt.
- [ ] 5.2 While the menus run, switch to another window for a moment: nothing must be typed there.
- [ ] 5.3 **A route game** (Witcher 3 or DOOM, data/game_routes.py): load the save named in the run text, Benchmark.
      The character walks and the **camera turns** (if it does not turn, the game ignores XTEST mouse moves: tell me).
      Switch windows a moment: the character stops walking.

## 6. Same on Wayland (KDE Plasma or GNOME)

Log out, pick the Wayland session, then:

- [ ] 6.1 Repeat 5.1: the run text says "XWayland". If the desktop asks to allow remote input / remote control,
      allow it (and note the exact wording). The step screenshots are the game's window only.
- [ ] 6.2 Repeat 5.2 and 5.3.
- [ ] 6.3 Note if the game was started with `PROTON_ENABLE_WAYLAND=1`: then no key is sent (expected).

## 7. Send back

For each line: OK, or what happened plus the files named above. If a screen showed an error, also the last lines of
the terminal the app was started from. Nothing on Windows changes from these tests.
