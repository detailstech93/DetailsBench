"""How a missing test is installed with one click on the Tests screen (app/install), per OS and architecture.

    INSTALLERS = {test id: {"windows": {"winget": package id, "hosts": [...], "arch": [...]},
                            "linux": {"packages": {distro family: package name}}}}

{"arch": [...], "zip": {...}} under an OS: the vendor's own zip or tar.gz, its URL, size and SHA-256 pinned
(app/install/direct.py); optional "then" names what the same button downloads next (app/ui/install_flow.py).

Windows: winget (Microsoft's Windows Package Manager). `winget show` gives the installer URL and its SHA256; the app
shows them and asks, refuses a URL whose host is not the vendor's ("hosts", optional "path_prefix"), and winget
checks the SHA256 before it runs the installer. "arch" lists the machine architectures winget has an installer
for, checked with `winget show --architecture <arch>` on 2026-10-18 (fio has no arm64 installer; PassMark none
for x86). Another architecture gets the official download page instead.

Linux: the distribution's own package manager through pkexec (the system asks for the password), the way MangoHud
is installed (app/frames/linux_install.py). Families: debian (apt-get), fedora (dnf), arch (pacman), suse
(zypper). Package names are the distributions' usual ones, NOT checked on a real Linux machine yet: the dialog
says so. A family missing from "packages" gets the official page.

{"steam": app id} under an OS installs through Steam (3DMark). Steam games are not listed here: a missing Steam game opens Steam's own install dialog (steam://install/<appid>)
on Windows and Linux. A test with no entry for this OS keeps its "Official download page" button.
"""

from typing import Dict

INSTALLERS: Dict[str, dict] = {
    "sevenzip_benchmark": {
        # 7-Zip 26.04: https://www.7-zip.org/a/7z2604-x64.msi (arm64: 7z2604-arm64.exe)
        "windows": {"winget": "7zip.7zip", "hosts": ["www.7-zip.org", "7-zip.org"], "arch": ["x64", "arm64", "x86"]},
        # the tool spec runs 7z or 7za: p7zip-full gives 7z (Debian/Ubuntu), p7zip gives 7za (Fedora)
        "linux": {"packages": {"debian": "p7zip-full", "fedora": "p7zip", "arch": "7zip", "suse": "7zip"}},
    },
    "sevenzip_fm_benchmark": {          # 7-Zip's window (7zFM.exe): Windows only
        "windows": {"winget": "7zip.7zip", "hosts": ["www.7-zip.org", "7-zip.org"], "arch": ["x64", "arm64", "x86"]},
    },
    "diskspd": {
        # DiskSpd 2.3 (Microsoft): one zip with amd64, x86 and arm64 builds
        "windows": {"winget": "Microsoft.DiskSpd", "hosts": ["github.com"], "path_prefix": "/microsoft/diskspd/",
                    "arch": ["x64", "arm64", "x86"]},
    },
    "fio": {
        # fio 3.43: https://github.com/axboe/fio/releases/download/fio-3.43/fio-3.43-x64.msi (x64 only)
        "windows": {"winget": "fio.fio", "hosts": ["github.com"], "path_prefix": "/axboe/fio/", "arch": ["x64"]},
        "linux": {"packages": {"debian": "fio", "fedora": "fio", "arch": "fio", "suse": "fio"}},
    },
    "passmark_performancetest": {
        # PerformanceTest 11.1: PerformanceTest_Windows_x86-64.exe / PerformanceTest_Windows_ARM.exe
        "windows": {"winget": "PassMark.PerformanceTest", "hosts": ["www.passmark.com"], "arch": ["x64", "arm64"]},
    },
    "blender_benchmark": {
        # Blender Benchmark Launcher CLI 3.3.0: no winget package; the zip from Blender's download server, size and
        # SHA-256 pinned on 2026-10-18 (benchmark-launcher-cli.exe inside is signed "Blender Foundation", checked
        # with Get-AuthenticodeSignature). Builds exist for Windows x64 and Linux x64 only (the download folder,
        # 2026-10-18; the Linux binary is an x86-64 ELF). app/install/direct.py installs it, then the same button
        # downloads Blender and the scenes with the launcher (app/pro/blender_benchmark.py, "then").
        "windows": {"arch": ["x64"], "zip": {
            "product": "Blender Benchmark Launcher CLI", "version": "3.3.0",
            "url": "https://download.blender.org/release/BlenderBenchmark2.0/launcher/"
                   "benchmark-launcher-cli-3.3.0-windows.zip",
            "hosts": ["download.blender.org"], "size": 7755878,
            "sha256": "3f00bb68915800f14ddad6877083544aa25c56aac252b77b161bf7962ee67dde",
            "folder": "benchmark-launcher-cli-3.3.0", "exe": "benchmark-launcher-cli.exe",
            "then": "blender_content"}},
        "linux": {"arch": ["x64"], "zip": {
            "product": "Blender Benchmark Launcher CLI", "version": "3.3.0",
            "url": "https://download.blender.org/release/BlenderBenchmark2.0/launcher/"
                   "benchmark-launcher-cli-3.3.0-linux.tar.gz",
            "hosts": ["download.blender.org"], "size": 7803498,
            "sha256": "a15e062179e7bf5cd20d58081f3c84cf87d56df3f596ffb0a11383638139324a",
            "folder": "benchmark-launcher-cli-3.3.0", "exe": "benchmark-launcher-cli",
            "then": "blender_content"}},
    },
    "vray_benchmark": {
        # V-Ray 6 Benchmark 6.00.02 (Chaos, 2025-07-02): one program file, no login needed, from Chaos's download
        # page (chaos.com/benchmark-download). Only the "latest" link works without an account (a link to one
        # version asks to sign in), so a newer release is refused by its SHA-256 until it is pinned here. Size
        # and SHA-256 read on 2026-10-18; the Windows file is signed "CHAOS SOFTWARE EOOD" (checked with
        # Get-AuthenticodeSignature), the Linux one is an x86-64 ELF. No ARM build.
        "windows": {"arch": ["x64"], "zip": {
            "product": "V-Ray 6 Benchmark (command line)", "version": "6.00.02",
            "url": "https://download.chaos.com/api/v3/builds/latest/download?product=V-Ray+Benchmark"
                   "&build-type=official&tags=windows-x64-cli",
            "hosts": ["download.chaos.com"], "size": 653056928,
            "sha256": "5b4b1d14a315682f6c5a1c3689a9fc372f06d56dd8e5ad06b4807d1d6ca6468f",
            "folder": "vray-benchmark-6.00.02", "exe": "vray-benchmark-6.00.02-cli.exe", "single": True,
            "note": "Before its first run the app shows Chaos's license agreement and asks you to accept it."}},
        "linux": {"arch": ["x64"], "zip": {
            "product": "V-Ray 6 Benchmark", "version": "6.00.02",
            "url": "https://download.chaos.com/api/v3/builds/latest/download?product=V-Ray+Benchmark"
                   "&build-type=official&tags=linux-x64",
            "hosts": ["download.chaos.com"], "size": 755338045,
            "sha256": "27a3bd029b9af851676740fb607869845ea4ca69e6dcb3e475accd634a95df6d",
            "folder": "vray-benchmark-6.00.02", "exe": "vray-benchmark-6.00.02", "single": True,
            "note": "Before its first run the app shows Chaos's license agreement and asks you to accept it."}},
    },
    "cinebench_2026": {
        # Cinebench 2026 (Maxon, free): the zips linked from maxon.net/en/downloads/cinebench-downloads, read on
        # 2026-10-19. Maxon publishes no SHA-256; its download server sends Content-MD5, pinned here with the size
        # (a newer upload under the same address is refused until it is pinned again). Cinebench.exe is at the
        # zip's root (the zip's own listing, read with range requests: x64 29,410 files / 4.1 GB unpacked,
        # ARM64 29,358 files / 1.8 GB). Windows x64 and ARM64 only: Maxon has no Linux build (macOS is a dmg).
        "windows": {"arch": ["x64", "arm64"], "zip_by_arch": {
            "x64": {
                "product": "Cinebench 2026 (Windows x64)", "version": "2026",
                "url": "https://mx-app-blob-prod.maxon.net/mx-package-production/website/windows/maxon/cinebench/"
                       "Cinebench2026_win_x86_64.zip",
                "hosts": ["mx-app-blob-prod.maxon.net"], "size": 2697130801,
                "md5": "593594ea7dc8385db9772daeb99ab1ad",
                "folder": "cinebench-2026-x64", "exe": "Cinebench.exe",
                "max_files": 30000, "max_unpacked": 4500 * 1024 ** 2,
                "note": "About 4.1 GB once extracted. Cinebench may show Maxon's license agreement on its first "
                        "run; accept it there once."},
            "arm64": {
                "product": "Cinebench 2026 (Windows ARM64)", "version": "2026",
                "url": "https://mx-app-blob-prod.maxon.net/mx-package-production/website/windows/maxon/cinebench/"
                       "Cinebench2026_win_arm64.zip",
                "hosts": ["mx-app-blob-prod.maxon.net"], "size": 783373346,
                "md5": "fa53a16db96f17b03d023b44e80ebe28",
                "folder": "cinebench-2026-arm64", "exe": "Cinebench.exe",
                "max_files": 30000, "max_unpacked": 2000 * 1024 ** 2,
                "note": "About 1.8 GB once extracted. Cinebench may show Maxon's license agreement on its first "
                        "run; accept it there once."},
        }},
    },
    "ycruncher_pi_1b": {
        # y-cruncher 0.8.7 (Alexander J. Yee, free for non-commercial use and technology reviews): the files linked
        # from numberworld.org/y-cruncher on its CDN, read on 2026-10-19. Size and SHA-256 equal the ones GitHub
        # lists for the same files in the author's release (github.com/Mysticial/y-cruncher), and were checked on a
        # download here. Windows: the zip of 0.8.7.9547b (the "b" build is Windows only); Linux: the static build
        # (works on most distributions). x64 only: no ARM build.
        "windows": {"arch": ["x64"], "zip": {
            "product": "y-cruncher (Windows x64)", "version": "0.8.7.9547b",
            "url": "https://cdn.numberworld.org/y-cruncher-downloads/y-cruncher%20v0.8.7.9547b.zip",
            "hosts": ["cdn.numberworld.org"], "size": 47198240,
            "sha256": "3be696c1cc44907f2ea73aac35de4a5d45048255308f34ed4410a743163a4f87",
            "folder": "y-cruncher-0.8.7.9547b", "exe": "y-cruncher v0.8.7.9547b/y-cruncher.exe",
            "note": "About 160 MB once extracted. Before its first run the app shows y-cruncher's license and asks "
                    "you to accept it. It needs Microsoft's Visual C++ runtime (most PCs have it)."}},
        "linux": {"arch": ["x64"], "zip": {
            "product": "y-cruncher (Linux x64, static)", "version": "0.8.7.9547",
            "url": "https://cdn.numberworld.org/y-cruncher-downloads/y-cruncher%20v0.8.7.9547-static.tar.xz",
            "hosts": ["cdn.numberworld.org"], "size": 35954568,
            "sha256": "e22ff344f42763520d2331f19ed6177f3a7c78ac5bd462e9c060c38267181490",
            "folder": "y-cruncher-0.8.7.9547-static", "exe": "y-cruncher v0.8.7.9547-static/y-cruncher",
            "note": "About 175 MB once extracted. Before its first run the app shows y-cruncher's license and asks "
                    "you to accept it."}},
    },
    "handbrake_x264_1080p": {
        # HandBrake's test clip (Blender's own server, read on 2026-10-19: Blender publishes no checksum, so size
        # and SHA-256 come from a download here); the Install button then gets HandBrakeCLI (HANDBRAKE_CLI below)
        "windows": {"arch": ["x64", "arm64"], "zip": {
            "product": "Big Buck Bunny 1080p 30 fps, test clip for HandBrake (Blender Foundation, CC BY 3.0)",
            "version": "2013", "url": "https://download.blender.org/demo/movies/BBB/bbb_sunflower_1080p_30fps_normal.mp4.zip",
            "hosts": ["download.blender.org"], "size": 275524128,
            "sha256": "e320fef389ec749117d0c1583945039266a40f25483881c2ff0d33207e62b362",
            "folder": "bbb-sunflower-1080p30", "exe": "bbb_sunflower_1080p_30fps_normal.mp4", "then": "handbrake_cli",
            "note": "The clip is 263 MB once extracted; it is only read (encoded), never changed."}},
        "linux": {"arch": ["x64", "arm64"], "zip": {
            "product": "Big Buck Bunny 1080p 30 fps, test clip for HandBrake (Blender Foundation, CC BY 3.0)",
            "version": "2013", "url": "https://download.blender.org/demo/movies/BBB/bbb_sunflower_1080p_30fps_normal.mp4.zip",
            "hosts": ["download.blender.org"], "size": 275524128,
            "sha256": "e320fef389ec749117d0c1583945039266a40f25483881c2ff0d33207e62b362",
            "folder": "bbb-sunflower-1080p30", "exe": "bbb_sunflower_1080p_30fps_normal.mp4", "then": "handbrake_cli",
            "note": "The clip is 263 MB once extracted; it is only read (encoded), never changed."}},
    },
    # 3DMark is on Steam; Windows only. The free "3DMark Demo" (231350): the paid 3DMark (223850, EUR 24.99) gave
    # the owner an error in Steam when it is not owned (2026-10-20). Steam store API, 2026-10-20: 231350 is a
    # free demo of 223850.
    "d3dmark": {"windows": {"steam": 231350}},
    # Geekbench 6.4.0 (owner, 2026-10-20: bring it back): winget's PrimateLabs.Geekbench.6 installs Primate Labs' own
    # https://cdn.geekbench.com/Geekbench-6.4.0-WindowsSetup.exe (SHA-256 4e46a831...67654, winget show 2026-10-20);
    # winget has no ARM64 installer for it
    "geekbench_6": {"windows": {"winget": "PrimateLabs.Geekbench.6", "hosts": ["cdn.geekbench.com"], "arch": ["x64"]}},
}

# the GPU run of Blender Benchmark uses the same launcher
INSTALLERS["blender_benchmark_gpu"] = INSTALLERS["blender_benchmark"]
# the V-Ray GPU runs use the same program
INSTALLERS["vray_benchmark_gpu"] = INSTALLERS["vray_benchmark_rtx"] = INSTALLERS["vray_benchmark"]
# the single-thread run of Cinebench 2026 uses the same program
INSTALLERS["cinebench_2026_single"] = INSTALLERS["cinebench_2026"]
# the automated Geekbench 6 tests use the same program
for _id in ("geekbench6_cpu", "geekbench6_gpu_opencl", "geekbench6_gpu_vulkan"):
    INSTALLERS[_id] = INSTALLERS["geekbench_6"]
# Geekbench on Linux: Primate Labs ships a tar.gz (geekbench.com/download/linux links geekbench-cdn.b-cdn.net; 6.7.1
# from /download/legacy), read on 2026-10-20. No checksum is published: size and SHA-256 pinned from the downloads
# here (owner said yes). Each holds Geekbench-<version>-Linux/geekbench<major> (x86-64). The ARM preview builds are
# not pinned, so ARM Linux is not offered.
_GEEKBENCH_LINUX = {
    6: {"product": "Geekbench 6 for Linux (x86-64)", "version": "6.7.1",
        "url": "https://geekbench-cdn.b-cdn.net/Geekbench-6.7.1-Linux.tar.gz",
        "hosts": ["geekbench-cdn.b-cdn.net"], "size": 228093838,
        "sha256": "0ddca977deb6d9db4bd866485f9408e72e2869d0dea0737b18d4bfe472858ace",
        "folder": "geekbench-6.7.1-linux", "exe": "Geekbench-6.7.1-Linux/geekbench6",
        "max_unpacked": 600 * 1024 ** 2},           # 5 files, 500,109,413 bytes unpacked (its tar's listing)
    7: {"product": "Geekbench 7 for Linux (x86-64)", "version": "7.1.0",
        "url": "https://geekbench-cdn.b-cdn.net/Geekbench-7.1.0-Linux.tar.gz",
        "hosts": ["geekbench-cdn.b-cdn.net"], "size": 484515924,
        "sha256": "411b72a9c851b69e6f91d390649d419e2e625f475e3983880169651ca27a8672",
        "folder": "geekbench-7.1.0-linux", "exe": "Geekbench-7.1.0-Linux/geekbench7",
        "max_unpacked": 1100 * 1024 ** 2},          # 6 files, 1,009,518,416 bytes unpacked (owner hit the 500 MB limit)
}
INSTALLERS["geekbench_6"]["linux"] = {"zip": _GEEKBENCH_LINUX[6], "arch": ["x64"]}    # the Geekbench 6 tests share it
INSTALLERS["geekbench_7"] = {"linux": {"zip": _GEEKBENCH_LINUX[7], "arch": ["x64"]}}
for _id in ("geekbench7_cpu", "geekbench7_gpu_opencl", "geekbench7_gpu_vulkan", "geekbench7_gpu_cuda"):
    INSTALLERS[_id] = INSTALLERS["geekbench_7"]
# GravityMark 1.89 (Tellusim), x64, from tellusim.com/download as linked on gravitymark.tellusim.com, read on
# 2026-10-20. No checksum is published: size and SHA-256 pinned from the downloads here (owner asked for it). Its
# server refuses curl's default User-Agent. Windows: the .msi, unpacked as an administrative image (no
# administrator, nothing registered; app/install/direct.py), GravityMark\bin\GravityMark.exe from its Directory
# table. Linux: the makeself .run, its tar.gz unpacked without running the script (bin/GravityMark.x64). The ARM
# builds are not pinned. Its license is evaluation only (app/pro/gravitymark.py asks before the first run).
_GRAVITYMARK = {
    "windows": {"arch": ["x64"], "zip": {
        "product": "GravityMark (Windows x64)", "version": "1.89",
        "url": "https://tellusim.com/download/GravityMark_1.89.msi", "hosts": ["tellusim.com"], "size": 220901376,
        "sha256": "38e32b475cf93eca2085334f7421fc738fb605c5b37f35e90e06d73a54169c8f",
        "folder": "gravitymark-1.89", "exe": "GravityMark/bin/GravityMark.exe"}},
    "linux": {"arch": ["x64"], "zip": {
        "product": "GravityMark (Linux x64)", "version": "1.89",
        "url": "https://tellusim.com/download/GravityMark_1.89.run", "hosts": ["tellusim.com"], "size": 193327384,
        "sha256": "de090c7b9e40f10f225d9393f2cf73a660ad4edc21f6b333eea3190a8722e005",
        "folder": "gravitymark-1.89", "exe": "bin/GravityMark.x64"}},
}
for _id in ("gravitymark_vulkan", "gravitymark_opengl", "gravitymark_d3d12"):
    INSTALLERS[_id] = _GRAVITYMARK
# vkmark and glmark2 (Linux): the distribution's packages. Debian/Ubuntu names read with apt-cache on Ubuntu 24.04
# (2026-10-20): vkmark 2017.08+git20220909; glmark2 2023.01 is split by window system there, glmark2-x11 gives the
# "glmark2" program (it also runs on Wayland desktops through XWayland). Other families are not offered.
# the web tests on Linux: Google Chrome's current stable .deb from dl.google.com, its size and SHA-256 read from
# Google's own apt index at install time (app/install/chrome_linux.py), installed by apt. Debian/Ubuntu x64 only.
INSTALLERS["speedometer"] = INSTALLERS["jetstream"] = {"linux": {"chrome_deb": True, "arch": ["x64"]}}
INSTALLERS["vkmark"] = {"linux": {"packages": {"debian": "vkmark"}}}
INSTALLERS["glmark2"] = {"linux": {"packages": {"debian": "glmark2-x11"}}}
INSTALLERS["ycruncher_pi_2_5b"] = INSTALLERS["ycruncher_pi_1b"]
INSTALLERS["handbrake_x265_1080p"] = INSTALLERS["handbrake_x264_1080p"]

# HandBrakeCLI 1.11.2 (2026-06-07), installed after the clip (app/pro/handbrake.py). Windows: the zips of HandBrake's
# own GitHub release (handbrake.fr links there); size and SHA-256 as GitHub lists them, equal on a download here
# (2026-10-19); HandBrakeCLI.exe at the zip's root. Linux: HandBrake ships only the GUI as a Flatpak, so the CLI
# is the distribution's package: Debian/Ubuntu "handbrake-cli" (universe), Arch "handbrake-cli" (extra); Fedora and
# openSUSE have it only in RPM Fusion / Packman, which the app does not add. Names not checked on a real system.
HANDBRAKE_CLI = {
    "windows": {
        "x64": {"product": "HandBrakeCLI (Windows x64)", "version": "1.11.2",
                "url": "https://github.com/HandBrake/HandBrake/releases/download/1.11.2/"
                       "HandBrakeCLI-1.11.2-win-x86_64.zip",
                "hosts": ["github.com"], "size": 25721458,
                "sha256": "80bfe8d5f5d11cc3ef76b834add3ed4e82dee6523ffeb435c283f88b1a21f09d",
                "folder": "handbrakecli-1.11.2-x64", "exe": "HandBrakeCLI.exe"},
        "arm64": {"product": "HandBrakeCLI (Windows ARM64)", "version": "1.11.2",
                  "url": "https://github.com/HandBrake/HandBrake/releases/download/1.11.2/"
                         "HandBrakeCLI-1.11.2-win-aarch64.zip",
                  "hosts": ["github.com"], "size": 19316730,
                  "sha256": "708ac040bf1ca41d46dbe5f355b588863d60366a67072a974e6829c314ac060d",
                  "folder": "handbrakecli-1.11.2-arm64", "exe": "HandBrakeCLI.exe"},
    },
    "linux": {"debian": "handbrake-cli", "arch": "handbrake-cli"},
}
# PassMark's automatic tests (app/pro/passmark.py) use the same program. On Linux, PassMark's own command-line
# program (CPU and Memory tests only): the zips linked from passmark.com/products/pt_linux/download.php, 11.0
# Build 1004 (2025-11-12), read on 2026-10-19. PassMark publishes no checksum: size and SHA-256 pinned from the
# downloaded zips (a newer upload under the same address is refused until it is pinned again). Each zip holds
# PerformanceTest/PerformanceTest_Linux_<arch> (an ELF, x86-64 / AArch64) and readme.txt. Its server refuses
# curl's default User-Agent but answers the app's. It needs ncurses 5 ("then": installed after the zip).
# ARM 32-bit has a zip too; the app does not tell ARM 32-bit machines apart, so it is not offered.
_PASSMARK_LINUX = {"arch": ["x64", "arm64"], "zip_by_arch": {
    "x64": {
        "product": "PassMark PerformanceTest Linux (x86-64)", "version": "11.0.1004",
        "url": "https://www.passmark.com/downloads/PerformanceTest_Linux_x86-64.zip",
        "hosts": ["www.passmark.com"], "size": 5646190,
        "sha256": "dab991f957373a5cfaa1e1b212a882bbb4d0fb85abaa22fc7694e8bd9dc070ad",
        "folder": "passmark-performancetest-linux-11.0.1004",
        "exe": "PerformanceTest/PerformanceTest_Linux_x86-64", "then": "ncurses5"},
    "arm64": {
        "product": "PassMark PerformanceTest Linux (ARM64)", "version": "11.0.1004",
        "url": "https://www.passmark.com/downloads/PerformanceTest_Linux_ARM64.zip",
        "hosts": ["www.passmark.com"], "size": 2588994,
        "sha256": "968d247170df1a060e798ce0e9c033c83c8e018fafbd2930d4db666c7d435021",
        "folder": "passmark-performancetest-linux-11.0.1004",
        "exe": "PerformanceTest/PerformanceTest_Linux_ARM64", "then": "ncurses5"},
}}
for _id in ("passmark_cpu", "passmark_memory"):
    INSTALLERS[_id] = {**INSTALLERS["passmark_performancetest"], "linux": _PASSMARK_LINUX}
for _id in ("passmark_2d", "passmark_3d"):            # graphics tests: Windows only
    INSTALLERS[_id] = INSTALLERS["passmark_performancetest"]
